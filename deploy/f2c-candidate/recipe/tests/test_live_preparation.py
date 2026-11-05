"""Pure/data-only fixtures for live-preparation code. No real downloads or ELF execution."""
import hashlib
import copy
import importlib.util
import io
import json
import os
import pathlib
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

RECIPE = pathlib.Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, RECIPE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

g = load('guardlib')
live = load('live_inputs')
runtime = load('runtime_prepare')
prepare = load('prepare_live')

class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='live-test-', dir=RECIPE / 'tests')
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def tar(self, rows):
        path = self.root / 'input.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, kind, content in rows:
                member = tarfile.TarInfo(name)
                member.mode = 0o644
                if kind == 'file':
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
                elif kind == 'symlink':
                    member.type = tarfile.SYMTYPE
                    member.linkname = content
                    archive.addfile(member)
                else:
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
        return path

class InputTests(Fixtures):
    def test_safe_tar_data_extraction(self):
        path = self.tar([('top/src/a', 'file', b'a')])
        live.tar_data_only(path, self.root / 'source')
        self.assertEqual((self.root / 'source/src/a').read_bytes(), b'a')

    def test_tar_escape_symlink_duplicate_and_multiple_roots_refused(self):
        cases = [[('../escape', 'file', b'x')], [('top/link', 'symlink', '/etc/passwd')],
                 [('top/a', 'file', b'x'), ('top/a', 'file', b'y')],
                 [('top/a', 'file', b'x'), ('other/a', 'file', b'y')]]
        for i, rows in enumerate(cases):
            path = self.tar(rows)
            with self.subTest(case=i), self.assertRaises(RuntimeError):
                live.tar_data_only(path, self.root / ('out' + str(i)))

    def test_interface_extraction_never_copies_native_libraries(self):
        path = self.tar([('top/include/a.h', 'file', b'header'),
                         ('top/lib/cmake/test.cmake', 'file', b'metadata'),
                         ('top/lib/libnative.so', 'file', b'not executed')])
        live.tar_data_only(path, self.root / 'headers', interface_only=True)
        self.assertTrue((self.root / 'headers/include/a.h').is_file())
        self.assertFalse((self.root / 'headers/lib/libnative.so').exists())

    def test_safe_zip_and_alias_refusal(self):
        path = self.root / 'input.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('ortools/.libs/data', b'fixture')
        live.wheel_data_only(path, self.root / 'wheel')
        self.assertEqual((self.root / 'wheel/ortools/.libs/data').read_bytes(), b'fixture')
        with zipfile.ZipFile(path, 'w') as archive:
            info = zipfile.ZipInfo('alias')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, '/etc/passwd')
        with self.assertRaisesRegex(RuntimeError, 'alias'):
            live.wheel_data_only(path, self.root / 'bad')

    def test_existing_download_requires_exact_bytes_without_network(self):
        p = self.root / 'cache'
        p.write_bytes(b'fixture')
        row = {'url': 'https://files.pythonhosted.org/fake', 'bytes': 7, 'sha256': hashlib.sha256(b'fixture').hexdigest()}
        with mock.patch.object(live.urllib.request, 'build_opener') as opener:
            self.assertEqual(live.fetch(row, p), p)
            opener.assert_not_called()
        p.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            live.fetch(row, p)

    def test_download_url_restrictions(self):
        for url in ('http://files.pythonhosted.org/a', 'https://evil.example/a', 'https://x@y.github.com/a', 'file:///etc/passwd'):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                live.approved_url(url)

    def test_file_row_records_alias_and_bytes(self):
        p = self.root / 'original'
        p.write_bytes(b'fixture')
        alias = self.root / 'alias'
        alias.symlink_to('original')
        row = live.file_row(alias, self.root, pathlib.Path('/usr'))
        self.assertEqual(row['symlink_target'], 'original')
        self.assertEqual(row['resolved_ref']['path'], 'original')
        self.assertEqual(row['sha256'], hashlib.sha256(b'fixture').hexdigest())

class HostAliasTests(Fixtures):
    def test_explicit_host_aliases_measure_hardlinks_and_dangling_links(self):
        p = self.root / 'a'
        p.write_text('fixture')
        os.link(p, self.root / 'b')
        (self.root / 'dangling').symlink_to('missing')
        rows = g.snapshot(self.root, self.root, host_aliases=True)
        self.assertEqual(next(r for r in rows if r['path'] == 'a')['link_count'], 2)
        self.assertTrue(next(r for r in rows if r['path'] == 'dangling')['dangling'])
        with self.assertRaises(RuntimeError):
            g.snapshot(self.root, self.root)

    def test_dangling_target_changes_measured_snapshot(self):
        p = self.root / 'link'
        p.symlink_to('missing')
        old = g.snapshot(self.root, self.root, host_aliases=True)
        p.unlink()
        p.symlink_to('other')
        self.assertNotEqual(old, g.snapshot(self.root, self.root, host_aliases=True))

