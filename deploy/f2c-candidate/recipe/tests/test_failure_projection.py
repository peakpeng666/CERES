"""Standard-library fixtures only; no native candidate execution or disclosure."""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

RECIPE = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('project_failure_code', RECIPE / 'project_failure_code.py')
projector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projector)

class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='projection-', dir=RECIPE / 'tests')
        self.root = pathlib.Path(self.temp.name)
        self.path = self.root / 'private.log'

    def tearDown(self):
        self.temp.cleanup()

    def log(self, data):
        self.path.write_bytes(data.encode() if isinstance(data, str) else data)
        return str(self.path)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-I', '-B', str(RECIPE / 'project_failure_code.py'), *args],
                              capture_output=True, text=True, check=False, timeout=5)

    def assert_safe_cli(self, result, code):
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, code + '\n')
        self.assertEqual(result.stderr, '')
        self.assertIn(result.stdout.strip(), projector.ALLOWED_CODES)

    def test_every_static_rule_outputs_only_its_allowlisted_code(self):
        for stage, rules in projector.RULES.items():
            for code, exact, prefixes in rules:
                for message in exact + tuple(x + 'PRIVATE_TOKEN /private/path https://private.example' for x in prefixes):
                    with self.subTest(stage=stage, code=code, message=message):
                        path = self.log('RuntimeError: ' + message + '\n')
                        self.assertEqual(projector.project(stage, path), code)

    def test_secret_path_url_are_never_echoed(self):
        path = self.log('credential=SECRET_TOKEN\nBLOCKED: Artifact hash/size mismatch: https://private.example/SECRET_TOKEN\n')
        self.assert_safe_cli(self.cli('--stage', 'packages', '--log', path), 'APT_ARCHIVE_MISMATCH')

    def test_unknown_and_json_logs_are_not_interpreted(self):
        for message in ('SECRET_TOKEN /private/path https://private.example\n',
                        '{"error": "BLOCKED: APT simulation proposes removal", "secret": "SECRET_TOKEN"}\n'):
            self.assert_safe_cli(self.cli('--stage', 'packages', '--log', self.log(message)), 'UNKNOWN')

    def test_non_utf8_and_control_sequences_refused_without_disclosure(self):
        for data in (b'\xffSECRET\nBLOCKED: APT simulation proposes removal\n',
                     b'\x1b[31mBLOCKED: APT simulation proposes removal\n',
                     b'\x00BLOCKED: APT simulation proposes removal\n'):
            self.assert_safe_cli(self.cli('--stage', 'packages', '--log', self.log(data)), 'UNKNOWN')

    def test_invalid_cli_never_prints_arguments_or_usage(self):
        for args in ((), ('--help',), ('--stage', 'SECRET_STAGE', '--log', '/private/SECRET_PATH'),
                     ('--stage', 'packages', '--stage', 'SECRET'), ('--unknown', 'SECRET', '--log', '/private/SECRET')):
            self.assert_safe_cli(self.cli(*args), 'UNKNOWN')

    def test_missing_log_is_unknown(self):
        self.assert_safe_cli(self.cli('--stage', 'packages', '--log', str(self.root / 'SECRET_MISSING')), 'UNKNOWN')

    def test_final_symlink_and_ancestor_symlink_refused(self):
        self.log('BLOCKED: APT simulation proposes removal\n')
        alias = self.root / 'alias'
        alias.symlink_to(self.path)
        self.assertEqual(projector.project('packages', str(alias)), 'UNKNOWN')
        real = self.root / 'directory'
        real.mkdir()
        (real / 'log').write_text('BLOCKED: APT simulation proposes removal\n')
        directory_alias = self.root / 'directory-alias'
        directory_alias.symlink_to(real, target_is_directory=True)
        self.assertEqual(projector.project('packages', str(directory_alias / 'log')), 'UNKNOWN')

    def test_hardlink_directory_and_fifo_are_unknown_without_blocking(self):
        self.log('BLOCKED: APT simulation proposes removal\n')
        hardlink = self.root / 'hardlink'
        os.link(self.path, hardlink)
        self.assertEqual(projector.project('packages', str(hardlink)), 'UNKNOWN')
        self.assertEqual(projector.project('packages', str(self.root)), 'UNKNOWN')
        fifo = self.root / 'pipe'
        os.mkfifo(fifo)
        self.assert_safe_cli(self.cli('--stage', 'packages', '--log', str(fifo)), 'UNKNOWN')

    def test_large_file_reads_at_most_last_64k(self):
        path = self.log(b'SECRET' * projector.MAX_READ_BYTES + b'\nBLOCKED: APT simulation proposes removal\n')
        original = os.pread
        with mock.patch.object(projector.os, 'pread', wraps=original) as read:
            self.assertEqual(projector.project('packages', path), 'APT_REMOVAL_REFUSED')
        self.assertEqual(read.call_count, 1)
        self.assertLessEqual(read.call_args.args[1], projector.MAX_READ_BYTES)

    def test_error_outside_tail_or_partial_tail_record_not_used(self):
        path = self.log(b'BLOCKED: APT simulation proposes removal\n' + b'x' * (projector.MAX_READ_BYTES + 100))
        self.assertEqual(projector.project('packages', path), 'UNKNOWN')

    def test_stage_specific_and_latest_unknown_error_remains_unknown(self):
        path = self.log('BLOCKED: APT simulation proposes removal\n')
        self.assertEqual(projector.project('native_validation', path), 'UNKNOWN')
        path = self.log('BLOCKED: APT simulation proposes removal\nRuntimeError: SECRET_UNKNOWN\n')
        self.assertEqual(projector.project('packages', path), 'UNKNOWN')

    def test_unknown_suffix_is_not_a_static_error_match(self):
        path = self.log('BLOCKED: APT simulation proposes removal SECRET\n')
        self.assertEqual(projector.project('packages', path), 'UNKNOWN')

    def test_exact_unshare_errors_are_stage_bound(self):
        for message in projector.UNSHARE_ERRORS:
            path = self.log(message + '\n')
            self.assertEqual(projector.project('offline_build', path), 'GUARD_NAMESPACE_REJECTED')
            self.assertEqual(projector.project('packages', path), 'UNKNOWN')

    def test_helper_test_summary_is_bounded_and_not_echoed(self):
        path = self.log('secret trace\nFAILED (failures=1, errors=2)\n')
        self.assert_safe_cli(self.cli('--stage', 'helper_tests', '--log', path), 'HELPER_TESTS_FAILED')

    def test_exception_in_reader_is_unknown_without_message(self):
        with mock.patch.object(projector.os, 'open', side_effect=OSError('SECRET_ERROR /private/path')):
            self.assertIsNone(projector.safe_tail('/safe/log'))

    def test_changing_file_is_refused(self):
        self.log('BLOCKED: APT simulation proposes removal\n')
        before = os.stat(self.path)
        after = mock.Mock(st_dev=before.st_dev, st_ino=before.st_ino, st_size=before.st_size + 1,
                          st_mtime_ns=before.st_mtime_ns, st_ctime_ns=before.st_ctime_ns)
        with mock.patch.object(projector.os, 'fstat', side_effect=[before, after]):
            self.assertEqual(projector.project('packages', str(self.path)), 'UNKNOWN')

    def test_all_public_codes_are_fixed_ascii_tokens(self):
        for code in projector.ALLOWED_CODES:
            self.assertRegex(code, r'^[A-Z][A-Z_]+$')
        self.assertLessEqual(max(map(len, projector.ALLOWED_CODES)), 64)

if __name__ == '__main__':
    unittest.main(verbosity=2)
