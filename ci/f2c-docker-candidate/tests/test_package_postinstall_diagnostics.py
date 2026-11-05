"""Pure post-install diagnostics fixtures; no bootstrap, native command or install.

Only functions and a post-install AST slice run. Paths, package status and every
side-effecting helper in that slice are fakes, and marker logs are temporary.
"""
import ast
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'preparation' / 'prepare_packages.py'
PRIVATE = 'PRIVATE_PACKAGE PRIVATE_VERSION /PRIVATE_PATH https://private.invalid/'
DELTA_OPERATIONS = (
    'post-delta-match', 'post-delta-missing', 'post-delta-unexpected',
    'post-delta-version', 'post-delta-held', 'post-delta-other',
)
POST_OPERATIONS = (
    'post-install-check', 'post-status-read', 'post-inventory-parse',
    'post-inventory-delta', 'post-closure-verify', 'post-inventory-write',
    'post-apt-check',
)
ORIGINAL_VALIDATE_DELTA = '''def validate_inventory_delta(before, after, transaction):
    expected = {k: dict(v) for k, v in before.items()}
    for row in transaction:
        key = row['name'] + ':' + row['architecture']
        expected[key] = {'name': row['name'], 'version': row['version'],
                         'architecture': row['architecture'], 'held': False}
    require(expected == after, 'Unexpected installed inventory change; no readiness lock emitted')'''
REJECTION = 'Unexpected installed inventory change; no readiness lock emitted'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(name, version='PRIVATE_VERSION', architecture='amd64', held=False):
    return dict(name=name, version=version, architecture=architecture, held=held)


def inventory_rows(*rows):
    return {item['name'] + ':' + item['architecture']: deepcopy(item) for item in rows}


def expected_delta(before, transaction):
    # Independent literal transcription of the frozen acceptance predicate.
    expected = {key: dict(value) for key, value in before.items()}
    for item in transaction:
        expected[item['name'] + ':' + item['architecture']] = {
            'name': item['name'], 'version': item['version'],
            'architecture': item['architecture'], 'held': False,
        }
    return expected


def delta_fixture(defects=()):
    before = inventory_rows(
        row('PRIVATE_missing'), row('PRIVATE_version'), row('PRIVATE_held'),
        row('PRIVATE_other'), row('PRIVATE_unchanged_hold', held=True),
    )
    transaction = [dict(row('PRIVATE_install', 'PRIVATE_NEW_VERSION'), action='install'),
                   dict(row('PRIVATE_version', 'PRIVATE_UPGRADE'), action='upgrade')]
    after = expected_delta(before, transaction)
    if 'missing' in defects:
        del after['PRIVATE_missing:amd64']
    if 'unexpected' in defects:
        after.update(inventory_rows(row('PRIVATE_unexpected')))
    if 'version' in defects:
        after['PRIVATE_version:amd64']['version'] = 'PRIVATE_WRONG_VERSION'
    if 'held' in defects:
        after['PRIVATE_held:amd64']['held'] = True
    if 'other' in defects:
        after['PRIVATE_other:amd64']['name'] = 'PRIVATE_WRONG_NAME'
    return before, after, transaction


def phase_name(node):
    if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == 'phase'
            and len(node.value.args) == 1 and isinstance(node.value.args[0], ast.Constant)):
        return node.value.args[0].value
    return None


class PackagePostinstallDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PACKAGE.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)
        cls.p = load_module('postinstall_packages_fixture', PACKAGE)
        cls.d = load_module('postinstall_buildkit_fixture', ROOT / 'buildkit_diagnostic.py')
        main = next(node for node in cls.tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'main')
        install = next(node for node in main.body if isinstance(node, ast.If)
                       and isinstance(node.test, ast.Attribute)
                       and isinstance(node.test.value, ast.Name)
                       and node.test.value.id == 'args' and node.test.attr == 'install')
        start = next(i for i, node in enumerate(install.body)
                     if phase_name(node) == 'post-install-check')
        end = next(i for i, node in enumerate(install.body[start:], start)
                   if phase_name(node) == 'provider-verification')
        cls.post_block = ast.Module(body=install.body[start:end], type_ignores=[])

    def test_original_acceptance_predicate_is_byte_identical_and_not_diagnostic_driven(self):
        validator = next(node for node in self.tree.body
                         if isinstance(node, ast.FunctionDef)
                         and node.name == 'validate_inventory_delta')
        self.assertEqual(ast.get_source_segment(self.source, validator), ORIGINAL_VALIDATE_DELTA)
        self.assertNotIn('post_install_delta_operation', ast.unparse(validator))
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(validator)))

    def test_valid_empty_install_upgrade_held_and_extra_base_metadata_match(self):
        cases = [({}, {}, []), delta_fixture()]
        before = inventory_rows(row('PRIVATE_preserved', held=True))
        before['PRIVATE_preserved:amd64']['private_extra'] = PRIVATE
        cases.append((before, deepcopy(before), []))
        # The original predicate overwrites transaction records, including held
        # and any old extra fields. This helper is not a transaction validator.
        transaction = [row('PRIVATE_preserved', 'PRIVATE_NEW_VERSION')]
        cases.append((before, expected_delta(before, transaction), transaction))
        # Repeated keys have exactly the original last-write-wins semantics.
        transaction = [row('PRIVATE_repeat', 'PRIVATE_FIRST'),
                       row('PRIVATE_repeat', 'PRIVATE_LAST')]
        cases.append(({}, expected_delta({}, transaction), transaction))
        for before, after, transaction in cases:
            with self.subTest(before=before, transaction=transaction):
                saved = deepcopy((before, after, transaction))
                self.assertEqual(self.p.post_install_delta_operation(before, after, transaction),
                                 'post-delta-match')
                self.assertIsNone(self.p.validate_inventory_delta(before, after, transaction))
                self.assertEqual((before, after, transaction), saved)

    def test_all_defect_combinations_keep_global_priority_and_original_rejection(self):
        defects = ('missing', 'unexpected', 'version', 'held', 'other')
        for bits in itertools.product((False, True), repeat=len(defects)):
            active = tuple(name for name, present in zip(defects, bits) if present)
            expected = 'post-delta-' + (active[0] if active else 'match')
            for reverse in (False, True):
                with self.subTest(defects=active, reverse=reverse):
                    before, after, transaction = delta_fixture(active)
                    if reverse:
                        before = dict(reversed(tuple(before.items())))
                        after = dict(reversed(tuple(after.items())))
                        transaction = list(reversed(transaction))
                    saved = deepcopy((before, after, transaction))
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        operation = self.p.post_install_delta_operation(before, after, transaction)
                        if active:
                            with self.assertRaises(RuntimeError) as caught:
                                self.p.validate_inventory_delta(before, after, transaction)
                            self.assertIs(type(caught.exception), RuntimeError)
                            self.assertEqual(str(caught.exception), REJECTION)
                        else:
                            self.assertIsNone(self.p.validate_inventory_delta(before, after, transaction))
                    self.assertEqual(operation, expected)
                    self.assertIs(type(operation), str)
                    self.assertEqual((before, after, transaction), saved)
                    self.assertEqual(stdout.getvalue(), '')
                    self.assertEqual(stderr.getvalue(), '')

    def test_optimized_functions_keep_the_exact_acceptance_predicate(self):
        names = {'require', 'validate_inventory_delta', 'post_install_delta_operation'}
        functions = [node for node in self.tree.body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in functions}, names)
        pure = ast.Module(body=functions, type_ignores=[])
        for optimize in (0, 1, 2):
            namespace = {}
            exec(compile(pure, '<pure-delta-functions>', 'exec', optimize=optimize),
                 namespace, namespace)
            for defect in (None, 'missing', 'unexpected', 'version', 'held', 'other'):
                case = delta_fixture(() if defect is None else (defect,))
                with self.subTest(optimize=optimize, defect=defect):
                    self.assertEqual(namespace['post_install_delta_operation'](*case),
                                     'post-delta-' + (defect or 'match'))
                    if defect is None:
                        self.assertIsNone(namespace['validate_inventory_delta'](*case))
                    else:
                        with self.assertRaises(RuntimeError) as caught:
                            namespace['validate_inventory_delta'](*case)
                        self.assertIs(type(caught.exception), RuntimeError)
                        self.assertEqual(str(caught.exception), REJECTION)

    def test_other_structural_mismatches_cannot_weaken_exact_equality(self):
        for change in ('name', 'architecture', 'extra', 'missing_field'):
            before, after, transaction = delta_fixture()
            target = after['PRIVATE_other:amd64']
            if change == 'extra':
                target['PRIVATE_EXTRA_FIELD'] = PRIVATE
            elif change == 'missing_field':
                del target['name']
            else:
                target[change] = PRIVATE
            with self.subTest(change=change):
                self.assertEqual(self.p.post_install_delta_operation(before, after, transaction),
                                 'post-delta-other')
                with self.assertRaisesRegex(RuntimeError, REJECTION):
                    self.p.validate_inventory_delta(before, after, transaction)

    def test_malformed_diagnostic_input_falls_back_without_output_or_exception_text(self):
        class UnprintableError(RuntimeError):
            def __str__(self):
                raise AssertionError('Private exception text must not be requested')

        class BadMapping(dict):
            def items(self):
                raise UnprintableError(PRIVATE)

        cases = [(None, {}, []), ({}, None, []), ({}, {}, [{'name': PRIVATE}]),
                 ({'same': row(PRIVATE)}, {'same': None}, []), (BadMapping(), {}, [])]
        for case in cases:
            with self.subTest(case=type(case[0]).__name__):
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(self.p.post_install_delta_operation(*case), 'post-delta-other')
                self.assertEqual(stdout.getvalue() + stderr.getvalue(), '')

    def test_diagnostic_does_not_swallow_process_control_exceptions(self):
        for exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
            class InterruptingMapping(dict):
                def items(self):
                    raise exception(PRIVATE)
            with self.subTest(exception=exception):
                with self.assertRaises(exception):
                    self.p.post_install_delta_operation(InterruptingMapping(), {}, [])

    def execute_post_block(self, optimize=0, defect=None, fault=None, validator_error=None):
        """Run only the real AST slice, with no real filesystem or process calls."""
        before, after, transaction = delta_fixture(() if defect is None else (defect,))
        events, validation_errors = [], []
        phases = []
        error = OSError(PRIVATE)
        status_bytes = b'PRIVATE_FAKE_STATUS'
        if fault == 'decode':
            status_bytes = b'\xff'

        def touch(name):
            events.append(name)
            if fault == name:
                raise error

        class FakePath:
            def __init__(self, value):
                self.value = value

            def __truediv__(self, value):
                return FakePath(self.value + '/' + value)

            def read_bytes(self):
                self_outer.assertEqual(self.value, '/var/lib/dpkg/status')
                touch('status_read')
                return status_bytes

            def write_bytes(self, value):
                self_outer.assertEqual(self.value, 'fixture/post-status')
                self_outer.assertEqual(value, status_bytes)
                touch('status_write')

        self_outer = self

        def phase(operation):
            phases.append(operation)

        def inventory(text):
            self.assertEqual(text, status_bytes.decode())
            touch('inventory')
            return deepcopy(after)

        def validate(*args):
            touch('validate')
            try:
                if validator_error is not None:
                    raise validator_error
                return self.p.validate_inventory_delta(*args)
            except Exception as original:
                validation_errors.append(original)
                raise

        def classify(*args):
            touch('classify')
            return self.p.post_install_delta_operation(*args)

        def write_json(path, value):
            self.assertEqual(path.value, 'fixture/post-inventory.json')
            self.assertEqual(value, after)
            touch('inventory_write')

        def run(argv, label, privileged=False):
            self.assertEqual(argv, ['/usr/bin/apt-get', 'check'])
            self.assertEqual(label, 'post-apt-check')
            self.assertIs(privileged, True)
            touch('apt_check')

        resolved = [dict(transaction[0])]
        if fault == 'closure':
            resolved[0]['version'] = 'PRIVATE_BAD_CLOSURE_VERSION'
        namespace = dict(Path=FakePath, out=FakePath('fixture'), phase=phase,
                         inventory=inventory, base=before, solution={'transaction': transaction},
                         validate_inventory_delta=validate, post_install_delta_operation=classify,
                         require=self.p.require, resolved=resolved, write_json=write_json, run=run)
        stdout, stderr = io.StringIO(), io.StringIO()
        caught = None
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                exec(compile(self.post_block, '<isolated-post-install-fixture>', 'exec',
                             optimize=optimize), namespace, namespace)
            except Exception as original:
                caught = original
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), '')
        return phases, events, caught, validation_errors

    def test_success_phase_order_and_failure_only_classifier_even_optimized(self):
        for optimize in (0, 1):
            with self.subTest(optimize=optimize):
                phases, events, caught, _ = self.execute_post_block(optimize)
                self.assertIsNone(caught)
                self.assertEqual(phases, list(POST_OPERATIONS))
                self.assertEqual(events, ['status_read', 'status_write', 'inventory',
                                          'validate', 'inventory_write', 'apt_check'])

    def test_delta_failure_preserves_original_error_and_never_continues(self):
        for optimize in (0, 1):
            for defect in ('missing', 'unexpected', 'version', 'held', 'other'):
                with self.subTest(optimize=optimize, defect=defect):
                    phases, events, caught, errors = self.execute_post_block(optimize, defect)
                    self.assertEqual(phases, list(POST_OPERATIONS[:4]) + ['post-delta-' + defect])
                    self.assertEqual(events, ['status_read', 'status_write', 'inventory',
                                              'validate', 'classify'])
                    self.assertIs(caught, errors[0])
                    self.assertIs(type(caught), RuntimeError)
                    self.assertEqual(str(caught), REJECTION)

    def test_diagnostic_match_cannot_convert_a_validator_failure_to_success(self):
        for optimize in (0, 1):
            sentinel = RuntimeError(PRIVATE)
            phases, events, caught, _ = self.execute_post_block(optimize, validator_error=sentinel)
            self.assertEqual(phases[-1], 'post-delta-match')
            self.assertIs(caught, sentinel)
            self.assertEqual(events[-1], 'classify')
            self.assertNotIn('inventory_write', events)
            self.assertNotIn('apt_check', events)

    def test_other_post_failures_stay_at_the_exact_static_phase(self):
        cases = [('status_read', 'post-status-read'), ('status_write', 'post-status-read'),
                 ('decode', 'post-inventory-parse'), ('inventory', 'post-inventory-parse'),
                 ('closure', 'post-closure-verify'), ('inventory_write', 'post-inventory-write'),
                 ('apt_check', 'post-apt-check')]
        for optimize in (0, 1):
            for fault, operation in cases:
                with self.subTest(optimize=optimize, fault=fault):
                    phases, events, caught, _ = self.execute_post_block(optimize, fault=fault)
                    self.assertIsNotNone(caught)
                    self.assertEqual(phases[-1], operation)
                    self.assertNotIn('classify', events)
                    if fault not in ('inventory_write', 'apt_check'):
                        self.assertNotIn('inventory_write', events)
                    if fault != 'apt_check':
                        self.assertNotIn('apt_check', events)

    def test_non_runtime_validator_error_keeps_generic_delta_phase(self):
        sentinel = KeyError(PRIVATE)
        phases, events, caught, _ = self.execute_post_block(validator_error=sentinel)
        self.assertIs(caught, sentinel)
        self.assertEqual(phases[-1], 'post-inventory-delta')
        self.assertNotIn('classify', events)

    def test_all_post_operations_round_trip_closed_public_markers_without_private_text(self):
        for operation in POST_OPERATIONS + DELTA_OPERATIONS:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                output = io.StringIO()
                with patch.object(self.p, 'DIAGNOSTIC_CONTEXT', {
                        'operation': operation, 'suite': PRIVATE, 'component': PRIVATE}), \
                        patch.object(self.p, 'DIAGNOSTIC_ROOT', None), redirect_stderr(output):
                    receipt = self.p.emit_failure(RuntimeError(PRIVATE))
                self.assertEqual(receipt, {
                    'schema': 1, 'code': 'APT_INVARIANT_REJECTED', 'operation': operation,
                    'exception_class': 'RuntimeError', 'http_status': None, 'errno': None,
                    'returncode': None, 'suite': None, 'component': None,
                })
                marker = output.getvalue()
                self.assertEqual(marker.count('\n'), 1)
                self.assertTrue(marker.startswith(self.p.DIAGNOSTIC_PREFIX))
                self.assertNotIn('PRIVATE', marker)
                self.assertNotIn('http', marker.replace('http_status', ''))
                log = Path(temporary) / 'PRIVATE_log'
                log.write_text(PRIVATE + '\n#15 [ 7/18] RUN ' + self.d.RUN_COMMANDS['package']
                               + '\n#15 0.123 ' + marker + PRIVATE + '\n')
                projected = self.d.parse_log(log)
                self.assertEqual(projected, {'schema': 1, 'source': 'package',
                                             'diagnostic_only': True, 'detail': receipt})
                self.assertEqual(self.d.validate_projection(projected), projected)
                self.assertNotIn('PRIVATE', json.dumps(projected))

    def test_unknown_or_contaminated_operations_and_extra_details_are_rejected(self):
        base = dict(schema=1, code='APT_INVARIANT_REJECTED', operation='post-delta-other',
                    exception_class='RuntimeError', http_status=None, errno=None,
                    returncode=None, suite=None, component=None)
        invalid = [dict(base, operation=operation) for operation in (
            'post-delta-version:PRIVATE_PACKAGE', 'post-delta-version PRIVATE_VERSION',
            'post-delta-unknown', 'post-status-read/PRIVATE_PATH', ['post-delta-other'])]
        invalid += [dict(base, package=PRIVATE), dict(base, version=PRIVATE),
                    dict(base, path=PRIVATE), dict(base, raw=PRIVATE)]
        for receipt in invalid:
            with self.subTest(receipt=receipt), tempfile.TemporaryDirectory() as temporary:
                log = Path(temporary) / 'log'
                log.write_text('#15 [7/18] RUN ' + self.d.RUN_COMMANDS['package']
                               + '\n#15 0.123 ' + self.d.PACKAGE_PREFIX
                               + json.dumps(receipt) + '\n')
                self.assertIsNone(self.d.parse_log(log))
                self.assertIsNone(self.d.validate_projection({
                    'schema': 1, 'source': 'package', 'diagnostic_only': True, 'detail': receipt}))


if __name__ == '__main__':
    unittest.main()
