"""Real receipt -> execute boundary -> reducer/CLI scope regressions.

Only production AST fragments without launch/preflight operations are executed.
The receipt producer and both reducers are real. Subprocesses run the public
reducer only, from an unrelated directory without PYTHONPATH, also under -O.
No native process, network, CI, debugger, or application checkout is needed.
"""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call


WAREHOUSE = Path(__file__).resolve().parent
HARNESS = WAREHOUSE.parent
sys.path[:0] = [str(WAREHOUSE), str(HARNESS)]

import lifecycle_only_public_summary as public
import recheck
import warehouse_acceptance as acceptance
import warehouse_public_summary as ordinary
from test_lifecycle_only_summary import passing_raw, passing_runner


MODE_FIELDS = frozenset(('stage', 'execution_scope', 'task_not_submitted',
                         'route_acceptance', 'overall_wall_budget_s'))
RESOURCE_FIELDS = (*public.RESOURCE_COUNTS, 'resources_match_historical',
                   'historical_resource_manifest_sha256',
                   'current_resource_manifest_sha256')


def finite_record():
    """Minimal finite input accepted by the real historical receipt producer."""
    return {
        'reproducibility': {'execution_sha256': '1' * 64,
                            'adapter_sha256': '2' * 64},
        'replay': {'current_map_fingerprint_sha256': '3' * 64,
                   'current_plan_sha256': '4' * 64,
                   'physical_budget_s': 649.6},
    }


def production_execute():
    tree = ast.parse(Path(acceptance.__file__).read_text())
    return next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == 'execute')


def execute_nodes(nodes, namespace, label):
    # Execute unmodified production statements, not a handwritten raw.update.
    module = ast.Module(body=deepcopy(nodes), type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, label, 'exec'), namespace)


def receipt_boundary(lifecycle_only=True):
    """Run actual initial mode setup and the contiguous receipt merge block."""
    execute = production_execute()
    initial_index = next(index for index, node in enumerate(execute.body)
                         if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name)
                                 and target.id == 'raw'
                                 for target in node.targets))
    initial_mode = execute.body[initial_index + 1]
    if not (isinstance(initial_mode, ast.If)
            and isinstance(initial_mode.test, ast.Name)
            and initial_mode.test.id == 'lifecycle_only'):
        raise AssertionError('Production raw initialization shape changed')
    main_try = next(node for node in execute.body if isinstance(node, ast.Try))
    receipt_index = next(index for index, node in enumerate(main_try.body)
                         if any(isinstance(child, ast.Call)
                                and isinstance(child.func, ast.Name)
                                and child.func.id == 'public_receipt'
                                for child in ast.walk(node)))
    save_index = next(index for index in range(receipt_index + 1, len(main_try.body))
                      if any(isinstance(child, ast.Call)
                             and isinstance(child.func, ast.Name)
                             and child.func.id == 'save' and child.args
                             and isinstance(child.args[0], ast.Constant)
                             and child.args[0].value == 'new-record.json'
                             for child in ast.walk(main_try.body[index])))
    evidence = passing_raw()
    record = finite_record()
    namespace = {
        'lifecycle_only': lifecycle_only, 'record': record,
        'public_receipt': recheck.public_receipt,
        'SOURCE': acceptance.SOURCE, 'OVERALL_WALL_S': acceptance.OVERALL_WALL_S,
        'a': SimpleNamespace(harness_sha='b' * 40),
        'resources': {key: evidence[key] for key in RESOURCE_FIELDS},
        'map_before': {'current_map_bytes_sha256': '5' * 64,
                       'current_map_fingerprint_sha256': '3' * 64},
    }
    execute_nodes(execute.body[initial_index:initial_index + 2], namespace,
                  'production-execute-raw-initialization')
    initial = deepcopy(namespace['raw'])
    execute_nodes(main_try.body[receipt_index:save_index], namespace,
                  'production-execute-receipt-boundary')
    return initial, namespace['raw'], record


def observed_raw():
    """Supply later successful observations without repairing mode or receipt."""
    _, raw, _ = receipt_boundary()
    # Other gate fixtures isolate scope. Crucially, no mode fields or producer
    # fields can be introduced/replaced here, even if production loses them.
    receipt_fields = set(recheck.public_receipt(finite_record()))
    for key, value in passing_raw().items():
        if key not in MODE_FIELDS and key not in receipt_fields and key not in raw:
            raw[key] = deepcopy(value)
    # These are actual later observation categories, overwritten after receipt
    # import in execute: completed lifecycle, cleanup, and observed readiness.
    raw.update(state='PASS', cleanup_pass=True, session_ready_observed=True)
    return raw


