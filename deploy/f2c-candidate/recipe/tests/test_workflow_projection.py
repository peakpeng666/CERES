"""Hermetic workflow bootstrap/module tests; never read an unindexed workflow."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

R = Path(__file__).resolve().parents[1]
S = importlib.util.spec_from_file_location('workflow_projection_gate', R/'verify_workflow_projection.py')
M = importlib.util.module_from_spec(S)
S.loader.exec_module(M)

class WorkflowProjectionTests(unittest.TestCase):
    def workflow(self):
        return "          python3 - <<'PY_FAILURE'\n"+textwrap.indent(M.BOOTSTRAP.rstrip('\n'),'          ')+'\n          PY_FAILURE\n'

    def test_exact_bootstrap_parity(self):
        M.verify_text(self.workflow())

    def test_changed_bootstrap_rejected(self):
        with self.assertRaises(ValueError): M.verify_text(self.workflow().replace("print('code=UNKNOWN')","print('PRIVATE_SENTINEL')"))

    def test_duplicate_bootstrap_rejected(self):
        with self.assertRaises(ValueError): M.verify_text(self.workflow()*2)

    def test_missing_bootstrap_rejected(self):
        with self.assertRaises(ValueError): M.verify_text('')

    def run_bootstrap(self, tamper=None):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'recipe').mkdir();(root/'private-workflow-evidence').mkdir()
            files={}
            for name in ('report_candidate_failure.py','project_failure_code.py','project_canonical_target.py'):
                data=(R/name).read_bytes();(root/'recipe'/name).write_bytes(data)
                files['recipe/'+name]={'sha256':hashlib.sha256(data).hexdigest()}
            raw=json.dumps({'files':files}).encode();(root/'source-index.json').write_bytes(raw)
            (root/'private-workflow-evidence/helper-tests.log').write_text('PRIVATE_SENTINEL /private/path https://private.invalid\nFAILED (errors=1)\n')
            expected=hashlib.sha256(raw).hexdigest()
            report=root/'recipe/report_candidate_failure.py'
            if tamper=='module':report.write_text("print('PRIVATE_SENTINEL /private/path https://private.invalid')\n")
            if tamper=='index':expected='0'*64
            if tamper=='missing':report.unlink()
            if tamper=='symlink':
                data=report.read_bytes();report.unlink();(root/'other.py').write_bytes(data);report.symlink_to(root/'other.py')
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','CANDIDATE_ROOT':td,'REVIEWED_SOURCE_INDEX_SHA256':expected,'FAILED_STAGE':'helper_tests'}
            p=subprocess.run([sys.executable,'-I','-B','-'],input=M.BOOTSTRAP,text=True,capture_output=True,env=env,check=True)
            self.assertEqual(p.stderr,'')
            for value in ('PRIVATE_SENTINEL','/private/path','https://private.invalid'):self.assertNotIn(value,p.stdout)
            return p.stdout

    def test_bootstrap_executes_actual_indexed_report_module(self):
        self.assertEqual(self.run_bootstrap(),'code=HELPER_TESTS_FAILED\n')

    def test_changed_module_not_executed(self):
        self.assertEqual(self.run_bootstrap('module'),'code=UNKNOWN\n')

    def test_wrong_index_rejected(self):
        self.assertEqual(self.run_bootstrap('index'),'code=UNKNOWN\n')

    def test_missing_module_rejected(self):
        self.assertEqual(self.run_bootstrap('missing'),'code=UNKNOWN\n')

    def test_aliased_module_rejected(self):
        self.assertEqual(self.run_bootstrap('symlink'),'code=UNKNOWN\n')

if __name__=='__main__':unittest.main()
