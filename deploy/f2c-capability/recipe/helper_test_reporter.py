#!/usr/bin/env python3
"""Stock unittest discovery with a separate, finite diagnostic receipt.

The caller must supply already source-index-verified manifest bytes, and keep
all stdout/stderr private. The manifest only permits public identity disclosure;
it never selects, filters, skips, or changes tests. This module is not an
authentication check and does not invoke any capability helper.
"""
import json
import os
from pathlib import Path
import re
import stat
import sys
import traceback
import unittest

RECEIPT = 'helper-tests-status.json'
MAX_IDENTITIES = 4096
MAX_ID_LENGTH = 256
MAX_MANIFEST_BYTES = 1048576
MAX_RECEIPT_BYTES = 16384
MAX_COUNT = 1000000
MAX_DETAILS = 8
PHASES = frozenset(('PRECHECK', 'DISCOVERY', 'TEST_RUN'))
CATEGORIES = frozenset(('UNKNOWN', 'EXCEPTION', 'ASSERTION', 'IMPORT',
                        'ATTRIBUTE', 'TYPE', 'VALUE', 'OS', 'SYSTEM_EXIT', 'KEY'))
OUTCOMES = frozenset(('FAILURE', 'ERROR', 'UNEXPECTED_SUCCESS'))
COUNTS = ('tests_run', 'failures', 'errors', 'skipped', 'expected_failures',
          'unexpected_successes')
PREFIX = 'stage=helper_tests status=FAIL code=HELPER_TESTS_FAILED'
_ID = re.compile(r'test_[A-Za-z0-9_]+\.[A-Za-z_][A-Za-z0-9_]*\.test_[A-Za-z0-9_]+\Z')
_METADATA = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size',
             'st_mtime_ns', 'st_ctime_ns')
_EXCEPTION_CATEGORIES = {
    Exception: 'EXCEPTION', RuntimeError: 'EXCEPTION',
    AssertionError: 'ASSERTION', ImportError: 'IMPORT',
    ModuleNotFoundError: 'IMPORT', AttributeError: 'ATTRIBUTE',
    TypeError: 'TYPE', ValueError: 'VALUE', OSError: 'OS',
    FileNotFoundError: 'OS', FileExistsError: 'OS', PermissionError: 'OS',
    NotADirectoryError: 'OS', IsADirectoryError: 'OS',
    SystemExit: 'SYSTEM_EXIT', KeyError: 'KEY',
}


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValueError('Non-finite JSON number')))


