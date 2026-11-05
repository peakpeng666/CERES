import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from public_receipt import project

class PublicReceiptTests(unittest.TestCase):
    def test_closed_failures_never_emit_raw_values(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            (root/'terminal.json').write_text(json.dumps({'phase':'preparation','exit_code':17,'raw':'PRIVATE'}))
            (root/'prepared').mkdir()
            (root/'prepared/prepared-image.json').write_text(json.dumps({'source_index_sha256':'PRIVATE','input_index_sha256':'PRIVATE','raw':'PRIVATE'}))
            result=project(root)
            self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertEqual(result['failure_category'],'TRUSTED_PREPARATION_FAILED')
            self.assertFalse(result['raw_logs_durably_archived'])
            self.assertEqual(result['stages'][-1]['status'],'not_run')
    def test_success_requires_complete_hash_receipt_and_never_accepts_runtime(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            (root/'terminal.json').write_text(json.dumps({'phase':'isolation_and_build','exit_code':0}))
            self.assertEqual(project(root)['failure_category'],'RESULT_RECEIPT_MISSING')
            (root/'prepared').mkdir();(root/'build-evidence').mkdir()
            (root/'prepared/prepared-image.json').write_text(json.dumps({'source_index_sha256':'a'*64,'input_index_sha256':'b'*64}))
            (root/'build-evidence/result.json').write_text(json.dumps({'exit_code':0,'output_tar_sha256':'c'*64}))
            result=project(root)
            self.assertEqual(result['outcome'],'built_not_accepted')
            self.assertEqual(result['fresh_runtime_acceptance'],'not_run')
            self.assertEqual(result['current_application_full_suite'],'not_run')
    def test_bad_stage_or_unbounded_exit_fails_closed(self):
        for value in ({'phase':'PRIVATE','exit_code':1},{'phase':'bootstrap','exit_code':999},{'phase':'bootstrap','exit_code':True}):
            with tempfile.TemporaryDirectory() as name:
                root=Path(name);(root/'terminal.json').write_text(json.dumps(value))
                with self.assertRaises(ValueError):project(root)
