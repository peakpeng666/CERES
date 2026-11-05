"""Pure finite-output diagnostics; subprocesses run tiny synthetic suites only."""
import copy
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

HELPER = Path(__file__).resolve().parents[1] / 'helper_test_reporter.py'
SPEC = importlib.util.spec_from_file_location('bounded_helper_reporter', HELPER)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
IDENTITY = 'test_fixture.Fixture.test_failure'
IDENTITIES = json.dumps({'schema': 1, 'ids': [IDENTITY]}).encode()
SECRET = 'SECRET_TOKEN_https_private_invalid_user_1001'


def manifest(*ids):
    return json.dumps({'schema': 1, 'ids': sorted(ids or (IDENTITY,))}).encode()


def failure_receipt(**changes):
    row = M._empty('TEST_RUN')
    row.update(tests_run=1, failures=1, test_run_completion='COMPLETE',
               details=[{'test': IDENTITY, 'outcome': 'FAILURE', 'category': 'ASSERTION'}])
    row.update(changes)
    return row


def invoke(root, source, raw=IDENTITIES, stock=False, python_options=()):
    """Never points discovery at this test suite, only ROOT's fixed fixture."""
    tests = root / 'recipe' / 'tests'
    if source is not None:
        tests.mkdir(parents=True, exist_ok=True)
        (tests / 'test_fixture.py').write_text(textwrap.dedent(source))
    if stock:
        argv = [sys.executable, '-I', '-B', '-S', *python_options, '-m', 'unittest', 'discover',
                '-s', str(tests), '-p', 'test_*.py', '-q']
    else:
        script = (
            'import importlib.util,sys\n'
            's=importlib.util.spec_from_file_location("fixture_reporter",' + repr(str(HELPER)) + ')\n'
            'm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n'
            'sys.exit(m.run(' + repr(str(root)) + ',' + repr(raw) + '))\n')
        argv = [sys.executable, '-I', '-B', '-S', *python_options, '-c', script]
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    return subprocess.run(argv, capture_output=True, text=True, timeout=20, env=environment)


class HelperManifestTests(unittest.TestCase):
    def test_held_manifest_exact_sorted_identity_allowlist(self):
        self.assertEqual(M.load_identities(IDENTITIES), frozenset((IDENTITY,)))

    def test_rejects_wrong_manifest_shape_schema_and_scalar_types(self):
        variants = [None, {}, [], {'schema': True, 'ids': [IDENTITY]},
                    {'schema': 2, 'ids': [IDENTITY]}, {'schema': 1, 'ids': IDENTITY},
                    {'schema': 1, 'ids': []}, {'schema': 1, 'ids': [True]},
                    {'schema': 1, 'ids': [IDENTITY], 'extra': SECRET}]
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    M.load_identities(json.dumps(value).encode())

    def test_rejects_duplicates_unsorted_and_duplicate_json_keys(self):
        for raw in (manifest(IDENTITY, IDENTITY),
                    b'{"schema":1,"schema":1,"ids":[]}',
                    json.dumps({'schema': 1, 'ids': ['test_z.Z.test_z', IDENTITY]}).encode()):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    M.load_identities(raw)

    def test_rejects_unbounded_and_malformed_identity_tokens(self):
        for item in ('test_fixture.Fixture.test_a\nSECRET', SECRET, 'https://private.invalid',
                     '/private/token', 'test_fixture.Fixture.test_' + 'a' * 257,
                     'test_fixture.Fixture.test_é', 'test_fixture.Fixture.test_a(secret=1)'):
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    M.load_identities(manifest(item))
        for raw in ('not bytes', bytearray(IDENTITIES), b'', b' ' * (M.MAX_MANIFEST_BYTES + 1)):
            with self.assertRaises(ValueError):
                M.load_identities(raw)

    def test_rejects_manifest_identity_count_limit(self):
        ids = ['test_fixture.Fixture.test_%05d' % n for n in range(M.MAX_IDENTITIES + 1)]
        with self.assertRaises(ValueError):
            M.load_identities(manifest(*ids))


class HelperProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / M.RECEIPT

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, row):
        self.path.write_text(json.dumps(row))

    def project(self):
        return M.project(self.root, IDENTITIES)

    def unknown(self):
        self.assertEqual(self.project(), M._format(M._empty('PRECHECK'), phase='UNKNOWN'))

    def test_missing_receipt_is_precheck_unknown_not_a_failing_test(self):
        value = self.project()
        self.assertTrue(value.startswith(M.PREFIX + ' phase=PRECHECK'))
        self.assertIn('tests_run=0 failures=0 errors=0', value)
        self.assertIn('test=UNKNOWN outcome=UNKNOWN category=UNKNOWN', value)

    def test_exact_manifest_identity_and_finite_exception_are_projected(self):
        self.write(failure_receipt())
        value = self.project()
        self.assertIn('detail1_test=' + IDENTITY, value)
        self.assertIn('detail1_category=ASSERTION', value)

    def test_forged_valid_looking_nonmanifest_id_is_not_projected(self):
        row = failure_receipt()
        row['details'][0]['test'] = 'test_fixture.Fixture.test_' + SECRET
        self.write(row)
        self.unknown()
        self.assertNotIn(SECRET, self.project())

    def test_no_private_identity_message_url_path_or_uid_escapes(self):
        secrets = (SECRET, 'https://private.invalid/token', '/home/private/token',
                   'RuntimeError: password', '1001', 'BAD\nTOKEN')
        for field in ('test', 'outcome', 'category'):
            for secret in secrets:
                with self.subTest(field=field, secret=secret):
                    row = failure_receipt()
                    row['details'][0][field] = secret
                    self.write(row)
                    self.unknown()
                    self.assertNotIn(secret, self.project())
        for field in ('message', 'traceback', 'path', 'uid', 'url'):
            row = failure_receipt()
            row['details'][0][field] = SECRET
            self.write(row)
            self.unknown()

    def test_unknown_exception_name_is_not_an_allowed_category(self):
        row = failure_receipt()
        row['details'][0]['category'] = 'SECRET_CUSTOM_EXCEPTION'
        self.write(row)
        self.unknown()

    def test_pass_receipt_cannot_explain_a_failed_stage(self):
        self.write(failure_receipt(status='PASS'))
        self.unknown()
        self.write(dict(M._empty('TEST_RUN'), status='PASS'))
        self.unknown()

    def test_invalid_status_phase_schema_and_extra_fields_are_unknown(self):
        for field, value in (('status', 'success'), ('status', True), ('phase', SECRET),
                             ('phase', None), ('schema', True), ('schema', 1.0),
                             ('schema', 2), ('extra', SECRET), ('exception_category', SECRET),
                             ('exception_category', True), ('test_run_completion', True),
                             ('test_run_completion', None), ('test_run_completion', 'DONE')):
            with self.subTest(field=field, value=value):
                self.write(failure_receipt(**{field: value}))
                self.unknown()
        for value in ([], None, 1, 'SECRET'):
            self.write(value)
            self.unknown()

    def test_all_numeric_fields_reject_bools_floats_negative_and_overflow(self):
        for field in M.COUNTS + ('truncated',):
            for value in (True, False, -1, 1.0, '1', None, M.MAX_COUNT + 1):
                with self.subTest(field=field, value=value):
                    self.write(failure_receipt(**{field: value}))
                    self.unknown()

    def test_duplicate_nested_and_top_level_keys_fail_closed(self):
        for raw in (b'{"schema":1,"schema":1}',
                    json.dumps(failure_receipt()).replace('"category": "ASSERTION"',
                                                         '"category":"ASSERTION","category":"UNKNOWN"').encode()):
            self.path.write_bytes(raw)
            self.unknown()

    def test_nonfinite_invalid_utf8_and_unbounded_json_fail_closed(self):
        for raw in (b'{', b'\xff', b'NaN', b'Infinity', b' ' * (M.MAX_RECEIPT_BYTES + 1)):
            self.path.write_bytes(raw)
            self.unknown()

    def test_details_count_truncation_and_outcome_totals_must_match(self):
        variants = [failure_receipt(details=[]), failure_receipt(truncated=1),
                    failure_receipt(details='SECRET'), failure_receipt(errors=1),
                    failure_receipt(failures=0, errors=1)]
        for row in variants:
            self.write(row)
            self.unknown()
        row = failure_receipt()
        row['details'][0]['outcome'] = 'UNEXPECTED_SUCCESS'
        row.update(failures=0, unexpected_successes=1)
        self.write(row)
        self.unknown()

    def test_details_never_exceed_eight_and_truncation_is_exact(self):
        row = failure_receipt(tests_run=20, failures=20, truncated=12)
        row['details'] = [copy.deepcopy(row['details'][0]) for _ in range(8)]
        self.write(row)
        self.assertIn('detail8_test=', self.project())
        self.assertNotIn('detail9_', self.project())
        self.assertIn('truncated=12', self.project())
        row['details'].append(copy.deepcopy(row['details'][0]))
        row['truncated'] = 11
        self.write(row)
        self.unknown()

    def test_precheck_cannot_claim_tests_or_failures(self):
        self.write(failure_receipt(phase='PRECHECK'))
        self.unknown()

    def test_discovery_fatal_without_result_has_fixed_import_unknown(self):
        self.write(M._empty('DISCOVERY'))
        value = self.project()
        self.assertIn('phase=DISCOVERY', value)
        self.assertIn('test=UNKNOWN outcome=UNKNOWN category=IMPORT', value)

    def test_symlink_receipt_is_rejected(self):
        target = self.root / 'target'
        target.write_text(json.dumps(failure_receipt()))
        self.path.symlink_to(target)
        self.unknown()

    def test_hardlinked_receipt_is_rejected(self):
        target = self.root / 'target'
        target.write_text(json.dumps(failure_receipt()))
        os.link(target, self.path)
        self.unknown()

    def test_fifo_and_directory_receipts_are_rejected_without_blocking(self):
        os.mkfifo(self.path)
        self.unknown()
        self.path.unlink()
        self.path.mkdir()
        self.unknown()

    def test_symlinked_ancestor_is_rejected(self):
        real = self.root / 'real'
        real.mkdir()
        (real / M.RECEIPT).write_text(json.dumps(failure_receipt()))
        link = self.root / 'link'
        link.symlink_to(real, target_is_directory=True)
        result = M.project(link, IDENTITIES)
        self.assertIn('phase=UNKNOWN', result)
        self.assertNotIn(IDENTITY, result)

    def test_changed_file_during_read_is_rejected(self):
        self.write(failure_receipt())
        original_read = M.os.read
        changed = [False]
        def change(fd, size):
            result = original_read(fd, size)
            if not changed[0]:
                changed[0] = True
                self.path.write_text(json.dumps(M._empty('PRECHECK')))
            return result
        with mock.patch.object(M.os, 'read', side_effect=change):
            self.unknown()

    def test_replaced_leaf_during_read_is_rejected(self):
        self.write(failure_receipt())
        original_read = M.os.read
        changed = [False]
        def change(fd, size):
            result = original_read(fd, size)
            if not changed[0]:
                changed[0] = True
                replacement = self.root / 'replacement'
                replacement.write_text(json.dumps(failure_receipt()))
                os.replace(replacement, self.path)
            return result
        with mock.patch.object(M.os, 'read', side_effect=change):
            self.unknown()

    def test_invalid_manifest_never_falls_back_to_regex_only_disclosure(self):
        self.write(failure_receipt())
        for raw in (None, b'{}', manifest('test_other.Other.test_other')):
            result = M.project(self.root, raw)
            self.assertIn('phase=UNKNOWN', result)
            self.assertNotIn(IDENTITY, result)

    def test_project_does_not_reopen_manifest_path(self):
        inputs = self.root / 'inputs'
        inputs.mkdir()
        (inputs / 'helper-test-identities.json').write_text(SECRET)
        self.write(failure_receipt())
        self.assertIn('detail1_test=' + IDENTITY, self.project())


class HelperExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def receipt(self):
        return json.loads((self.root / M.RECEIPT).read_bytes())

    def test_passing_run_keeps_native_exit_and_pass_receipt(self):
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_failure(self): self.assertTrue(True)
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.receipt()['status'], 'PASS')
        self.assertEqual(self.receipt()['test_run_completion'], 'COMPLETE')
        self.assertEqual(self.receipt()['tests_run'], 1)

    def test_assertion_failure_has_exact_known_parent_identity(self):
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_failure(self): self.fail('SECRET_TOKEN')
        ''')
        self.assertEqual(result.returncode, 1)
        row = self.receipt()
        self.assertEqual(row['phase'], 'TEST_RUN')
        self.assertEqual(row['test_run_completion'], 'COMPLETE')
        self.assertEqual(row['status'], 'FAIL')
        self.assertEqual(row['details'], [{'test': IDENTITY, 'outcome': 'FAILURE', 'category': 'ASSERTION'}])
        self.assertNotIn('SECRET_TOKEN', json.dumps(row))
        self.assertIn('SECRET_TOKEN', result.stderr)

    def test_subtests_use_parent_identity_never_parameters(self):
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_failure(self):
                    with self.subTest(secret='SUBTEST_SECRET_URL'):
                        self.fail('PRIVATE_MESSAGE')
                    with self.subTest(secret='SECOND_SECRET'):
                        raise KeyError('PRIVATE_KEY')
        ''')
        self.assertEqual(result.returncode, 1)
        row = self.receipt()
        self.assertEqual((row['tests_run'], row['failures'], row['errors']), (1, 1, 1))
        self.assertEqual([d['test'] for d in row['details']], [IDENTITY, IDENTITY])
        public = M.project(self.root, IDENTITIES)
        for secret in ('SUBTEST_SECRET_URL', 'PRIVATE_MESSAGE', 'SECOND_SECRET', 'PRIVATE_KEY'):
            self.assertNotIn(secret, public)
        self.assertIn('SUBTEST_SECRET_URL', result.stderr)

    def test_unknown_exception_class_is_redacted_to_unknown(self):
        result = invoke(self.root, '''
            import unittest
            class SECRET_CUSTOM_EXCEPTION(Exception): pass
            class Fixture(unittest.TestCase):
                def test_failure(self): raise SECRET_CUSTOM_EXCEPTION('PRIVATE_SECRET')
        ''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.receipt()['details'][0]['category'], 'UNKNOWN')
        self.assertNotIn('SECRET', M.project(self.root, IDENTITIES))

    def test_unlisted_discovered_test_still_runs_but_identity_is_unknown(self):
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_SECRET_UNKNOWN_METHOD(self):
                    print('UNLISTED_TEST_EXECUTED')
                    self.fail('SECRET')
        ''')
        self.assertEqual(result.returncode, 1)
        self.assertIn('UNLISTED_TEST_EXECUTED', result.stdout)
        self.assertEqual(self.receipt()['tests_run'], 1)
        self.assertEqual(self.receipt()['details'][0]['test'], 'UNKNOWN')
        self.assertNotIn('SECRET', M.project(self.root, IDENTITIES))

    def test_overridden_id_cannot_inject_private_data(self):
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def id(self): return 'SECRET_OVERRIDE'
                def test_failure(self): self.fail('SECRET')
        ''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.receipt()['details'][0]['test'], IDENTITY)
        self.assertNotIn('SECRET', M.project(self.root, IDENTITIES))

    def test_import_placeholder_is_discovery_import_unknown(self):
        result = invoke(self.root, "raise RuntimeError('SECRET_IMPORT_MODULE')\n")
        self.assertEqual(result.returncode, 1)
        row = self.receipt()
        self.assertEqual((row['phase'], row['tests_run'], row['errors']), ('DISCOVERY', 1, 1))
        self.assertEqual(row['details'], [{'test': 'UNKNOWN', 'outcome': 'ERROR', 'category': 'IMPORT'}])
        self.assertNotIn('SECRET', M.project(self.root, IDENTITIES))

    def test_import_failure_does_not_prevent_other_tests_running(self):
        tests = self.root / 'recipe' / 'tests'
        tests.mkdir(parents=True)
        (tests / 'test_broken.py').write_text("raise ImportError('SECRET_IMPORT')\n")
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_failure(self): print('NORMAL_TEST_EXECUTED')
        ''')
        self.assertEqual(result.returncode, 1)
        self.assertIn('NORMAL_TEST_EXECUTED', result.stdout)
        self.assertEqual(self.receipt()['tests_run'], 2)
        self.assertEqual(self.receipt()['phase'], 'DISCOVERY')

    def test_load_tests_hook_failure_preserves_discovery_semantics(self):
        result = invoke(self.root, '''
            def load_tests(loader, tests, pattern):
                raise ValueError('SECRET_LOAD_TESTS')
        ''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.receipt()['phase'], 'DISCOVERY')
        self.assertEqual(self.receipt()['details'][0]['category'], 'IMPORT')

    def test_native_cli_warning_output_outcomes_and_exit_are_preserved(self):
        source = '''
            import unittest, warnings
            class Fixture(unittest.TestCase):
                def test_failure(self):
                    print('PRIVATE_STDOUT')
                    warnings.warn('VISIBLE_DEFAULT_DEPRECATION', DeprecationWarning)
                    self.fail('EXPECTED_PRIVATE_FAILURE')
                @unittest.skip('PRIVATE_SKIP_REASON')
                def test_skip(self): pass
                @unittest.expectedFailure
                def test_expected(self): self.fail('PRIVATE_EXPECTED')
                @unittest.expectedFailure
                def test_unexpected(self): pass
        '''
        actual = invoke(self.root, source)
        stock = invoke(self.root, source, stock=True)
        normalize = lambda value: re.sub(r'in [0-9.]+s', 'in TIME', value)
        self.assertEqual(actual.returncode, stock.returncode)
        self.assertEqual(actual.stdout, stock.stdout)
        self.assertEqual(normalize(actual.stderr), normalize(stock.stderr))
        row = self.receipt()
        self.assertEqual([row[k] for k in M.COUNTS], [4, 1, 0, 1, 1, 1])
        self.assertEqual(row['details'][1]['outcome'], 'UNEXPECTED_SUCCESS')
        self.assertEqual(row['details'][1]['category'], 'UNKNOWN')

    def test_explicit_python_warning_options_are_preserved(self):
        source = '''
            import unittest, warnings
            class Fixture(unittest.TestCase):
                def test_failure(self): warnings.warn('HIDDEN_WARNING', DeprecationWarning)
        '''
        actual = invoke(self.root, source, python_options=('-W', 'ignore'))
        stock = invoke(self.root, source, stock=True, python_options=('-W', 'ignore'))
        self.assertEqual(actual.returncode, stock.returncode)
        self.assertNotIn('HIDDEN_WARNING', actual.stderr)
        self.assertNotIn('HIDDEN_WARNING', stock.stderr)

    def test_multiple_subtest_errors_have_bounded_details_and_exact_truncation(self):
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_failure(self):
                    for n in range(21):
                        with self.subTest(secret='SECRET', n=n):
                            raise ValueError('PRIVATE')
        ''')
        self.assertEqual(result.returncode, 1)
        row = self.receipt()
        self.assertEqual((row['tests_run'], row['errors'], len(row['details']), row['truncated']),
                         (1, 21, 8, 13))
        public = M.project(self.root, IDENTITIES)
        self.assertIn('truncated=13', public)
        self.assertNotIn('SECRET', public)
        self.assertLess(len(public), 4096)

    def test_missing_discovery_directory_is_not_mislabeled_precheck(self):
        actual = invoke(self.root, None)
        stock = invoke(self.root, None, stock=True)
        self.assertEqual(actual.returncode, stock.returncode)
        self.assertNotEqual(actual.returncode, 0)
        self.assertEqual(self.receipt()['phase'], 'DISCOVERY')
        self.assertEqual(self.receipt()['tests_run'], 0)
        self.assertIn('category=IMPORT', M.project(self.root, IDENTITIES))

    def test_invalid_manifest_stays_precheck_and_does_not_execute_tests(self):
        result = invoke(self.root, "print('MUST_NOT_EXECUTE')", raw=b'{}')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('MUST_NOT_EXECUTE', result.stdout)
        self.assertEqual(self.receipt(), dict(M._empty('PRECHECK'), exception_category='VALUE'))

    def test_empty_suite_preserves_interpreter_specific_exit_status(self):
        actual = invoke(self.root, '# no tests\n')
        stock = invoke(self.root, '# no tests\n', stock=True)
        self.assertEqual(actual.returncode, stock.returncode)
        self.assertEqual(self.receipt()['tests_run'], 0)
        self.assertEqual(self.receipt()['phase'], 'TEST_RUN')

    def test_discovery_system_exit_preserves_exact_native_exit(self):
        actual = invoke(self.root, "raise SystemExit(7)\n")
        stock = invoke(self.root, "raise SystemExit(7)\n", stock=True)
        self.assertEqual(actual.returncode, stock.returncode)
        self.assertNotEqual(actual.returncode, 0)
        self.assertEqual(self.receipt()['phase'], 'DISCOVERY')
        self.assertEqual(self.receipt()['details'][0]['category'], 'IMPORT')

    def test_keyboard_interrupt_remains_native_interrupt_not_exit_one(self):
        source = '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_failure(self): raise KeyboardInterrupt('SECRET_INTERRUPT')
        '''
        actual = invoke(self.root, source)
        stock = invoke(self.root, source, stock=True)
        self.assertEqual(actual.returncode, stock.returncode)
        self.assertNotEqual(actual.returncode, 1)
        self.assertEqual(self.receipt()['phase'], 'TEST_RUN')
        self.assertEqual(self.receipt()['test_run_completion'], 'INCOMPLETE')
        self.assertNotIn('SECRET', M.project(self.root, IDENTITIES))

    def test_test_system_exit_is_an_error_not_a_process_success(self):
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_failure(self): raise SystemExit(0)
        ''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.receipt()['details'][0]['category'], 'SYSTEM_EXIT')

    def test_class_fixture_error_has_no_synthetic_test_identity(self):
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                @classmethod
                def setUpClass(cls): raise AttributeError('SECRET_FIXTURE')
                def test_failure(self): pass
        ''')
        self.assertNotEqual(result.returncode, 0)
        row = self.receipt()
        self.assertEqual((row['tests_run'], row['errors']), (0, 1))
        self.assertEqual(row['details'][0]['test'], 'UNKNOWN')
        self.assertEqual(row['details'][0]['category'], 'ATTRIBUTE')

    def test_optimized_execution_retains_finite_redaction(self):
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_failure(self): self.fail('SECRET_OPTIMIZED')
        ''', python_options=('-O',))
        self.assertEqual(result.returncode, 1)
        self.assertIn('detail1_test=' + IDENTITY, M.project(self.root, IDENTITIES))
        self.assertNotIn('SECRET_OPTIMIZED', M.project(self.root, IDENTITIES))

    def test_receipt_writer_will_not_follow_existing_symlink(self):
        target = self.root / 'target'
        target.write_text('KEEP_PRIVATE_TARGET')
        (self.root / M.RECEIPT).symlink_to(target)
        result = invoke(self.root, '''
            import unittest
            class Fixture(unittest.TestCase):
                def test_failure(self): self.fail('PRIVATE')
        ''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(target.read_text(), 'KEEP_PRIVATE_TARGET')
        self.assertIn('phase=UNKNOWN', M.project(self.root, IDENTITIES))

    def test_boolean_system_exit_codes_keep_native_zero_and_one(self):
        for code in (False, True):
            with self.subTest(code=code):
                with mock.patch.object(unittest.TestProgram, 'runTests', side_effect=SystemExit(code)):
                    with mock.patch.object(unittest.TestLoader, 'discover', return_value=unittest.TestSuite()):
                        with contextlib.redirect_stderr(io.StringIO()):
                            result = M.run(self.root, IDENTITIES)
                self.assertEqual(result, int(code))

    def test_escaping_discovery_type_error_has_finite_category_without_test(self):
        with mock.patch.object(unittest.TestLoader, 'discover', side_effect=TypeError('SECRET_API_MISMATCH')):
            with self.assertRaises(TypeError):
                with contextlib.redirect_stderr(io.StringIO()):
                    M.run(self.root, IDENTITIES)
        row = self.receipt()
        self.assertEqual((row['phase'], row['exception_category']), ('DISCOVERY', 'TYPE'))
        self.assertEqual(row['tests_run'], 0)
        self.assertEqual(row['test_run_completion'], 'INCOMPLETE')
        self.assertEqual(row['details'], [])
        value = M.project(self.root, IDENTITIES)
        self.assertIn('exception_category=TYPE', value)
        self.assertNotIn('SECRET', value)

    def test_truncated_late_import_still_marks_discovery(self):
        state = M._State(self.root, M.load_identities(IDENTITIES))
        state.phase = 'TEST_RUN'
        test = unittest.FunctionTestCase(lambda: None)
        for _ in range(10):
            state.record(test, 'ERROR', (ValueError, ValueError('SECRET'), None))
        failed = unittest.loader._FailedTest('SECRET_MODULE', ImportError('SECRET'))
        state.record(failed, 'ERROR', (ImportError, ImportError('SECRET'), None))
        self.assertTrue(state.discovery_failed)
        self.assertEqual(state.receipt()['phase'], 'DISCOVERY')
        self.assertEqual(len(state.details), 8)

    def test_subprocess_fixtures_are_isolated_and_never_write_bytecode(self):
        hooks = self.root / 'inherited-hooks'
        hooks.mkdir()
        marker = self.root / 'hook-executed'
        hook_source = 'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("BAD_HOOK")\n'
        for name in ('sitecustomize.py', 'usercustomize.py', 'inherited_hook.py'):
            (hooks / name).write_text(hook_source)
        cache = HELPER.parent / '__pycache__'
        def helper_bytecode():
            return {str(path): (path.stat().st_mtime_ns, path.read_bytes())
                    for path in cache.glob('helper_test_reporter*.pyc')}
        before = helper_bytecode()
        source = """
            import importlib.util, os, sys, unittest
            class Fixture(unittest.TestCase):
                def test_failure(self):
                    self.assertEqual(sys.flags.isolated, 1)
                    self.assertEqual(sys.flags.no_site, 1)
                    self.assertEqual(sys.flags.dont_write_bytecode, 1)
                    self.assertNotIn('site', sys.modules)
                    self.assertIsNone(importlib.util.find_spec('inherited_hook'))
                    self.assertEqual(dict(os.environ), {
                        'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
                    print('ISOLATED_FIXTURE_PASS')
        """
        poisoned = {'PYTHONPATH': str(hooks), 'PYTHONHOME': str(hooks),
                    'PYTHONUSERBASE': str(hooks), 'PRIVATE_INHERITED_TOKEN': 'SECRET_ENV'}
        with mock.patch.dict(os.environ, poisoned):
            for stock in (False, True):
                with self.subTest(stock=stock):
                    result = invoke(self.root, source, stock=stock)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('ISOLATED_FIXTURE_PASS', result.stdout)
                    self.assertFalse(marker.exists())
                    self.assertEqual(list(self.root.rglob('*.pyc')), [])
                    self.assertEqual(list(self.root.rglob('__pycache__')), [])
                    self.assertEqual(helper_bytecode(), before)

    def test_terminated_fixture_keeps_an_explicit_incomplete_progress_snapshot(self):
        result = invoke(self.root, """
            import json, os, unittest
            from pathlib import Path
            class Fixture(unittest.TestCase):
                def test_failure(self):
                    root = Path(__file__).parents[2]
                    progress = json.loads((root / 'helper-tests-status.json').read_bytes())
                    self.assertEqual(progress['phase'], 'TEST_RUN')
                    self.assertEqual(progress['test_run_completion'], 'INCOMPLETE')
                    self.assertEqual(progress['tests_run'], 0)
                    print('VERIFIED_INCOMPLETE_PROGRESS', flush=True)
                    os._exit(17)
        """)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertIn('VERIFIED_INCOMPLETE_PROGRESS', result.stdout)
        row = self.receipt()
        self.assertEqual((row['phase'], row['status'], row['test_run_completion']),
                         ('TEST_RUN', 'FAIL', 'INCOMPLETE'))
        self.assertEqual(row['tests_run'], 0)
        self.assertEqual(row['details'], [])
        public = M.project(self.root, IDENTITIES)
        self.assertIn('test_run_completion=INCOMPLETE', public)
        self.assertIn('test=UNKNOWN outcome=UNKNOWN', public)

    def test_completion_is_not_inferred_from_zero_counts_or_status(self):
        state = M._State(self.root, M.load_identities(IDENTITIES))
        state.phase = 'TEST_RUN'
        incomplete = state.receipt()
        state.completed = True
        complete = state.receipt()
        self.assertEqual(incomplete['test_run_completion'], 'INCOMPLETE')
        self.assertEqual(complete['test_run_completion'], 'COMPLETE')
        self.assertEqual(incomplete['status'], complete['status'])
        self.assertEqual([incomplete[name] for name in M.COUNTS],
                         [complete[name] for name in M.COUNTS])

    def test_completed_discovery_failure_still_reports_runner_completion(self):
        result = invoke(self.root, "raise ImportError('PRIVATE_IMPORT')\n")
        self.assertNotEqual(result.returncode, 0)
        row = self.receipt()
        self.assertEqual((row['phase'], row['test_run_completion']), ('DISCOVERY', 'COMPLETE'))
        self.assertIn('test_run_completion=COMPLETE', M.project(self.root, IDENTITIES))

    def test_precheck_complete_and_incomplete_pass_receipts_fail_closed(self):
        path = self.root / M.RECEIPT
        for row in (dict(M._empty('PRECHECK'), test_run_completion='COMPLETE'),
                    dict(M._empty('TEST_RUN'), status='PASS')):
            with self.subTest(row=row):
                path.write_text(json.dumps(row))
                public = M.project(self.root, IDENTITIES)
                self.assertIn('phase=UNKNOWN', public)
                self.assertIn('test_run_completion=INCOMPLETE', public)