class PackagePayloadTests(Fixtures):
    def payload(self, name, data):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        stream.seek(0)
        return stream

    def test_selected_system_bytes_match_mocked_authenticated_archive(self):
        p = self.root / 'binary'
        p.write_bytes(b'fixture')
        proc = mock.Mock()
        proc.stdout = self.payload(str(p).lstrip('/'), b'fixture')
        proc.wait.return_value = 0
        proc.poll.return_value = 0
        with mock.patch.object(live.subprocess, 'Popen', return_value=proc) as command:
            proof = live.verify_system_archive_bytes([pathlib.Path('authenticated.deb')], [p])
        self.assertEqual(proof[str(p)]['sha256'], hashlib.sha256(b'fixture').hexdigest())
        self.assertEqual(command.call_args.args[0][:2], ['/usr/bin/dpkg-deb', '--fsys-tarfile'])

    def test_changed_selected_bytes_refused(self):
        p = self.root / 'binary'
        p.write_bytes(b'changed')
        proc = mock.Mock()
        proc.stdout = self.payload(str(p).lstrip('/'), b'fixture')
        proc.poll.return_value = 0
        with mock.patch.object(live.subprocess, 'Popen', return_value=proc), self.assertRaisesRegex(RuntimeError, 'differs'):
            live.verify_system_archive_bytes([pathlib.Path('authenticated.deb')], [p])

    def test_missing_archive_provenance_refused(self):
        p = self.root / 'binary'
        p.write_bytes(b'fixture')
        with self.assertRaisesRegex(RuntimeError, 'Missing official'):
            live.verify_system_archive_bytes([], [p])

class RuntimeTests(Fixtures):
    def test_static_readelf_parser(self):
        dynamic = '  (NEEDED) Shared library: [libc.so.6]\n  (SONAME) Library soname: [libx.so.1]\n  (RUNPATH) Library runpath: [$ORIGIN]\n'
        versions = 'Version definition section\n  Name: LIBX_1\nVersion needs section\n  File: libc.so.6\n  Name: GLIBC_2.17\n'
        result = runtime.parse_elf(dynamic, versions)
        self.assertEqual(result['needed'], ['libc.so.6'])
        self.assertEqual(result['definitions'], ['LIBX_1'])
        self.assertEqual(result['version_needs'], {'libc.so.6': ['GLIBC_2.17']})

    def test_core_system_priority_and_ortools_wheel_priority(self):
        wheel, system = self.root / 'wheel', self.root / 'system'
        (wheel / 'ortools/.libs').mkdir(parents=True)
        system.mkdir()
        for name in ('libc.so.6', 'libortools.so.9'):
            (wheel / 'ortools/.libs' / name).write_text('wheel')
            (system / name).write_text('system')
        self.assertEqual(runtime.resolve_soname('libc.so.6', wheel, [system])[1], 'system')
        self.assertEqual(runtime.resolve_soname('libortools.so.9', wheel, [system])[1], 'wheel')

    def test_unsafe_or_unresolved_soname_refused(self):
        for name in ('../../escape', 'missing.so'):
            with self.assertRaises(RuntimeError):
                runtime.resolve_soname(name, self.root, [])

    def test_interface_properties_accept_indented_closing_parenthesis(self):
        text = 'set_target_properties(x::x PROPERTIES\n  INTERFACE_COMPILE_DEFINITIONS "A;B"\n  )\n'
        self.assertEqual(runtime.target_properties(text, 'x::x')['INTERFACE_COMPILE_DEFINITIONS'], 'A;B')

    def test_interface_metadata_is_checked_not_native_abi_claimed(self):
        ortools = self.root / 'lib/cmake/ortools/ortoolsTargets.cmake'
        ortools.parent.mkdir(parents=True)
        ortools.write_text('set_target_properties(ortools::ortools PROPERTIES\n'
                           ' INTERFACE_COMPILE_DEFINITIONS "OR_PROTO_DLL=;USE_MATH_OPT;USE_BOP;USE_CBC;USE_CLP;USE_GLOP;USE_HIGHS;USE_PDLP;USE_SCIP"\n'
                           ' INTERFACE_COMPILE_OPTIONS "-fwrapv;-Wno-range-loop-construct;-Wno-sign-compare"\n'
                           ' INTERFACE_COMPILE_FEATURES "cxx_std_17"\n)\n')
        proto = self.root / 'lib/cmake/protobuf/targets.cmake'
        proto.parent.mkdir(parents=True)
        proto.write_text('set_target_properties(protobuf::libprotobuf PROPERTIES\n INTERFACE_COMPILE_DEFINITIONS "PROTOBUF_USE_DLLS"\n)\n')
        release = proto.with_name('targets-release.cmake')
        release.write_text('set_target_properties(protobuf::libprotobuf PROPERTIES\n IMPORTED_SONAME_RELEASE "libprotobuf.so.33.1.0"\n )\n')
        definitions, options = runtime.interface_flags(self.root)
        self.assertIn('PROTOBUF_USE_DLLS', definitions)
        self.assertIn('_GLIBCXX_USE_CXX11_ABI=1', definitions)
        proto.write_text(proto.read_text().replace('PROTOBUF_USE_DLLS', 'CHANGED'))
        with self.assertRaisesRegex(RuntimeError, 'DLL definitions changed'):
            runtime.interface_flags(self.root)

