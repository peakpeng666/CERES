import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import textwrap
import unittest
from unittest.mock import patch

R=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('workflow_contract',R/'verify_workflow_contract.py')
W=importlib.util.module_from_spec(spec);spec.loader.exec_module(W)

def body(marker):
    s=W.TEMPLATE.split("<<'"+marker+"'\n",1)[1].split('\n          '+marker,1)[0]
    return textwrap.dedent(s)+'\n'

def compiled_body(marker, optimize):
    return compile(body(marker), '<workflow:'+marker+'>', 'exec', optimize=optimize)

class WorkflowTests(unittest.TestCase):
    def test_bootstrap_bodies_have_no_assert_statements(self):
        for marker in ('PY_ANCHOR','PY_HELPERS','PY_REPORT'):
            with self.subTest(marker=marker):
                self.assertFalse(any(isinstance(node,ast.Assert) for node in ast.walk(ast.parse(body(marker)))))

    def test_full_parity(self):
        anchor='a'*64
        W.verify_text(W.render(anchor),anchor)
        for old,new in [('ubuntu-22.04','ubuntu-24.04'),('contents: read','contents: write'),('fix/agriculture-compatibility-20261022','main'),('paths: [.github/workflows/f2c-capability.yml]','paths: ["**"]'),('--execute-capability-probe','--unsafe'),('-I -B -S','-I -B')]:
            with self.subTest(old=old),self.assertRaises(ValueError):
                W.verify_text(W.render(anchor).replace(old,new),anchor)
    def test_all_shell_bodies_bound_stderr_without_masking_failure(self):
        bodies=W.render('a'*64).split('        run: |\n')[1:]
        self.assertEqual(len(bodies),5)
        for body in bodies:
            first=body.splitlines()[0].strip()
            self.assertEqual(first,'exec 2>/dev/null')
            result=subprocess.run(['/bin/bash','-c',first+"\nprintf '/home/private/token https://private.invalid' >&2; exit 23"],capture_output=True,text=True,env={'PATH':'/usr/bin:/bin'})
            self.assertEqual((result.returncode,result.stdout,result.stderr),(23,'',''))
    def test_failed_authentication_keeps_namespace_step_gated(self):
        workflow=W.render('a'*64)
        self.assertLess(workflow.index('id: authentication'),workflow.index('id: capability'))
        step=workflow.split('        id: capability',1)[1].split('      - name: Publish',1)[0]
        self.assertNotIn('if:',step)
        self.assertIn('--execute-capability-probe',step)

    def test_invalid_anchors(self):
        for value in ('','x'*64,'a'*63,'a'*65,'A'*64,'a'*64+'\n'):
            with self.subTest(value=value),self.assertRaises(ValueError): W.render(value)
    def test_source_index_only_materialization(self):
        for optimize in (0,1):
            with self.subTest(optimize=optimize),tempfile.TemporaryDirectory() as d:
                base=Path(d); source=base/'recipe-source/deploy/f2c-capability';source.mkdir(parents=True)
                target=base/'target';target.mkdir()
                (source/'recipe').mkdir();data=b'VALUE=1\n';(source/'recipe/demo.py').write_bytes(data)
                raw=json.dumps({'schema':1,'status':'reviewed-source','identity':'jammy-rootless-capability-v7','files':{'recipe/demo.py':{'sha256':hashlib.sha256(data).hexdigest()}}}).encode()
                (source/'source-index.json').write_bytes(raw)
                (source/'unindexed-private.txt').write_text('UNINDEXED MUST NOT COPY')
                # A source-index-only checkout has no candidate workflow at all.
                self.assertFalse((source/'candidate-workflow.yml').exists())
                env={'GITHUB_WORKSPACE':str(base),'CANDIDATE_ROOT':str(target),'REVIEWED_SOURCE_INDEX_SHA256':hashlib.sha256(raw).hexdigest()}
                with patch.dict(os.environ,env,clear=True): exec(compiled_body('PY_ANCHOR',optimize),{})
                self.assertEqual((target/'recipe/demo.py').read_bytes(),data)
                self.assertEqual((target/'source-index.json').read_bytes(),raw)
                self.assertFalse((target/'unindexed-private.txt').exists())
                self.assertFalse((target/'candidate-workflow.yml').exists())

    def test_anchor_rejects_symlink_and_hardlink(self):
        for optimize in (0,1):
            for kind in ('symlink','hardlink'):
                with self.subTest(optimize=optimize,kind=kind),tempfile.TemporaryDirectory() as d:
                    base=Path(d); source=base/'recipe-source/deploy/f2c-capability';source.mkdir(parents=True)
                    target=base/'target';target.mkdir();original=base/'original';original.write_bytes(b'x')
                    path=source/'x'
                    if kind=='symlink':path.symlink_to(original)
                    else:os.link(original,path)
                    raw=json.dumps({'schema':1,'status':'reviewed-source','identity':'jammy-rootless-capability-v7','files':{'x':{'sha256':hashlib.sha256(b'x').hexdigest()}}}).encode()
                    (source/'source-index.json').write_bytes(raw)
                    env={'GITHUB_WORKSPACE':str(base),'CANDIDATE_ROOT':str(target),'REVIEWED_SOURCE_INDEX_SHA256':hashlib.sha256(raw).hexdigest()}
                    with patch.dict(os.environ,env,clear=True),self.assertRaises(ValueError):
                        exec(compiled_body('PY_ANCHOR',optimize),{})
                    self.assertFalse((target/'x').exists())

    def test_anchor_rejects_invalid_index_and_file_bytes_under_optimization(self):
        for optimize in (0,1):
            for invalid in ('anchor_format','index_digest','schema','status','identity','empty_files','absolute_path','parent_path','invalid_path','row_fields','row_digest','file_digest','duplicate_key'):
                with self.subTest(optimize=optimize,invalid=invalid),tempfile.TemporaryDirectory() as d:
                    base=Path(d);source=base/'recipe-source/deploy/f2c-capability';source.mkdir(parents=True)
                    target=base/'target';target.mkdir();(source/'x').write_bytes(b'x')
                    row={'sha256':hashlib.sha256(b'x').hexdigest()}
                    index={'schema':1,'status':'reviewed-source','identity':'jammy-rootless-capability-v7','files':{'x':row}}
                    if invalid=='schema':index['schema']=2
                    elif invalid=='status':index['status']='unreviewed'
                    elif invalid=='identity':index['identity']='jammy-rootless-capability-v2'
                    elif invalid=='empty_files':index['files']={}
                    elif invalid in ('absolute_path','parent_path','invalid_path'):
                        name={'absolute_path':'/x','parent_path':'../x','invalid_path':'x?'}[invalid]
                        index['files']={name:row}
                    elif invalid=='row_fields':row['unreviewed']='x'
                    elif invalid=='row_digest':row['sha256']='not-a-digest'
                    elif invalid=='file_digest':row['sha256']='0'*64
                    raw=json.dumps(index).encode()
                    if invalid=='duplicate_key':raw=raw.replace(b'"schema": 1',b'"schema": 1, "schema": 1')
                    (source/'source-index.json').write_bytes(raw)
                    expected=hashlib.sha256(raw).hexdigest()
                    if invalid=='anchor_format':expected='not-a-digest'
                    elif invalid=='index_digest':expected='0'*64
                    env={'GITHUB_WORKSPACE':str(base),'CANDIDATE_ROOT':str(target),'REVIEWED_SOURCE_INDEX_SHA256':expected}
                    with patch.dict(os.environ,env,clear=True),self.assertRaises(ValueError):
                        exec(compiled_body('PY_ANCHOR',optimize),{})
                    self.assertFalse((target/'source-index.json').exists())

    def test_reporting_bootstrap_uses_only_verified_bytes(self):
        for optimize in (0,1):
            for invalid in (None,'index_digest','reporter_digest','codes_digest','reporter_symlink','codes_symlink','reporter_hardlink','codes_hardlink','diagnostic_digest','identities_digest','diagnostic_symlink','identities_symlink','diagnostic_hardlink','identities_hardlink'):
                with self.subTest(optimize=optimize,invalid=invalid),tempfile.TemporaryDirectory() as d:
                    root=Path(d);(root/'recipe').mkdir()
                    reporter=b"if _VERIFIED_PUBLIC_CODES_BYTES!=b'{}': raise ValueError('Unverified bytes')\nprint('SAFE')\n"
                    files={'recipe/report_capability_status.py':reporter,'recipe/public_codes.json':b'{}','recipe/helper_test_reporter.py':b"def project(root, identities): return 'SAFE_HELPERS'\n",'inputs/helper-test-identities.json':b'{}'}
                    for name,data in files.items():
                        (root/name).parent.mkdir(parents=True,exist_ok=True);(root/name).write_bytes(data)
                    raw=json.dumps({'files':{k:{'sha256':hashlib.sha256(v).hexdigest()} for k,v in files.items()}}).encode()
                    (root/'source-index.json').write_bytes(raw)
                    expected=hashlib.sha256(raw).hexdigest()
                    if invalid=='index_digest':expected='0'*64
                    elif invalid=='reporter_digest':(root/'recipe/report_capability_status.py').write_bytes(b"print('UNVERIFIED')\n")
                    elif invalid=='codes_digest':(root/'recipe/public_codes.json').write_bytes(b'{"secret":"/home/private/token"}')
                    elif invalid in ('diagnostic_digest','identities_digest'):
                        path=root/('recipe/helper_test_reporter.py' if invalid=='diagnostic_digest' else 'inputs/helper-test-identities.json')
                        path.write_bytes(b'UNVERIFIED SECRET /private/token')
                    elif invalid:
                        which,kind=invalid.split('_')
                        path=root/{'reporter':'recipe/report_capability_status.py','codes':'recipe/public_codes.json','diagnostic':'recipe/helper_test_reporter.py','identities':'inputs/helper-test-identities.json'}[which]
                        original=root/'original';path.rename(original)
                        if kind=='symlink':path.symlink_to(original)
                        else:os.link(original,path)
                    env={'CANDIDATE_ROOT':str(root),'REVIEWED_SOURCE_INDEX_SHA256':expected}
                    out=io.StringIO()
                    with patch.dict(os.environ,env,clear=True),contextlib.redirect_stdout(out):
                        exec(compiled_body('PY_REPORT',optimize),{})
                    self.assertEqual(out.getvalue(),'SAFE\n' if invalid is None else 'stage=report status=FAIL code=UNKNOWN\n')

    def test_production_reporter_bootstrap_acceptance_contract(self):
        # These are the production bytes, not the synthetic PROVED fixture.
        files={name:(R.parent/name).read_bytes() for name in ('recipe/report_capability_status.py','recipe/public_codes.json','recipe/helper_test_reporter.py','inputs/helper-test-identities.json')}
        codes=json.loads(files['recipe/public_codes.json'])
        self.assertEqual(codes['authentication']['pass_code'],'CAP_INPUTS_VERIFIED')
        self.assertEqual(codes['capability']['pass_code'],'OK')
        self.assertNotIn('PROVED',codes['capability']['codes'])
        for optimize in (0,1):
            with self.subTest(optimize=optimize),tempfile.TemporaryDirectory() as d:
                root=Path(d);(root/'recipe').mkdir()
                for name,data in files.items():
                    (root/name).parent.mkdir(parents=True,exist_ok=True);(root/name).write_bytes(data)
                raw=json.dumps({'files':{k:{'sha256':hashlib.sha256(v).hexdigest()} for k,v in files.items()}}).encode()
                (root/'source-index.json').write_bytes(raw)
                (root/'capability-preparation-status.json').write_text(json.dumps({'schema':1,'status':'PASS','code':'CAP_INPUTS_VERIFIED'}))
                env={'CANDIDATE_ROOT':str(root),'REVIEWED_SOURCE_INDEX_SHA256':hashlib.sha256(raw).hexdigest()}
                env.update({'OUTCOME_'+stage.upper():'success' for stage in ('checkout','anchor','helper_tests','authentication','capability')})
                for code,expected in (('OK','stage=capability status=PASS code=OK\n'),('PROVED','stage=report status=FAIL code=UNKNOWN\n')):
                    with self.subTest(code=code):
                        (root/'capability-probe-status.json').write_text(json.dumps({'schema':1,'status':'PASS','code':code}))
                        out=io.StringIO()
                        with patch.dict(os.environ,env,clear=True),contextlib.redirect_stdout(out):
                            exec(compiled_body('PY_REPORT',optimize),{})
                        self.assertEqual(out.getvalue(),expected)


    def test_helper_bootstrap_preserves_exit_and_uses_verified_held_bytes(self):
        for optimize in (0,1):
            for invalid in (None,'index_digest','helper_digest','identities_digest','helper_symlink','helper_hardlink','identities_symlink','identities_hardlink'):
                with self.subTest(optimize=optimize,invalid=invalid),tempfile.TemporaryDirectory() as d:
                    root=Path(d);(root/'recipe').mkdir();(root/'inputs').mkdir()
                    helper=b"def run(root, identities):\n if identities != b'{}': raise ValueError('Unverified identities')\n return 23\n"
                    files={'recipe/helper_test_reporter.py':helper,'inputs/helper-test-identities.json':b'{}'}
                    for name,data in files.items():(root/name).write_bytes(data)
                    raw=json.dumps({'files':{k:{'sha256':hashlib.sha256(v).hexdigest()} for k,v in files.items()}}).encode()
                    (root/'source-index.json').write_bytes(raw)
                    expected=hashlib.sha256(raw).hexdigest()
                    if invalid=='index_digest':expected='0'*64
                    elif invalid=='helper_digest':(root/'recipe/helper_test_reporter.py').write_bytes(b"raise RuntimeError('UNVERIFIED SECRET')\n")
                    elif invalid=='identities_digest':(root/'inputs/helper-test-identities.json').write_bytes(b'{"secret":"/private/token"}')
                    elif invalid:
                        which,kind=invalid.split('_');path=root/('recipe/helper_test_reporter.py' if which=='helper' else 'inputs/helper-test-identities.json')
                        original=root/'original';path.rename(original)
                        if kind=='symlink':path.symlink_to(original)
                        else:os.link(original,path)
                    with patch('sys.argv',['-',str(root),expected]):
                        if invalid is None:
                            with self.assertRaises(SystemExit) as caught:exec(compiled_body('PY_HELPERS',optimize),{})
                            self.assertEqual(caught.exception.code,23)
                        else:
                            with self.assertRaises(ValueError):exec(compiled_body('PY_HELPERS',optimize),{})

    def test_helper_failure_gates_authentication_and_keeps_raw_output_private(self):
        workflow=W.render('a'*64)
        helper=workflow.split('        id: helper_tests',1)[1].split('      - name: Authenticate',1)[0]
        self.assertIn('private-evidence/tests.log" 2>&1',helper)
        self.assertIn("raise SystemExit(namespace['run']",helper)
        auth=workflow.split('        id: authentication',1)[1].split('      - name: Prove',1)[0]
        self.assertNotIn('if:',auth)
        self.assertIn('set -euo pipefail',helper)

if __name__=='__main__':unittest.main()