def load_identities(raw):
    """Validate held, externally anchored bytes; never reopen the manifest."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_MANIFEST_BYTES:
        raise ValueError('Invalid identity manifest')
    value = _json(raw)
    if (type(value) is not dict or set(value) != {'schema', 'ids'} or
            type(value['schema']) is not int or value['schema'] != 1 or
            type(value['ids']) is not list or
            not 1 <= len(value['ids']) <= MAX_IDENTITIES):
        raise ValueError('Invalid identity manifest')
    ids = value['ids']
    if any(type(item) is not str or not 1 <= len(item) <= MAX_ID_LENGTH or
           _ID.fullmatch(item) is None for item in ids):
        raise ValueError('Invalid identity manifest')
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise ValueError('Invalid identity manifest')
    return frozenset(ids)


def _same(before, after, fields=_METADATA):
    return all(getattr(before, field) == getattr(after, field) for field in fields)


class _Directory:
    """Hold every ancestor open, rejecting symlinks without check/open races."""
    def __init__(self, root):
        self.fds = []
        self.edges = []
        path = Path(root)
        if not path.is_absolute():
            path = Path.cwd() / path
        if '..' in path.parts:
            raise ValueError('Unsafe directory')
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            self.fds.append(os.open('/', flags))
            for part in path.parts[1:]:
                child = os.open(part, flags, dir_fd=self.fds[-1])
                self.edges.append((self.fds[-1], part, os.fstat(child)))
                self.fds.append(child)
            self.fd = self.fds[-1]
        except BaseException:
            self.close()
            raise

    def verify(self):
        for parent, name, before in self.edges:
            after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISDIR(after.st_mode) or
                    not _same(before, after, ('st_dev', 'st_ino', 'st_mode'))):
                raise ValueError('Changed directory')

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds = []


def _read_receipt(root):
    directory = _Directory(root)
    fd = None
    try:
        fd = os.open(RECEIPT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory.fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                not 1 <= before.st_size <= MAX_RECEIPT_BYTES):
            raise ValueError('Unsafe receipt')
        raw = bytearray()
        while len(raw) <= MAX_RECEIPT_BYTES:
            part = os.read(fd, min(4096, MAX_RECEIPT_BYTES + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(fd)
        visible = os.stat(RECEIPT, dir_fd=directory.fd, follow_symlinks=False)
        directory.verify()
        if (len(raw) != before.st_size or len(raw) > MAX_RECEIPT_BYTES or
                not _same(before, after) or not _same(after, visible)):
            raise ValueError('Changed receipt')
        return _json(bytes(raw))
    finally:
        if fd is not None:
            os.close(fd)
        directory.close()


def _write_receipt(root, receipt):
    """Atomic best-effort output, without following any existing file link."""
    raw = (json.dumps(receipt, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError('Oversized receipt')
    directory = _Directory(root)
    temporary = None
    fd = None
    try:
        try:
            before = os.stat(RECEIPT, dir_fd=directory.fd, follow_symlinks=False)
        except FileNotFoundError:
            before = None
        if before is not None and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
            raise ValueError('Unsafe receipt destination')
        # A fixed directory fd and exclusive random leaf avoid following links.
        for _ in range(16):
            temporary = '.helper-tests-' + os.urandom(12).hex()
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory.fd)
                break
            except FileExistsError:
                temporary = None
        if fd is None:
            raise ValueError('Receipt temporary unavailable')
        pending = memoryview(raw)
        while pending:
            size = os.write(fd, pending)
            if size <= 0:
                raise ValueError('Short receipt write')
            pending = pending[size:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        directory.verify()
        os.replace(temporary, RECEIPT, src_dir_fd=directory.fd, dst_dir_fd=directory.fd)
        temporary = None
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            os.unlink(temporary, dir_fd=directory.fd)
        directory.close()


def _empty(phase):
    row = dict(schema=1, status='FAIL', phase=phase,
               test_run_completion='INCOMPLETE', exception_category='UNKNOWN',
               details=[], truncated=0)
    row.update((name, 0) for name in COUNTS)
    return row


def _category(err):
    if type(err) is tuple and len(err) == 3:
        for kind, category in _EXCEPTION_CATEGORIES.items():
            if err[0] is kind:
                return category
    return 'UNKNOWN'


def _identity(test, identities):
    # Do not call overridden id(), and never receive a subtest here. Standard
    # TestCase.id preserves the real parent class/method identity. Fixture
    # _ErrorHolder and discovery _FailedTest are deliberately unnamed.
    if not isinstance(test, unittest.TestCase) or isinstance(test, unittest.loader._FailedTest):
        return 'UNKNOWN'
    try:
        value = unittest.TestCase.id(test)
        if type(value) is str and value in identities:
            return value
    except Exception:
        pass
    return 'UNKNOWN'


class _State:
    def __init__(self, root, identities):
        self.root = root
        self.identities = identities
        self.phase = 'PRECHECK'
        self.discovery_failed = False
        self.completed = False
        self.exception_category = 'UNKNOWN'
        self.result = None
        self.details = []
        self.total_details = 0

    def record(self, test, outcome, err=None):
        self.total_details += 1
        failed_import = isinstance(test, unittest.loader._FailedTest)
        if failed_import:
            self.discovery_failed = True
        if len(self.details) >= MAX_DETAILS:
            return
        self.details.append({'test': 'UNKNOWN' if failed_import else _identity(test, self.identities),
                             'outcome': outcome,
                             'category': 'IMPORT' if failed_import else _category(err)})

    def receipt(self, exit_code=1):
        phase = 'DISCOVERY' if self.discovery_failed else self.phase
        row = _empty(phase)
        completion = 'COMPLETE' if self.completed else 'INCOMPLETE'
        row['test_run_completion'] = completion
        row['exception_category'] = self.exception_category
        result = self.result
        if result is not None:
            row.update(tests_run=result.testsRun, failures=len(result.failures),
                       errors=len(result.errors), skipped=len(result.skipped),
                       expected_failures=len(result.expectedFailures),
                       unexpected_successes=len(result.unexpectedSuccesses))
            if (self.completed and exit_code == 0 and result.wasSuccessful()):
                row['status'] = 'PASS'
        if any(type(row[name]) is not int or not 0 <= row[name] <= MAX_COUNT for name in COUNTS):
            return dict(_empty(phase), test_run_completion=completion)
        if not 0 <= self.total_details <= MAX_COUNT:
            return dict(_empty(phase), test_run_completion=completion)
        row['details'] = list(self.details)
        row['truncated'] = self.total_details - len(self.details)
        return row

    def persist(self, exit_code=1):
        try:
            _write_receipt(self.root, self.receipt(exit_code))
        except Exception:
            # A diagnostic write must not change unittest's result. The caller
            # keeps this traceback private, as it does all unittest output.
            traceback.print_exc()


def run(root, identity_bytes):
    """Run precisely discover -s ROOT/recipe/tests -p test_*.py -q.

    TestProgram, including its warning setup, runs with exit=True. Catching its
    SystemExit preserves native-version exit codes, including no-tests behavior.
    All ordinary unittest output remains unchanged for the caller to redirect.
    """
    state = _State(root, frozenset())
    state.persist()
    try:
        state.identities = load_identities(identity_bytes)
    except Exception:
        state.exception_category = _category(sys.exc_info())
        state.persist()
        traceback.print_exc()
        return 1

    class DiagnosticResult(unittest.TextTestResult):
        def addFailure(self, test, err):
            super().addFailure(test, err)
            state.record(test, 'FAILURE', err)

        def addError(self, test, err):
            super().addError(test, err)
            state.record(test, 'ERROR', err)

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            state.record(test, 'UNEXPECTED_SUCCESS')

        def addSubTest(self, test, subtest, err):
            before_failures = len(self.failures)
            before_errors = len(self.errors)
            super().addSubTest(test, subtest, err)
            if len(self.failures) > before_failures:
                state.record(test, 'FAILURE', err)
            elif len(self.errors) > before_errors:
                state.record(test, 'ERROR', err)

    class DiagnosticRunner(unittest.TextTestRunner):
        resultclass = DiagnosticResult

        def _makeResult(self):
            result = super()._makeResult()
            state.result = result
            return result

        def run(self, test):
            result = super().run(test)
            state.completed = True
            return result

    class DiagnosticProgram(unittest.TestProgram):
        def createTests(self, from_discovery=False, Loader=None):
            state.phase = 'DISCOVERY'
            state.persist()
            super().createTests(from_discovery=from_discovery, Loader=Loader)
            state.discovery_failed = bool(self.testLoader.errors)

        def runTests(self):
            state.phase = 'TEST_RUN'
            state.persist()
            super().runTests()

    exit_code = 1
    try:
        DiagnosticProgram(module=None,
                          argv=['unittest', 'discover', '-s',
                                str(Path(root) / 'recipe' / 'tests'),
                                '-p', 'test_*.py', '-q'],
                          testLoader=unittest.TestLoader(),
                          testRunner=DiagnosticRunner, exit=True)
    except SystemExit as exc:
        # Python treats None as success and noninteger SystemExit as failure.
        if not state.completed:
            state.exception_category = 'SYSTEM_EXIT'
        if exc.code is None:
            exit_code = 0
        elif isinstance(exc.code, int):
            exit_code = exc.code
        else:
            print(exc.code, file=sys.stderr)
            exit_code = 1
    except BaseException:
        # Retain the original private traceback and native exception behavior.
        state.exception_category = _category(sys.exc_info())
        state.persist()
        raise
    finally:
        state.persist(exit_code)
    return exit_code


def _validate_receipt(value, identities):
    keys = {'schema', 'status', 'phase', 'test_run_completion',
            'exception_category', 'details', 'truncated'} | set(COUNTS)
    if (type(value) is not dict or set(value) != keys or
            type(value['schema']) is not int or value['schema'] != 1 or
            type(value['status']) is not str or value['status'] != 'FAIL' or
            type(value['phase']) is not str or value['phase'] not in PHASES or
            type(value['test_run_completion']) is not str or
            value['test_run_completion'] not in {'COMPLETE', 'INCOMPLETE'} or
            type(value['exception_category']) is not str or
            value['exception_category'] not in CATEGORIES):
        raise ValueError('Invalid receipt')
    for name in COUNTS + ('truncated',):
        if type(value[name]) is not int or not 0 <= value[name] <= MAX_COUNT:
            raise ValueError('Invalid count')
    details = value['details']
    if type(details) is not list or len(details) > MAX_DETAILS:
        raise ValueError('Invalid details')
    totals = dict(FAILURE=value['failures'], ERROR=value['errors'],
                  UNEXPECTED_SUCCESS=value['unexpected_successes'])
    seen = dict.fromkeys(OUTCOMES, 0)
    for detail in details:
        if (type(detail) is not dict or set(detail) != {'test', 'outcome', 'category'} or
                type(detail['test']) is not str or
                (detail['test'] != 'UNKNOWN' and detail['test'] not in identities) or
                type(detail['outcome']) is not str or detail['outcome'] not in OUTCOMES or
                type(detail['category']) is not str or detail['category'] not in CATEGORIES):
            raise ValueError('Invalid detail')
        if detail['outcome'] == 'UNEXPECTED_SUCCESS' and detail['category'] != 'UNKNOWN':
            raise ValueError('Invalid outcome category')
        seen[detail['outcome']] += 1
    total = sum(totals.values())
    if (total > MAX_COUNT or len(details) != min(total, MAX_DETAILS) or
            value['truncated'] != total - len(details) or
            any(seen[name] > totals[name] for name in OUTCOMES)):
        raise ValueError('Inconsistent detail counts')
    if value['phase'] == 'PRECHECK' and (any(value[name] for name in COUNTS) or details or
                                        value['test_run_completion'] != 'INCOMPLETE'):
        raise ValueError('Inconsistent precheck')
    # Discovery may also run ordinary tests: stock unittest executes its entire
    # suite even when import placeholders exist. Never discard those outcomes.
    return value


def _format(value, phase=None):
    text = PREFIX + ' phase=' + (phase or value['phase'])
    text += ' test_run_completion=' + value['test_run_completion']
    text += ' exception_category=' + value['exception_category']
    text += ''.join(' ' + name + '=' + str(value[name]) for name in COUNTS)
    if value['details']:
        for number, detail in enumerate(value['details'], 1):
            lead = ' detail' + str(number) + '_'
            text += lead + 'test=' + detail['test']
            text += lead + 'outcome=' + detail['outcome']
            text += lead + 'category=' + detail['category']
    else:
        category = 'IMPORT' if value['phase'] == 'DISCOVERY' else 'UNKNOWN'
        text += ' test=UNKNOWN outcome=UNKNOWN category=' + category
    return text + ' truncated=' + str(value['truncated'])


def project(root, identity_bytes):
    """Project a failing stage only. No receipt means precheck, not a test ID."""
    try:
        identities = load_identities(identity_bytes)
    except Exception:
        return _format(_empty('PRECHECK'), phase='UNKNOWN')
    try:
        value = _read_receipt(root)
    except FileNotFoundError:
        return _format(_empty('PRECHECK'))
    except Exception:
        return _format(_empty('PRECHECK'), phase='UNKNOWN')
    try:
        return _format(_validate_receipt(value, identities))
    except Exception:
        return _format(_empty('PRECHECK'), phase='UNKNOWN')