class SourceAnchorTests(Fixtures):
    def index(self):
        files = {}
        for name in prepare.SOURCE_REQUIRED:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('fixture only, never executed')
            files[name] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
        p = self.root / 'source-index.json'
        p.write_text(json.dumps({'schema': 1, 'status': 'reviewed-source', 'files': files}))
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def test_valid_source_index_reads_only(self):
        anchor = self.index()
        with mock.patch.object(prepare, 'load') as load_helper:
            result = prepare.source_anchor(self.root, anchor)
            load_helper.assert_not_called()
        self.assertEqual(result['status'], 'reviewed-source')

    def test_wrong_source_anchor_rejects_before_helper_import(self):
        self.index()
        with mock.patch.object(prepare, 'load') as load_helper, self.assertRaisesRegex(RuntimeError, 'anchor mismatch'):
            prepare.prepare(self.root, pathlib.Path(sys.executable), self.root / 'missing', '0' * 64)
        load_helper.assert_not_called()

    def test_changed_preparer_rejected(self):
        anchor = self.index()
        (self.root / 'recipe/prepare_live.py').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            prepare.source_anchor(self.root, anchor)

class ZlibAlternativeTests(Fixtures):
    def setup_providers(self):
        candidate = self.root / 'candidate'
        wheel = candidate / 'provider'
        system = self.root / 'system'
        (wheel / 'ortools/.libs').mkdir(parents=True)
        system.mkdir()
        for path, data in ((wheel / 'ortools/.libs/libz.so.1', b'wheel-zlib'),
                           (system / 'libz.so.1', b'jammy-zlib'), (system / 'libc.so.6', b'jammy-libc')):
            path.write_bytes(data)
        prefix = self.root / 'python'
        wheel_row = live.file_row(wheel / 'ortools/.libs/libz.so.1', candidate, prefix)
        libc_row = live.file_row(system / 'libc.so.6', candidate, prefix)
        selected = {
            'libz.so.1': {'origin': 'wheel', 'file': wheel_row, 'sonames': ['libz.so.1'],
                          'definitions': ['ZLIB_1.2.9'], 'needed': ['libc.so.6'],
                          'version_needs': {'libc.so.6': ['GLIBC_2.2.5']}},
            'libc.so.6': {'origin': 'system', 'file': libc_row, 'sonames': ['libc.so.6'],
                          'definitions': ['GLIBC_2.2.5'], 'needed': [], 'version_needs': {}},
            'consumer.so': {'origin': 'wheel', 'definitions': [], 'needed': ['libz.so.1'],
                            'version_needs': {'libz.so.1': ['ZLIB_1.2.9']}},
        }
        alternate = {'sonames': ['libz.so.1'], 'definitions': ['ZLIB_1.2.9'], 'needed': ['libc.so.6'],
                     'version_needs': {'libc.so.6': ['GLIBC_2.2.5']}}
        return candidate, prefix, wheel, system, selected, alternate

    def test_only_exact_libz_alternatives_are_emitted(self):
        candidate, prefix, wheel, system, selected, alternate = self.setup_providers()
        with mock.patch.object(runtime, 'inspect', return_value=alternate) as inspect:
            result = runtime.zlib_provider_alternative(candidate, prefix, wheel, [system], selected)
        inspect.assert_called_once_with(system / 'libz.so.1')
        self.assertEqual(set(result), {'libz.so.1'})
        self.assertEqual(set(result['libz.so.1']), {'policy', 'wheel', 'system'})
        self.assertEqual(result['libz.so.1']['policy'], 'exactly-one-first-loaded-compatible-provider')
        self.assertEqual(result['libz.so.1']['wheel']['ref']['base'], 'candidate')
        self.assertEqual(result['libz.so.1']['system']['ref']['base'], 'system')
        self.assertEqual(result['libz.so.1']['system']['sha256'], hashlib.sha256(b'jammy-zlib').hexdigest())

    def test_incompatible_alternative_version_is_refused(self):
        candidate, prefix, wheel, system, selected, alternate = self.setup_providers()
        alternate['definitions'] = []
        with mock.patch.object(runtime, 'inspect', return_value=alternate), self.assertRaisesRegex(RuntimeError, 'lacks required symbol versions'):
            runtime.zlib_provider_alternative(candidate, prefix, wheel, [system], selected)

    def test_alternative_cannot_expand_dependency_closure(self):
        candidate, prefix, wheel, system, selected, alternate = self.setup_providers()
        alternate['needed'].append('unmeasured.so')
        with mock.patch.object(runtime, 'inspect', return_value=alternate), self.assertRaisesRegex(RuntimeError, 'expands unmeasured'):
            runtime.zlib_provider_alternative(candidate, prefix, wheel, [system], selected)

    def test_alternative_soname_must_remain_libz_only(self):
        candidate, prefix, wheel, system, selected, alternate = self.setup_providers()
        alternate['sonames'] = ['libother.so.1']
        with mock.patch.object(runtime, 'inspect', return_value=alternate), self.assertRaisesRegex(RuntimeError, 'SONAME'):
            runtime.zlib_provider_alternative(candidate, prefix, wheel, [system], selected)

    def test_missing_system_alternative_is_refused(self):
        candidate, prefix, wheel, system, selected, alternate = self.setup_providers()
        (system / 'libz.so.1').unlink()
        with mock.patch.object(runtime, 'inspect') as inspect, self.assertRaisesRegex(RuntimeError, 'Missing actual Jammy'):
            runtime.zlib_provider_alternative(candidate, prefix, wheel, [system], selected)
        inspect.assert_not_called()

    def test_system_alias_to_wheel_is_not_an_independent_provider(self):
        candidate, prefix, wheel, system, selected, alternate = self.setup_providers()
        (system / 'libz.so.1').unlink()
        (system / 'libz.so.1').symlink_to(wheel / 'ortools/.libs/libz.so.1')
        with self.assertRaisesRegex(RuntimeError, 'aliases the wheel'):
            runtime.zlib_provider_alternative(candidate, prefix, wheel, [system], selected)

    def test_preparation_returns_system_alternative_for_archive_proof(self):
        candidate, prefix, wheel, system, selected, alternate = self.setup_providers()
        (candidate / 'preparation-evidence').mkdir()
        with mock.patch.object(runtime, 'inspect', return_value=alternate):
            alternatives = runtime.zlib_provider_alternative(candidate, prefix, wheel, [system], selected)
        def inspect(path):
            key = path.name
            return {k: copy.deepcopy(v) for k, v in selected[key].items() if k not in ('origin', 'file')}
        def resolve(name, *_):
            return (wheel / 'ortools/.libs' / name, 'wheel') if name == 'libz.so.1' else (system / name, 'system')
        with mock.patch.object(runtime, 'ROOT_LIBRARIES', ('libz.so.1', 'libc.so.6')), \
             mock.patch.object(runtime, 'resolve_soname', side_effect=resolve), \
             mock.patch.object(runtime, 'inspect', side_effect=inspect), \
             mock.patch.object(runtime, 'interface_flags', return_value=('fixture', 'fixture')), \
             mock.patch.object(runtime, 'zlib_provider_alternative', return_value=alternatives):
            shim, system_rows, evidence = runtime.prepare_runtime(candidate, prefix, wheel, candidate / 'headers')
        self.assertIn(alternatives['libz.so.1']['system'], system_rows)
        self.assertEqual(json.loads(evidence.read_text())['provider_alternatives'], alternatives)
        self.assertIn('does NOT pin the runtime libz.so.1 SONAME', (shim / 'ortoolsConfig.cmake').read_text())

if __name__ == '__main__':
    unittest.main(verbosity=2)
