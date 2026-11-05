"""Focused negative gates: a green Lab never proves the archive/Jobs healthy."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('smoke_archive', ROOT / 'tools/smoke_archive.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class ArchiveSmokeTests(unittest.TestCase):
    def test_failed_backend_is_terminal_even_when_lab_is_green(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(smoke, 'request', side_effect=[{'status': 'ok'}, {'services': [
                    {'id': 'archive', 'state': 'failed', 'detail': 'exit 127'}]}]), \
                self.assertRaisesRegex(RuntimeError, 'Archive startup failed'):
            smoke.smoke('http://unused', Path(tmp) / 'output')

    def test_unavailable_backend_fails_without_waiting_out_timeout(self):
        with patch.object(smoke, 'request', return_value={'services': [
                {'id': 'archive', 'state': 'unavailable'}]}), \
                self.assertRaisesRegex(RuntimeError, 'Archive startup failed'):
            smoke.wait_archive('http://unused', 'running')

    def test_restart_waits_for_new_process_identity(self):
        with patch.object(smoke, 'request', side_effect=[
                {'services': [{'id': 'archive', 'state': 'running', 'pid': 1}]},
                {'services': [{'id': 'archive', 'state': 'running', 'pid': 2}]}]), \
                patch.object(smoke.time, 'sleep'):
            self.assertEqual(smoke.wait_archive('http://unused', 'running', previous_pid=1)['pid'], 2)

    def test_bad_job_or_independent_check_fails_gate(self):
        good = {'status': 'completed', 'payload_sha256': 'abc', 'result': {
            'feasible': True, 'cost': 4, 'payload_sha256': 'abc', 'checkpoint_checks': [{'valid': True}]}}
        bad_jobs = [dict(good, status='failed'),
                    dict(good, result=dict(good['result'], checkpoint_checks=[])),
                    dict(good, result=dict(good['result'], checkpoint_checks=[{'valid': False}])),
                    dict(good, result=dict(good['result'], payload_sha256='other'))]
        for job in bad_jobs:
            with self.subTest(job=job), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(smoke, 'wait_archive', return_value={'controls': ['stop', 'restart']}), \
                    patch.object(smoke, 'request', side_effect=[
                        {'status': 'ok'}, {'status': 'ok'},
                        {'algorithms': [{'id': 'savings', 'available': True}]}, {'id': 'job-1'}, job]), \
                    self.assertRaisesRegex(RuntimeError, 'Job/check/identity failed'):
                smoke.smoke('http://unused', Path(tmp) / 'output')

    def test_external_archive_not_mutated(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(smoke, 'request', return_value={'status': 'ok'}), \
                patch.object(smoke, 'wait_archive', return_value={'controls': []}), \
                self.assertRaisesRegex(RuntimeError, 'external'):
            smoke.smoke('http://unused', Path(tmp) / 'output')


if __name__ == '__main__':
    unittest.main()
