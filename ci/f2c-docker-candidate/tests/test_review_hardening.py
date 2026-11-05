"""Pure local negative fixtures. No special node creation or Docker calls."""
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine_controller import verify_inputs
from freeze_input_index import freeze
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'workflow/compiler-build.yml.example'

class HardenedReviewTests(unittest.TestCase):
    def fixture(self, root):
        for name in ('engine_gate.py', 'offline_build.py'):
            (root/name).write_text('fixture')
        return freeze(root, 'build')

    def test_all_unsupported_node_types_are_rejected_without_opening(self):
        for kind in (stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK, 0):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); anchor=self.fixture(root)
                members=list(root.rglob('*'))
                # Deliberately do not create sockets/FIFOs or request capabilities.
                special=SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=kind | 0o600))
                with patch.object(Path, 'rglob', return_value=iter(members+[special])):
                    with self.assertRaisesRegex(RuntimeError, 'unsupported input member'):
                        verify_inputs(root,anchor)

    def test_unsafe_directory_permissions_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); anchor=self.fixture(root)
            (root/'extra').mkdir(mode=0o700); (root/'extra').chmod(0o770)
            with self.assertRaisesRegex(RuntimeError, 'writable input directory'):
                verify_inputs(root, anchor)

    def bootstrap(self):
        text=TEMPLATE.read_text()
        start=text.index('          import hashlib, json, pathlib, stat, sys\n')
        end=text.index('          PYVERIFY\n', start)
        return '\n'.join(line[10:] for line in text[start:end].splitlines())+'\n'

    def test_actual_inline_bootstrap_is_optimized_safe(self):
        for optimized in (False,True):
            for defect in ('none','index_hash','member_hash','duplicate_key','path_escape','commit'):
                with self.subTest(optimized=optimized,defect=defect), tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp); source=root/'source'; source.mkdir()
                    (source/'x.py').write_text('fixture')
                    index={'schema':1,'status':'reviewed-source','application_commit':'3acf840d1a0c16ab1cfcca15744e4ea6609ddb35','files':{'x.py':{'sha256':hashlib.sha256(b'fixture').hexdigest()}}}
                    if defect=='member_hash':index['files']['x.py']['sha256']='0'*64
                    if defect=='path_escape':index['files']['../outside']={'sha256':'0'*64}
                    if defect=='commit':index['application_commit']='0'*40
                    raw=json.dumps(index)
                    if defect=='duplicate_key':raw=raw.replace('"schema": 1','"schema": 1, "schema": 1')
                    (source/'source-index.json').write_text(raw)
                    expected=hashlib.sha256(raw.encode()).hexdigest() if defect!='index_hash' else '0'*64
                    command=[sys.executable,'-I','-B']+(['-O'] if optimized else [])+['-',str(source),str(root/'verified'),expected]
                    run=subprocess.run(command,input=self.bootstrap(),text=True,capture_output=True)
                    self.assertEqual(run.returncode==0,defect=='none')
                    if defect=='none':self.assertEqual((root/'verified/x.py').read_text(),'fixture')

    def trap(self):
        text=TEMPLATE.read_text(); start=text.index('          finish() {\n'); end=text.index('          trap finish EXIT\n',start)
        return '\n'.join(line[10:] for line in text[start:end].splitlines())+'\n'

    def test_receipt_projection_and_missing_evidence_cannot_report_shell_success(self):
        for original,phase,complete,expected in [(0,'isolation_and_build',False,1),(0,'invalid',False,1),(17,'preparation',False,17),(0,'isolation_and_build',True,0)]:
            with self.subTest(original=original,phase=phase,complete=complete), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                if complete:
                    (root/'prepared').mkdir(); (root/'build-evidence').mkdir()
                    (root/'prepared/prepared-image.json').write_text(json.dumps({'source_index_sha256':'a'*64,'input_index_sha256':'b'*64}))
                    (root/'build-evidence/result.json').write_text(json.dumps({'exit_code':0,'output_tar_sha256':'c'*64}))
                script='set -euo pipefail\nstate="$1"\ncandidate="$2"\nphase="$3"\n'+self.trap()+'trap finish EXIT\nexit "$4"\n'
                run=subprocess.run(['bash','-c',script,'fixture',str(root),str(ROOT),phase,str(original)],capture_output=True,text=True)
                self.assertEqual(run.returncode,expected,run.stderr)
                receipt=json.loads(run.stdout)
                self.assertFalse(receipt['raw_logs_durably_archived'])
                if expected==0:self.assertEqual(receipt['outcome'],'built_not_accepted')
                else:self.assertNotEqual(receipt['outcome'],'built_not_accepted')

if __name__=='__main__':unittest.main()