def actual_finalizer(namespace):
    main_try = next(node for node in production_execute().body
                    if isinstance(node, ast.Try))
    function = ast.FunctionDef(
        name='finalize',
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                           kw_defaults=[], defaults=[]),
        body=[ast.Global(names=['forced', 'diagnostic'])]
             + deepcopy(main_try.finalbody),
        decorator_list=[],
    )
    execute_nodes([function], namespace, 'production-execute-scope-finalizer')
    return namespace['finalize']


class LifecycleScopeIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='lifecycle-scope-test-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.cwd = self.directory / 'unrelated-working-directory'
        self.cwd.mkdir()
        self.env = dict(os.environ)
        self.env.pop('PYTHONPATH', None)
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'
        self.env['PYTHONNOUSERSITE'] = '1'

    def assert_finite(self, value):
        return json.dumps(value, allow_nan=False)

    def run_public_cli(self, raw, optimized=False):
        source = self.directory / 'raw.json'
        runner = self.directory / 'runner.json'
        source.write_text(self.assert_finite(raw), encoding='utf-8')
        runner.write_text(self.assert_finite(passing_runner()), encoding='utf-8')
        command = [sys.executable]
        if optimized:
            command.append('-O')
        command += [str(WAREHOUSE / 'lifecycle_only_public_summary.py'),
                    '--input', str(source), '--runner-status', str(runner)]
        self.assertNotIn('PYTHONPATH', self.env)
        completed = subprocess.run(command, cwd=self.cwd, env=self.env,
                                   capture_output=True, text=True, timeout=10,
                                   check=False)
        self.assertEqual(completed.stderr, '')
        result = json.loads(completed.stdout)
        self.assert_finite(result)
        self.assertEqual(completed.returncode, 0 if result['state'] == 'PASS' else 1)
        self.assertEqual(result['warehouse_state'], 'NOT_RUN')
        self.assertEqual(result['route_acceptance'], 'NOT_RUN')
        return result

    def finalizer_namespace(self, raw, *, lifecycle_only=True):
        capture = acceptance.IdentityCapture()
        gateway = Mock()
        gateway.poll.return_value = None
        gateway.returncode = 0
        cleanup = Mock(wraps=acceptance.cleanup_lifecycle_gateway)
        return dict(
            lifecycle_only=lifecycle_only, process_observer=None, observer=None,
            is_cancellation=acceptance.is_cancellation, identity_capture=capture,
            raw=raw, base='mock_api', gateway=gateway, api=Mock(return_value={}),
            diagnostic=raw['warehouse_diagnostics'],
            describe_failure=acceptance.describe_failure,
            owned=Mock(return_value=[]), signal_owned=Mock(), token='owned',
            forced=False, subprocess=subprocess, signal=signal, time=time,
            collect=Mock(return_value={}), runtime=self.directory, sid='sim_fixed',
            project=public.project if lifecycle_only else ordinary.project,
            cleanup_lifecycle_gateway=cleanup,
            a=SimpleNamespace(summary=self.directory / 'summary.json'), json=json,
        )

    def assert_gateway_cleanup(self, namespace, *, lifecycle=True):
        cleanup = namespace['cleanup_lifecycle_gateway']
        if lifecycle:
            cleanup.assert_called_once_with(
                'mock_api', namespace['gateway'], 'owned', namespace['api'],
                namespace['owned'], namespace['signal_owned'],
                namespace['identity_capture'])
        else:
            cleanup.assert_not_called()
        namespace['api'].assert_called_once_with(
            'mock_api', 'POST', '/shutdown', {}, timeout=10)
        self.assertEqual(namespace['gateway'].wait.call_args_list,
                         [call(timeout=240), call(timeout=5)])
        namespace['signal_owned'].assert_not_called()

    def test_real_receipt_remains_finite_ordinary_nonexecution_evidence(self):
        record = finite_record()
        before = deepcopy(record)
        receipt = recheck.public_receipt(record)
        self.assertEqual(record, before)
        self.assert_finite(record)
        self.assert_finite(receipt)
        self.assertEqual(receipt['stage'], 'warehouse')
        self.assertEqual(receipt['state'], 'VALIDATED_NOT_EXECUTED')
        self.assertEqual(receipt['simulation_state'], 'NOT_RUN')
        self.assertIsNone(receipt['cleanup_pass'])
        self.assertEqual(receipt['physical_budget_s'], 649.6)
        for key in ('execution_scope', 'task_not_submitted', 'route_acceptance'):
            self.assertNotIn(key, receipt)

    def test_actual_execute_boundary_restores_lifecycle_mode_after_real_receipt(self):
        initial, raw, record = receipt_boundary()
        expected_mode = dict(stage='warehouse-lifecycle-only',
                             execution_scope='lifecycle_only',
                             task_not_submitted=True, route_acceptance='NOT_RUN',
                             overall_wall_budget_s=3600)
        self.assertEqual({key: initial[key] for key in MODE_FIELDS}, expected_mode)
        self.assertEqual({key: raw[key] for key in MODE_FIELDS}, expected_mode)
        self.assertEqual(raw['state'], 'ERROR')
        self.assertFalse(raw['session_ready_observed'])
        self.assertEqual(recheck.public_receipt(record)['stage'], 'warehouse')
        for key, value in recheck.public_receipt(record).items():
            if key not in ('stage', 'state', 'overall_wall_budget_s',
                           'resources_match_historical'):
                self.assertEqual(raw[key], value, key)

    def test_ordinary_execute_boundary_keeps_receipt_behavior_unchanged(self):
        initial, raw, record = receipt_boundary(lifecycle_only=False)
        expected = deepcopy(initial)
        expected.update(recheck.public_receipt(record))
        expected.update({key: passing_raw()[key] for key in RESOURCE_FIELDS})
        expected.update(source_sha=acceptance.SOURCE, harness_sha='b' * 40,
                        overall_wall_budget_s=acceptance.OVERALL_WALL_S,
                        state='ERROR', current_map_bytes_sha256='5' * 64,
                        current_map_fingerprint_sha256='3' * 64)
        self.assertEqual(raw, expected)
        self.assertEqual(raw['stage'], 'warehouse')
        self.assertEqual(raw['overall_wall_budget_s'], 7200)
        for key in ('execution_scope', 'task_not_submitted', 'route_acceptance',
                    'session_ready_observed'):
            self.assertNotIn(key, raw)

    def test_real_boundary_reducer_has_all_nonrunner_gates_not_only_scope(self):
        raw = observed_raw()
        result = public.project(raw, require_runner=False)
        self.assertEqual(result['state'], 'PASS')
        self.assertEqual(result['overall_wall_budget_s'], 3600)
        self.assertFalse(result['lifecycle_gates']['runner_verified'])
        for key, value in result['lifecycle_gates'].items():
            if key != 'runner_verified':
                self.assertIs(value, True, key)
        self.assertIsNone(result['tasks_total'])
        self.assertIsNone(result['tasks_reached'])
        self.assertIsNone(result['trajectory_samples'])
        self.assert_finite(result)

    def test_real_boundary_and_reducer_reach_cli_without_pythonpath_normal_and_o(self):
        raw = observed_raw()
        intermediate = public.project(raw, require_runner=False)
        for name, input_value in (('raw-boundary', raw),
                                  ('harness-reduced', intermediate)):
            for optimized in (False, True):
                with self.subTest(input=name, optimized=optimized):
                    result = self.run_public_cli(input_value, optimized)
                    self.assertEqual(result['state'], 'PASS')
                    self.assertEqual(result['stage'], public.STAGE)
                    self.assertEqual(result['execution_scope'], public.EXECUTION_SCOPE)
                    self.assertIs(result['task_not_submitted'], True)
                    self.assertTrue(all(result['lifecycle_gates'].values()))
                    self.assertEqual(result['overall_wall_budget_s'], 3600)

    def test_corrupt_postreceipt_scope_fails_only_scope_in_real_reducer_and_cli(self):
        mutations = ({'stage': 'warehouse'}, {'execution_scope': 'warehouse'},
                     {'task_not_submitted': False}, {'stage': None})
        for mutation in mutations:
            raw = observed_raw()
            raw.update(mutation)
            result = public.project(raw, passing_runner())
            self.assertEqual(result['state'], 'FAIL')
            self.assertEqual([key for key, value in result['lifecycle_gates'].items()
                              if not value], ['scope_verified'])
            for optimized in (False, True):
                with self.subTest(mutation=mutation, optimized=optimized):
                    result = self.run_public_cli(raw, optimized)
                    self.assertEqual(result['state'], 'FAIL')
                    self.assertEqual([key for key, value in result['lifecycle_gates'].items()
                                      if not value], ['scope_verified'])

    def test_corrupt_raw_cannot_switch_actual_lifecycle_finalizer_to_old_cleanup(self):
        for stage in ('warehouse', None, 'unknown'):
            with self.subTest(stage=stage):
                raw = observed_raw()
                raw.update(stage=stage, execution_scope='warehouse',
                           task_not_submitted=False, route_acceptance='PASS')
                namespace = self.finalizer_namespace(raw, lifecycle_only=True)
                actual_finalizer(namespace)()
                self.assert_gateway_cleanup(namespace)
                self.assertIs(namespace['lifecycle_only'], True)
                saved = json.loads(namespace['a'].summary.read_text())
                self.assertEqual(saved['state'], 'FAIL')
                self.assertFalse(saved['lifecycle_gates']['scope_verified'])
                self.assertTrue(saved['lifecycle_gates']['cleanup_verified'])
                self.assertTrue(saved['lifecycle_gates']['final_cleanup_verified'])

    def test_first_exact_cancellation_survives_corrupt_scope_and_later_cancels(self):
        for kind in ('process', 'observer', 'collection', 'original'):
            for first in (RuntimeError('interrupted'), KeyboardInterrupt('first'),
                          SystemExit(19)):
                with self.subTest(site=kind, error=type(first)):
                    raw = observed_raw()
                    raw['stage'] = 'warehouse'
                    raw.pop('execution_scope')
                    namespace = self.finalizer_namespace(raw, lifecycle_only=True)
                    second = SystemExit(23)
                    capture = namespace['identity_capture']
                    if kind == 'original':
                        capture.original_error = first
                    if kind == 'process':
                        namespace['process_observer'] = Mock()
                        namespace['process_observer'].stop.side_effect = first
                    if kind in ('process', 'observer', 'original'):
                        namespace['observer'] = Mock()
                        namespace['observer'].raw = {}
                        namespace['observer'].safe.side_effect = (
                            first if kind == 'observer' else second)
                    namespace['collect'].side_effect = (
                        first if kind == 'collection' else second)
                    # An earlier collection cancellation cannot precede gateway
                    # shutdown; leave that API successful in the collection case.
                    if kind != 'collection':
                        namespace['api'].side_effect = second
                    try:
                        actual_finalizer(namespace)()
                    except BaseException as received:
                        self.assertIs(received, first)
                    else:
                        self.fail('Production finalizer lost the first cancellation')
                    self.assert_gateway_cleanup(namespace)
                    if kind != 'original':
                        self.assertIs(capture.pending, first)
                    saved = json.loads(namespace['a'].summary.read_text())
                    self.assertNotEqual(saved['state'], 'PASS')
                    self.assertEqual(saved['remaining_owned_process_count'], 0)

    def test_corrupt_scope_original_cancel_survives_actual_summary_write_failure(self):
        for first in (RuntimeError('interrupted'), KeyboardInterrupt('first'),
                      SystemExit(19)):
            with self.subTest(error=type(first)):
                raw = observed_raw()
                raw['stage'] = 'warehouse'
                namespace = self.finalizer_namespace(raw, lifecycle_only=True)
                namespace['identity_capture'].original_error = first
                namespace['a'].summary = self.directory  # A directory cannot be written.
                try:
                    actual_finalizer(namespace)()
                except BaseException as received:
                    self.assertIs(received, first)
                else:
                    self.fail('Original cancellation lost after summary write failure')
                self.assert_gateway_cleanup(namespace)

    def test_ordinary_finalizer_does_not_trust_forged_lifecycle_scope(self):
        raw = observed_raw()
        namespace = self.finalizer_namespace(raw, lifecycle_only=False)
        actual_finalizer(namespace)()
        self.assert_gateway_cleanup(namespace, lifecycle=False)
        self.assertIs(namespace['lifecycle_only'], False)
        saved = json.loads(namespace['a'].summary.read_text())
        self.assertEqual(saved['stage'], 'warehouse')


if __name__ == '__main__':
    unittest.main()
