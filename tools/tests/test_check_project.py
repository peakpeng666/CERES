"""Real subprocess regressions for the engineering gate lifecycle and evidence."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('check_project', ROOT / 'tools/check_project.py')
check_project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_project)

CHILD = '''import os,signal,sys,time
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()))
while True:
 print("child output",flush=True)
 time.sleep(.03)
'''
PARENT = '''import signal,subprocess,sys,time
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]],start_new_session=sys.argv[4]=="detached")
while not Path(sys.argv[2]).exists():time.sleep(.005)
if sys.argv[3]=="wait":time.sleep(30)
raise SystemExit(7 if sys.argv[3]=="failure" else 0)
'''


@unittest.skipUnless(sys.platform == 'linux', 'gate supervision requires Linux')
class GateLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'report'
        self.output.mkdir()
        self.pidfile = self.root / 'child.pid'
        self.addCleanup(self.cleanup_fixture)

    def cleanup_fixture(self):
        if self.pidfile.exists():
            pid = int(self.pidfile.read_text())
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

    def command(self, mode, detached=False):
        return [sys.executable, '-c', PARENT, CHILD, str(self.pidfile), mode,
                'detached' if detached else 'same-group']

    def assert_reaped_and_stable(self, result):
        self.assertTrue(result['cleanup']['complete'], result)
        self.assertEqual(result['cleanup']['remaining_pids'], [])
        pid = int(self.pidfile.read_text())
        self.assertIn(pid, [row['pid'] for row in result['cleanup']['reaped_descendants']])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        log = self.output / result['log']
        before = log.read_bytes()
        self.assertEqual(result['log_sha256'], hashlib.sha256(before).hexdigest())
        time.sleep(.1)
        self.assertEqual(log.read_bytes(), before)

    def test_timeout_kills_and_reaps_term_resistant_descendants_before_hash(self):
        result = check_project.run_gate('timeout_fixture', self.root, self.command('wait'),
                                        self.output, timeout=.35, cleanup_grace=.05)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['returncode'], -signal.SIGKILL)
        self.assert_reaped_and_stable(result)

    def test_success_cleans_detached_descendant_and_preserves_unrelated_child(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(.6);raise SystemExit(23)'])
        self.addCleanup(lambda: unrelated.poll() is None and unrelated.kill())
        result = check_project.run_gate('success_fixture', self.root, self.command('success', True),
                                        self.output, timeout=3, cleanup_grace=.05)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['returncode'], 0)
        self.assert_reaped_and_stable(result)
        self.assertEqual(unrelated.wait(timeout=3), 23)

    def test_failure_also_reaps_descendants_before_hash(self):
        result = check_project.run_gate('failure_fixture', self.root, self.command('failure'),
                                        self.output, timeout=3, cleanup_grace=.05)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['returncode'], 7)
        self.assert_reaped_and_stable(result)

    def test_wait_exception_still_cleans_a_live_process_tree(self):
        def fail_wait(_proc, timeout):
            deadline = time.monotonic() + timeout
            while not self.pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.005)
            raise RuntimeError('injected parent wait failure')

        with patch.object(check_project.subprocess.Popen, 'wait', fail_wait):
            result = check_project.run_gate('exception_fixture', self.root, self.command('wait', True),
                                            self.output, timeout=3, cleanup_grace=.05)
        self.assertEqual(result['status'], 'error')
        self.assertIn('injected parent wait failure', result['error'])
        self.assert_reaped_and_stable(result)

    def test_interrupt_returns_a_failed_gate_after_cleanup(self):
        original_wait = subprocess.Popen.wait
        timers = []

        def interrupt_wait(_proc, timeout):
            result = original_wait(_proc, timeout=timeout)
            timer = threading.Timer(.03, lambda: os.kill(os.getpid(), signal.SIGINT))
            timers.append(timer)
            timer.start()
            return result

        try:
            with patch.object(check_project.subprocess.Popen, 'wait', interrupt_wait):
                result = check_project.run_gate('interrupt_fixture', self.root, self.command('success'),
                                                self.output, timeout=3, cleanup_grace=.2)
        finally:
            for timer in timers:
                timer.cancel()
                timer.join(timeout=2)
        self.assertEqual(result['status'], 'interrupted')
        self.assert_reaped_and_stable(result)

    def test_empty_unittest_discovery_is_not_passed(self):
        empty = self.root / 'empty-tests'
        empty.mkdir()
        # The research test script returns wasSuccessful(), which is True for
        # an empty suite, even on Python versions whose unittest CLI exits 5.
        command = ('import sys,unittest; '
                   'suite=unittest.defaultTestLoader.discover(sys.argv[1]); '
                   'result=unittest.TextTestRunner().run(suite); '
                   'raise SystemExit(0 if result.wasSuccessful() else 1)')
        result = check_project.run_gate('empty_fixture', self.root,
                                        [sys.executable, '-c', command, str(empty)],
                                        self.output, timeout=3, require_tests=True)
        self.assertEqual(result['returncode'], 0)
        self.assertEqual(result['tests_run'], 0)
        self.assertEqual(result['status'], 'no_tests')

    def test_test_gate_without_unittest_summary_is_not_passed(self):
        result = check_project.run_gate('missing_fixture', self.root,
                                        [sys.executable, '-c', 'print("no tests executed")'],
                                        self.output, timeout=3, require_tests=True)
        self.assertEqual(result['status'], 'test_summary_missing')

    def test_inherited_evidence_paths_are_redirected_and_archives_unchanged(self):
        archive = self.root / 'historical_evidence'
        archive.mkdir()
        (archive / 'test_fault.json').write_text('retain historical research evidence')
        old_report = archive / 'scene.json'
        old_report.write_text('retain historical integration evidence')
        before = {p.name: p.read_bytes() for p in archive.iterdir()}
        overrides = {name: str(old_report) for name in check_project.REPORT_ENVIRONMENT}
        overrides['CERES_CVRPLIB_TEST_LOG_DIR'] = str(archive)
        body = '''import os
from pathlib import Path
directory=os.environ.get("CERES_CVRPLIB_TEST_LOG_DIR")
if directory:
 root=Path(directory);root.mkdir(parents=True,exist_ok=True)
 (root/"test_fault.json").write_text("new synthetic research evidence")
report=os.environ.get("ROUTE_SCENE_INTEGRATION_REPORT")
if report:Path(report).write_text("new synthetic integration evidence")
assert "MATURE_INTEGRATION_REPORT" not in os.environ
assert "ROUTE_INTEGRATION_DB" not in os.environ
'''
        with patch.dict(os.environ, overrides):
            for name in ('research', 'backend_route_contracts'):
                result = check_project.run_gate(name, self.root, [sys.executable, '-c', body],
                                                self.output, timeout=3)
                self.assertEqual(result['status'], 'passed', result)
        self.assertEqual({p.name: p.read_bytes() for p in archive.iterdir()}, before)
        self.assertTrue((self.output / 'research_worker_evidence/test_fault.json').is_file())
        self.assertEqual((self.output / 'backend_route_scene.json').read_text(),
                         'new synthetic integration evidence')


if __name__ == '__main__':
    unittest.main()
