"""Stdlib-only tests. Never load or execute native candidate artifacts."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('v', Path(__file__).resolve().parents[1] / 'validate_jammy_candidate.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_tree_detects_extra_file(self):
        before = v.tree(self.root); (self.root / 'extra.cpp').write_text('x')
        self.assertNotEqual(before, v.tree(self.root))
    def test_dependency_hidden_members_not_excluded(self):
        (self.root / 'runtime').mkdir(); (self.root / 'runtime/x').write_text('x')
        self.assertIn('runtime/x', v.tree(self.root, excludes=set()))
    def test_tree_rejects_external_symlink(self):
        (self.root / 'escape').symlink_to('/etc/passwd')
        with self.assertRaises(RuntimeError): v.tree(self.root)
    def test_tree_records_symlink_target(self):
        (self.root / 'a').write_text('a'); (self.root / 'b').write_text('b')
        link = self.root / 'link'; link.symlink_to('a'); before = v.tree(self.root)
        link.unlink(); link.symlink_to('b'); self.assertNotEqual(before, v.tree(self.root))
    def wheel(self, names):
        p = self.root / 'test.whl'
        with zipfile.ZipFile(p, 'w') as z:
            for n in names: z.writestr(n, b'not executable')
        return p
    def test_wheel_traversal_rejected(self):
        with self.assertRaises(RuntimeError): v.wheel_files(self.wheel([v.EXTENSION, '../escape']))
    def test_wheel_unexpected_top_level_rejected(self):
        with self.assertRaises(RuntimeError): v.wheel_files(self.wheel([v.EXTENSION, 'sitecustomize.py']))
    def test_wheel_duplicate_rejected(self):
        with self.assertRaises(RuntimeError): v.wheel_files(self.wheel([v.EXTENSION, v.EXTENSION]))
    def test_wheel_directory_rejected(self):
        with self.assertRaises(RuntimeError): v.wheel_files(self.wheel([v.EXTENSION, 'fields2cover/']))
    def test_wheel_symlink_rejected(self):
        p = self.root / 'test.whl'
        with zipfile.ZipFile(p, 'w') as z:
            info = zipfile.ZipInfo(v.EXTENSION); info.external_attr = 0o120777 << 16; z.writestr(info, '/etc/passwd')
        with self.assertRaises(RuntimeError): v.wheel_files(p)
    def test_wheel_regular_layout(self):
        self.assertEqual(set(v.wheel_files(self.wheel([v.EXTENSION]))), {v.EXTENSION})
    def test_environment_drops_loader_and_python_overrides(self):
        with patch.dict(os.environ, {'LD_LIBRARY_PATH':'bad', 'LD_PRELOAD':'bad', 'PYTHONHOME':'bad', 'PYTEST_ADDOPTS':'bad'}):
            env = v.clean({'output':str(self.root),'tools':{'python':{'path':'/python/bin/python'},'node':{'path':'/node/bin/node'}}})
        for key in ['LD_LIBRARY_PATH','LD_PRELOAD','PYTHONHOME','PYTEST_ADDOPTS']: self.assertNotIn(key,env)
    def test_junit_exact_count_and_zero_skip(self):
        p = self.root / 'result.xml'; p.write_text('<testsuite><testcase/><testcase/><testcase/></testsuite>')
        v.assert_junit(p,3)
        with self.assertRaises(RuntimeError): v.assert_junit(p,602)
        p.write_text('<testsuite><testcase><skipped/></testcase></testsuite>')
        with self.assertRaises(RuntimeError): v.assert_junit(p,1)
    def test_elf_parser_versions_and_symbols(self):
        p = self.root / 'fake.so'; p.write_bytes(b'data')
        outputs = [' (NEEDED) Shared library: [libx.so]\n (RUNPATH) Library runpath: [$ORIGIN]',
            "Version definition section '.gnu.version_d':\n Name: OWN_1\nVersion needs section '.gnu.version_r':\n File: libx.so\n Name: DEP_1\n",
            ' 1: 000 0 FUNC GLOBAL DEFAULT UND required@DEP_1 (2)\n 2: 001 4 FUNC GLOBAL DEFAULT 1 own\n 3: 0 0 NOTYPE WEAK DEFAULT UND optional\n']
        with patch.object(v.subprocess,'check_output',side_effect=outputs): result=v.elf(p,'/readelf')
        self.assertEqual(result['needed'],['libx.so']); self.assertEqual(result['needs'],{'libx.so':{'DEP_1'}})
        self.assertEqual(result['definitions'],{'OWN_1'});self.assertEqual(result['undefined'],{'required'});self.assertEqual(result['defined'],{'own'})
    def test_native_mode_requires_explicit_gate(self):
        with patch.object(v.sys,'argv',['validate','run','--config','missing','--expected-config-sha256','0'*64]):
            with self.assertRaisesRegex(RuntimeError,'not authorized'): v.main()
    def test_bad_anchor_refused_before_import(self):
        p=self.root/'config.json';p.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'anchor'):v.anchored(p,'0'*64)
    def test_prepare_refuses_dangling_output(self):
        out=self.root/'out';out.symlink_to('missing')
        with patch.object(v,'check_inputs'):
            with self.assertRaisesRegex(RuntimeError,'reuse'):v.prepare({'output':str(out)})

    def test_static_rejects_absolute_f2c_runpath(self):
        with patch.object(v, 'installed', return_value=self.root), patch.object(v, 'elf', return_value={'rpath':['/build/lib']}):
            with self.assertRaisesRegex(RuntimeError,'RUNPATH'):
                v.static_review({'tools':{'readelf':{'path':'/readelf'}}})
    def static_fixture(self):
        site=self.root/'site'; site.mkdir(); system=self.root/'system'; system.mkdir()
        ext=site/v.EXTENSION; ext.parent.mkdir(); ext.write_bytes(b'fake')
        ort=site/'ortools/.libs/libortools.so.9'; ort.parent.mkdir(parents=True); ort.write_bytes(b'fake')
        py=self.root/'python'; py.write_bytes(b'fake')
        for name in ['libgdal.so.30','libtinyxml2.so.9','libz.so.1']: (system/name).write_bytes(b'fake')
        (site/'ortools/.libs/libz.so.1').write_bytes(b'fake')
        def fake(path, readelf):
            is_ext=path==ext
            return {'path':str(path),'sha256':'hash','needed':['libgdal.so.30','libtinyxml2.so.9','libortools.so.9','libz.so.1'] if is_ext else (['libz.so.1'] if path.name=='libgdal.so.30' else []),
                'soname':['libz.so.1'] if path.name=='libz.so.1' else [],'rpath':[':'.join(v.RPATH)] if is_ext else [],'needs':{},'definitions':set(),'defined':{'deflate'} if path.name=='libz.so.1' else {'symbol'},'undefined':{'deflate','symbol'} if is_ext else set(), 'defined_versions':{'deflate':{''}} if path.name=='libz.so.1' else {'symbol':{''}}, 'undefined_versions':{'deflate':{''},'symbol':{''}} if is_ext else {}}
        config={'tools':{'readelf':{'path':'/readelf'},'python':{'resolved':str(py)}},'system_library_dirs':[str(system)],'python_prefix':str(self.root), 'provider_alternatives':{'libz.so.1':{'policy':'exactly-one-first-loaded-compatible-provider','wheel':{'relative_path':'ortools/.libs/libz.so.1','sha256':v.ORTOOLS_SHA},'system':{'path':str(system/'libz.so.1'),'sha256':v.ORTOOLS_SHA}}}}
        return site,config,fake
    def test_static_complete_closure_passes(self):
        site,c,fake=self.static_fixture()
        with patch.object(v,'installed',return_value=site),patch.object(v,'elf',side_effect=fake),patch.object(v,'sha',return_value=v.ORTOOLS_SHA):
            result=v.static_review(c)
        self.assertEqual(set(result['providers']),{'libgdal.so.30','libtinyxml2.so.9','libortools.so.9'})
    def test_static_missing_version_rejected(self):
        site,c,fake=self.static_fixture()
        def altered(path,reader):
            result=fake(path,reader)
            if path==site/v.EXTENSION:result['needs']={'libgdal.so.30':{'MISSING_1'}}
            return result
        with patch.object(v,'installed',return_value=site),patch.object(v,'elf',side_effect=altered),patch.object(v,'sha',return_value=v.ORTOOLS_SHA):
            with self.assertRaisesRegex(RuntimeError,'versions'):v.static_review(c)
    def test_static_missing_strong_symbol_rejected(self):
        site,c,fake=self.static_fixture()
        def altered(path,reader):
            result=fake(path,reader)
            if path==site/v.EXTENSION:result['undefined']={'unprovided'}
            return result
        with patch.object(v,'installed',return_value=site),patch.object(v,'elf',side_effect=altered),patch.object(v,'sha',return_value=v.ORTOOLS_SHA):
            with self.assertRaisesRegex(RuntimeError,'strong dynamic'):v.static_review(c)
    def test_candidate_manifest_cannot_claim_acceptance(self):
        with self.assertRaisesRegex(RuntimeError,'candidate manifest'):
            v.manifest_bytes({'manifest':{'acceptance_state':'production'},'wheel':{'sha256':'a','extension_sha256':'b'}})

    def test_relocated_probe_checks_closure_with_base_python(self):
        helper=self.root/'recipe/verify_validation_build.py';helper.parent.mkdir();helper.write_text('# test')
        c={'candidate_root':str(self.root),'output':str(self.root/'relocated'),
            'harness':{str(helper):v.sha(helper)},'build_index_sha256':'a'*64,
            'tools':{'python':{'path':'/base/bin/python3.12'},'node':{'path':'/node/bin/node'}}}
        completed=v.subprocess.CompletedProcess([],0,stdout='{}',stderr='')
        with patch.object(v.sys,'executable','/relocated/venv/bin/python'),patch.object(v.subprocess,'run',return_value=completed) as run:
            v.verify_build_closure(c)
        argv=run.call_args.args[0]
        self.assertEqual(argv[:3],['/base/bin/python3.12','-I','-B'])
        self.assertEqual(argv[argv.index('--python')+1],'/base/bin/python3.12')
        self.assertTrue(run.call_args.kwargs['close_fds'])
        self.assertNotIn('LD_LIBRARY_PATH',run.call_args.kwargs['env'])
    def test_base_closure_failure_is_not_ignored(self):
        helper=self.root/'recipe/verify_validation_build.py';helper.parent.mkdir();helper.write_text('# test')
        c={'candidate_root':str(self.root),'output':str(self.root/'relocated'),
            'harness':{str(helper):v.sha(helper)},'build_index_sha256':'a'*64,
            'tools':{'python':{'path':'/base/bin/python3.12'},'node':{'path':'/node/bin/node'}}}
        completed=v.subprocess.CompletedProcess([],2,stdout='',stderr='base identity mismatch')
        with patch.object(v.subprocess,'run',return_value=completed):
            with self.assertRaisesRegex(RuntimeError,'base identity mismatch'):v.verify_build_closure(c)

    def test_junit_subtest_accounting_not_double_counted(self):
        p=self.root/'result.xml'
        p.write_text('<testsuites><testsuite tests="619" errors="0" failures="0" skipped="0">'+('<testcase/>'*602)+'</testsuite></testsuites>')
        v.assert_junit(p,602)
        p.write_text('<testsuites><testsuite tests="619" errors="1" failures="0" skipped="0">'+('<testcase/>'*602)+'</testsuite></testsuites>')
        with self.assertRaises(RuntimeError):v.assert_junit(p,602)

    def test_system_dirs_include_measured_nonmultiarch_provider(self):
        path=Path(__file__).resolve().parents[1]/'freeze_validation_config.py'
        spec=importlib.util.spec_from_file_location('freeze_for_test',path)
        freezer=importlib.util.module_from_spec(spec);spec.loader.exec_module(freezer)
        evidence=self.root/'preparation-evidence/runtime-interface.json';evidence.parent.mkdir()
        evidence.write_text(json.dumps({'libraries':{'libarmadillo.so.10':{'origin':'system','file':{
            'ref':{'base':'system','path':'usr/lib/libarmadillo.so.10'},
            'resolved_ref':{'base':'system','path':'usr/lib/libarmadillo.so.10'},'sha256':'digest'}}}}))
        with patch.object(freezer.v,'sha',return_value='digest'),patch.object(Path,'resolve',lambda self,strict=False:self):
            providers,dirs,digest=freezer.measured_system_runtime(self.root)
        self.assertIn('/usr/lib',dirs)
        self.assertEqual(providers['libarmadillo.so.10']['path'],'/usr/lib/libarmadillo.so.10')

    def zlib_symbol_fixture(self):
        def provider():return {'soname':['libz.so.1'],'defined':{'deflate','inflate'},'defined_versions':{'deflate':{''},'inflate':{'ZLIB_1.2.9',''}},'undefined_versions':{}}
        return {'wheel':provider(),'system':provider(),'consumer':{'undefined_versions':{'deflate':{''},'inflate':{'ZLIB_1.2.9'}}}}
    def test_both_libz_symbol_sets_required(self):
        seen=self.zlib_symbol_fixture()
        self.assertEqual(v.check_libz_symbols(seen,['wheel','system']),{'deflate':[''],'inflate':['ZLIB_1.2.9']})
        seen['system']['defined_versions'].pop('deflate')
        with self.assertRaisesRegex(RuntimeError,'strong/versioned'):v.check_libz_symbols(seen,['wheel','system'])
    def test_libz_version_pair_not_just_version_name(self):
        seen=self.zlib_symbol_fixture();seen['system']['defined_versions']['inflate']={'ZLIB_OTHER',''}
        seen['system']['defined_versions']['unrelated']={'ZLIB_1.2.9'}
        with self.assertRaisesRegex(RuntimeError,'inflate'):v.check_libz_symbols(seen,['wheel','system'])
    def test_libz_unversioned_requires_default_export(self):
        seen=self.zlib_symbol_fixture();seen['system']['defined_versions']['deflate']={'ZLIB_NONDEFAULT'}
        with self.assertRaisesRegex(RuntimeError,'deflate'):v.check_libz_symbols(seen,['wheel','system'])
    def test_non_libz_alternative_prohibited(self):
        with self.assertRaisesRegex(RuntimeError,'Only libz'):
            v.libz_alternatives({'provider_alternatives':{'libz.so.1':{},'libortools.so.9':{}}},self.root)
    def test_mapped_libz_one_exact_approved_provider(self):
        paths=['/wheel/libz.so.1','/system/libz.so.1.2.11']
        static={'provider_alternatives':{'libz.so.1':paths},'files':{paths[0]:'wheel-hash',paths[1]:'system-hash'}}
        for path in paths:self.assertEqual(v.validate_mapped_libz({path:static['files'][path]},static),path)
        for maps in [{},dict(static['files']),{paths[0]:'wrong'},{'/other/libz.so.1':'wheel-hash'}]:
            with self.assertRaisesRegex(RuntimeError,'mapped libz'):v.validate_mapped_libz(maps,static)
    def test_libz_version_requirement_applies_to_both(self):
        site,c,fake=self.static_fixture()
        def altered(path,reader):
            result=fake(path,reader)
            if path==site/v.EXTENSION:result['needs']={'libz.so.1':{'ZLIB_1.2.9'}}
            if path==site/'ortools/.libs/libz.so.1':result['definitions']={'ZLIB_1.2.9'}
            return result
        with patch.object(v,'installed',return_value=site),patch.object(v,'elf',side_effect=altered),patch.object(v,'sha',return_value=v.ORTOOLS_SHA):
            with self.assertRaisesRegex(RuntimeError,'versions'):v.static_review(c)

    def test_mapped_libz_count_uses_soname_not_filename(self):
        approved='/wheel/libz.so.1';hidden='/other/renamed.so'
        static={'provider_alternatives':{'libz.so.1':[approved]},'files':{approved:'ok'}}
        maps={approved:'ok',hidden:'other'};sonames={approved:['libz.so.1'],hidden:['libz.so.1']}
        with self.assertRaisesRegex(RuntimeError,'mapped libz'):v.validate_mapped_libz(maps,static,sonames)

    def test_stdlib_zlib_consumer_contributes_symbol_obligations(self):
        site,c,fake=self.static_fixture()
        stdlib=self.root/'lib/python3.12/lib-dynload/zlib.cpython-312.so';stdlib.parent.mkdir(parents=True);stdlib.write_bytes(b'\x7fELFfixture')
        def altered(path,reader):
            result=fake(path,reader)
            if path==stdlib:
                result['undefined']={'inflateValidate'}
                result['undefined_versions']={'inflateValidate':{'ZLIB_1.2.9'}}
            if path==site/'ortools/.libs/libz.so.1':
                result['defined'].add('inflateValidate')
                result['defined_versions']['inflateValidate']={'ZLIB_1.2.9',''}
            return result
        with patch.object(v,'installed',return_value=site),patch.object(v,'elf',side_effect=altered),patch.object(v,'sha',return_value=v.ORTOOLS_SHA):
            with self.assertRaisesRegex(RuntimeError,'inflateValidate'):v.static_review(c)

if __name__ == '__main__': unittest.main()
