"""Local pure tests: no native execution, debugger, network, or process probing."""
import copy
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import cleanup_diagnostics
import lifecycle_only_public_summary as public
import warehouse_public_summary as ordinary
from historical_fuel import ARCHIVE_SHA256, SELECTED_SHA256, COHORT, ALIAS_PROJECTION
from historical_input import SOURCE


def passing_raw():
    raw = {key: 'a' * 64 for key in ordinary.HASHES}
    raw.update({key: True for key in public.IDENTITY_BOOLS})
    raw.update(
        state='PASS', stage=public.STAGE, execution_scope=public.EXECUTION_SCOPE,
        task_not_submitted=True, session_ready_observed=True,
        route_acceptance='NOT_RUN', source_sha=SOURCE, harness_sha='b' * 40,
        cleanup_pass=True, gateway_exit_code=0, remaining_owned_process_count=0,
        resource_cache_cohort=COHORT, historical_cache_verified=True,
        historical_fuel_archive_sha256=ARCHIVE_SHA256,
        historical_selected_manifest_sha256=SELECTED_SHA256,
        historical_materialized_file_count=61, historical_alias_projection=ALIAS_PROJECTION,
        raw_resource_manifest_sha256=public.RAW_RESOURCE_PAIRS[0][0],
        raw_resources_match_historical=False,
        historical_resource_count=45, current_resource_count=45,
        missing_resource_count=0, additional_resource_count=0, changed_resource_count=0,
        overall_wall_budget_s=1800,
        warehouse_diagnostics={
            'session_report': 'available',
            'harness': {'forced_cleanup': False, 'gateway_health_verified': True,
                        'gateway_record_verified': True, 'phase': 'complete'},
            'lifecycle': {'session_id_observed': True, 'last_session_phase': 'ready',
                          'cleanup_session_phase': 'stopped', 'phase': 'cleanup_wait'}},
    )
    raw['warehouse_cleanup_diagnostics'] = cleanup_diagnostics.collect(
        {'status': 'PASS', 'remaining_owned_processes': [], 'abnormal_exits': [],
         'errors': [], 'final_state': {'phase': 'stopping', 'errors': []},
         'cleanup': [], 'bridge_shutdown': {'still_running': []},
         'lifecycle_shutdown': {'navigation': True, 'localization': True},
         'gazebo_shutdown': {'accepted': True, 'returncode': 0}},
        {'stop': {'phase': 'stopped', 'errors': []}})
    return raw


def passing_runner():
    return {'stage': 'simulation', 'requested_stage': public.STAGE,
            'termination_reason': 'completed', 'exit_code': 0,
            'cleanup_remaining': 0, 'forced_kill_count': 0, 'failure_hint': 'none'}


class LifecycleOnlySummaryTests(unittest.TestCase):
    def assert_rejected(self, raw, runner=None, **kwargs):
        result = public.project(raw, passing_runner() if runner is None else runner, **kwargs)
        self.assertNotEqual(result['state'], 'PASS')
        self.assertEqual(result['warehouse_state'], 'NOT_RUN')
        self.assertEqual(result['route_acceptance'], 'NOT_RUN')
        return result

    def test_success_is_lifecycle_only_and_no_task_measurements(self):
        raw = passing_raw()
        result = public.project(raw, passing_runner())
        self.assertEqual(result['state'], 'PASS')
        self.assertEqual(result['lifecycle_state'], 'PASS')
        self.assertEqual(result['stage'], public.STAGE)
        self.assertEqual(result['execution_scope'], public.EXECUTION_SCOPE)
        self.assertEqual(result['warehouse_state'], 'NOT_RUN')
        self.assertEqual(result['route_acceptance'], 'NOT_RUN')
        self.assertTrue(result['task_not_submitted'])
        self.assertTrue(all(result['lifecycle_gates'].values()))
        for key in (*public.TASK_COUNTS, *public.TASK_METRICS):
            self.assertIsNone(result[key])
            self.assertEqual(result['measurement_availability'][key], 'not_measured')
        for key in ('tasks_all_reached', 'execution_mode_verified', 'trajectory_motion_verified',
                    'execution_protocol', 'execution_diagnostics', 'physical_budget_s'):
            self.assertNotIn(key, result)

    def test_no_route_task_protocol_or_7200_gate(self):
        raw = passing_raw()
        raw.update(tasks_total=5, tasks_reached=0, tasks_all_reached=False,
                   trajectory_samples=0, trajectory_motion_verified=False,
                   actual_length_m=0, execution_protocol={}, physical_budget_s=0,
                   overall_wall_budget_s=120, route_check_valid=False,
                   map_collision_free=False, actual_map_collision_free=False,
                   execution_mode_verified=False)
        result = public.project(raw, passing_runner())
        self.assertEqual(result['state'], 'PASS')
        self.assertIsNone(result['tasks_reached'])
        self.assertIsNone(result['actual_length_m'])
        self.assertEqual(result['overall_wall_budget_s'], 120)

    def test_cannot_promote_ordinary_route_pass(self):
        for stage in ('warehouse', 'maze', 'simulation', 'PASS', None, '', 'unknown', [], {}, True):
            with self.subTest(stage=stage):
                raw = passing_raw()
                raw['stage'] = stage
                self.assert_rejected(raw)
        raw = passing_raw()
        del raw['stage']
        self.assert_rejected(raw)
        for raw in ({'state': 'PASS'}, ordinary.project({'state': 'PASS'}, passing_runner())):
            self.assert_rejected(raw)

    def test_real_route_pass_and_lifecycle_pass_cannot_cross_promote(self):
        from test_lifecycle import LifecycleTests
        route = ordinary.project(LifecycleTests().good(), require_runner=False)
        self.assertEqual(route['state'], 'PASS')
        self.assertEqual(route['stage'], 'warehouse')
        self.assert_rejected(route)
        for runner in (None, passing_runner()):
            lifecycle = public.project(passing_raw(), runner, require_runner=False)
            self.assertEqual(lifecycle['state'], 'PASS')
            promoted = ordinary.project(lifecycle, runner, require_runner=False)
            self.assertEqual(promoted['state'], 'FAIL')
            self.assertEqual(promoted['warehouse_state'], 'FAIL')

    def test_scope_and_not_submitted_must_be_exact(self):
        for key, values in (
            ('execution_scope', ('warehouse', 'route', None, True, [], {}, '')),
            ('task_not_submitted', (False, None, 1, 0, 'true', [], {})),
            ('session_ready_observed', (False, None, 1, 'ready', [], {}))):
            for value in values:
                with self.subTest(key=key, value=value):
                    raw = passing_raw()
                    raw[key] = value
                    self.assert_rejected(raw)
            raw = passing_raw()
            del raw[key]
            self.assert_rejected(raw)

    def test_ready_requires_observed_identity_and_phase(self):
        for key, values in (
            ('session_id_observed', (False, None, 1, 'true')),
            ('last_session_phase', ('starting', 'undocking', 'executing', 'stopped', None))):
            for value in values:
                raw = passing_raw()
                raw['warehouse_diagnostics']['lifecycle'][key] = value
                result = self.assert_rejected(raw)
                self.assertFalse(result['lifecycle_gates']['ready_verified'])

    def test_each_identity_boolean_and_hash_required(self):
        for key in public.IDENTITY_BOOLS:
            for value in (False, None, 1, 'true'):
                raw = passing_raw()
                raw[key] = value
                self.assert_rejected(raw)
        for key in (*ordinary.HASHES, 'source_sha', 'harness_sha'):
            for value in (None, 'bad', True, 'A' * (40 if key.endswith('_sha') else 64)):
                raw = passing_raw()
                raw[key] = value
                self.assert_rejected(raw)
        raw = passing_raw()
        raw['source_sha'] = 'a' * 40
        self.assert_rejected(raw)

    def test_each_historical_cache_condition_required(self):
        bad = {'resource_cache_cohort': 'unknown', 'historical_cache_verified': False,
               'historical_fuel_archive_sha256': 'c' * 64,
               'historical_selected_manifest_sha256': 'c' * 64,
               'historical_materialized_file_count': 60,
               'historical_alias_projection': 'unknown'}
        for key, value in bad.items():
            raw = passing_raw()
            raw[key] = value
            result = self.assert_rejected(raw)
            self.assertFalse(result['lifecycle_gates']['historical_cache_verified'])

    def test_each_resource_condition_required(self):
        for key, value in (
            ('raw_resource_manifest_sha256', 'c' * 64),
            ('raw_resources_match_historical', True),
            ('historical_resource_count', 44), ('current_resource_count', 46),
            ('missing_resource_count', 1), ('additional_resource_count', 1),
            ('changed_resource_count', 1)):
            raw = passing_raw()
            raw[key] = value
            self.assert_rejected(raw)
        for digest, matches in public.RAW_RESOURCE_PAIRS:
            raw = passing_raw()
            raw.update(raw_resource_manifest_sha256=digest,
                       raw_resources_match_historical=matches)
            self.assertEqual(public.project(raw, passing_runner())['state'], 'PASS')

    def test_six_existing_cleanup_predicates_each_required(self):
        for key in cleanup_diagnostics.CONJUNCTS:
            for value in (False, None, 1, 'true'):
                raw = passing_raw()
                raw['warehouse_cleanup_diagnostics']['conjuncts'][key] = value
                result = self.assert_rejected(raw)
                self.assertFalse(result['lifecycle_gates']['cleanup_verified'])
        for value in (False, None, 1, 'true'):
            raw = passing_raw()
            raw['cleanup_pass'] = value
            self.assert_rejected(raw)

    def test_cleanup_detail_must_be_authenticated_at_both_levels(self):
        for value in ('missing', 'invalid', 'identity_mismatch', 'unavailable', None, 'SECRET'):
            for target in ('harness', 'detail'):
                raw = passing_raw()
                if target == 'harness':
                    raw['warehouse_diagnostics']['session_report'] = value
                else:
                    raw['warehouse_cleanup_diagnostics']['availability'] = value
                self.assert_rejected(raw)
        for value in ('FAIL', None, 'SECRET'):
            raw = passing_raw()
            raw['warehouse_cleanup_diagnostics']['session_status'] = value
            self.assert_rejected(raw)
        raw = passing_raw()
        raw['warehouse_diagnostics']['lifecycle']['cleanup_session_phase'] = 'stopping'
        self.assert_rejected(raw)

    def test_final_gateway_owned_forced_constraints_each_required(self):
        for key in ('gateway_exit_code', 'remaining_owned_process_count'):
            for value in (-9, 1, None, False, 0.0, '0'):
                raw = passing_raw()
                raw[key] = value
                self.assert_rejected(raw)
        for value in (True, None, 0, 'false'):
            raw = passing_raw()
            raw['warehouse_diagnostics']['harness']['forced_cleanup'] = value
            self.assert_rejected(raw)

    def test_runner_precise_requested_stage_and_phase(self):
        for key, values in (
            ('requested_stage', ('warehouse', 'maze', None, 'simulation', [])),
            ('stage', ('installation', public.STAGE, None, 'unknown')),
            ('termination_reason', ('unknown', 'cancelled', 'child_exit', 'stage_timeout', 'supervisor_error', None))):
            for value in values:
                runner = passing_runner()
                runner[key] = value
                result = self.assert_rejected(passing_raw(), runner)
                self.assertFalse(result['lifecycle_gates']['runner_verified'])
        for key in ('exit_code', 'cleanup_remaining', 'forced_kill_count'):
            for value in (1, -1, None, False, 0.0, '0'):
                runner = passing_runner()
                runner[key] = value
                self.assert_rejected(passing_raw(), runner)

    def test_runner_required_except_explicit_intermediate(self):
        self.assertNotEqual(public.project(passing_raw())['state'], 'PASS')
        intermediate = public.project(passing_raw(), require_runner=False)
        self.assertEqual(intermediate['state'], 'PASS')
        self.assertFalse(intermediate['runner_status_present'])
        self.assertFalse(intermediate['lifecycle_gates']['runner_verified'])
        self.assertEqual(public.project(intermediate, passing_runner())['state'], 'PASS')
        self.assertNotEqual(public.project(intermediate)['state'], 'PASS')
        for runner in ({}, [], False, 'SECRET'):
            self.assert_rejected(passing_raw(), runner, require_runner=False)

    def test_no_nonpass_input_promoted(self):
        for state in ('FAIL', 'ERROR', 'NOT_RUN', None, [], {}, 'secret', 1):
            raw = passing_raw()
            raw['state'] = state
            self.assert_rejected(raw)

    def test_idempotent_intermediate_final_fail_and_unknown(self):
        for state in public.STATES:
            raw = passing_raw()
            raw['state'] = state
            for require_runner, runner in ((False, None), (True, passing_runner()), (True, {})):
                first = public.project(raw, runner, require_runner=require_runner)
                self.assertEqual(public.project(first, require_runner=require_runner), first)
                self.assertEqual(public.project(json.loads(json.dumps(first)), require_runner=require_runner), first)
        for raw in (None, [], {}, {'state': 'PASS', 'stage': 'warehouse'}, 'SECRET'):
            first = public.project(raw)
            self.assertEqual(public.project(first), first)

    def test_bad_explicit_runner_overrides_prior_good_runner(self):
        good = public.project(passing_raw(), passing_runner())
        self.assertEqual(good['state'], 'PASS')
        bad = public.project(good, {})
        self.assertEqual(bad['state'], 'FAIL')
        self.assertFalse(bad['lifecycle_gates']['runner_verified'])

    def test_preserve_existing_bounded_observations(self):
        raw = passing_raw()
        raw['warehouse_runtime_identity'] = {'status': 'unavailable', 'secret': 'SECRET'}
        raw['warehouse_process_observation'] = {'status': 'unavailable', 'rows': [], 'secret': 'SECRET'}
        result = public.project(raw, passing_runner())
        clean = ordinary.project(dict(raw, state='NOT_RUN'), passing_runner(), require_runner=False)
        for key in public.OBSERVATIONS:
            self.assertEqual(result[key], clean[key])
        self.assertIn('process_group_details', result['warehouse_cleanup_diagnostics'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_auxiliary_cleanup_details_do_not_add_new_physical_gates(self):
        raw = passing_raw()
        detail = raw['warehouse_cleanup_diagnostics']
        detail['process_groups'] = {'status': 'invalid', 'any_forced_or_incomplete': True}
        detail['process_group_details'] = {'status': 'unavailable'}
        detail['final_state_error_categories'] = {'status': 'invalid', 'other_present': True}
        self.assertEqual(public.project(raw, passing_runner())['state'], 'PASS')

    def test_privacy_unknown_fields_and_nested_strings_removed(self):
        secret = '/home/private/token-SECRET-session-sim_1234'
        raw = passing_raw()
        for key in ('path', 'token', 'stdout', 'stderr', 'error', 'session_id', 'pid', 'plan'):
            raw[key] = secret
        raw['warehouse_diagnostics']['harness'].update(error=secret, phase=secret)
        raw['warehouse_diagnostics']['lifecycle']['error'] = secret
        raw['warehouse_cleanup_diagnostics']['process_group_details'] = {
            'status': 'available', 'groups': [{'component': secret, 'signals': [secret],
                                             'pid': 987654321, 'command': secret}],
            'leaves': {'private': secret}}
        result = public.project(raw, dict(passing_runner(), token=secret, stdout=secret))
        encoded = json.dumps(result, allow_nan=False)
        self.assertNotIn(secret, encoded)
        self.assertNotIn('987654321', encoded)
        self.assertEqual(public.project(result), result)
        self.assertLess(len(encoded), 50000)

    def test_fuzz_malformed_fields_finite_and_idempotent(self):
        rng = random.Random(20261023)
        values = [None, False, True, 0, -1, 1, 0.0, float('nan'), float('inf'),
                  [], {}, ['SECRET'], {'token': 'SECRET'}, 'SECRET', 'PASS', 'available']
        keys = tuple(passing_raw()) + ('warehouse_process_observation', 'warehouse_runtime_identity',
                                      'startup_diagnostics', 'warehouse_startup_observation')
        for _ in range(300):
            raw = passing_raw()
            for key in rng.sample(keys, 5):
                raw[key] = rng.choice(values)
            result = public.project(raw, passing_runner())
            encoded = json.dumps(result, allow_nan=False)
            self.assertNotIn('SECRET', encoded)
            self.assertEqual(public.project(result), result)
            self.assertEqual(result['route_acceptance'], 'NOT_RUN')

    def test_pure_no_mutation_no_io_no_native_or_network(self):
        raw = passing_raw()
        runner = passing_runner()
        before = copy.deepcopy((raw, runner))
        with (patch('builtins.open', side_effect=AssertionError('no file read')),
              patch('subprocess.Popen', side_effect=AssertionError('no subprocess')),
              patch('urllib.request.urlopen', side_effect=AssertionError('no network'))):
            result = public.project(raw, runner)
        self.assertEqual((raw, runner), before)
        self.assertEqual(result['state'], 'PASS')

    def test_ordinary_reducer_always_called_not_run(self):
        original = ordinary.project
        with patch.object(ordinary, 'project', wraps=original) as observed:
            result = public.project(passing_raw(), passing_runner())
        self.assertEqual(result['state'], 'PASS')
        self.assertEqual(observed.call_args.args[0]['state'], 'NOT_RUN')
        self.assertFalse(observed.call_args.kwargs['require_runner'])

    def test_runtime_cancellation_forwarded_and_not_pass(self):
        error = KeyboardInterrupt()
        pending = []
        with patch.object(ordinary.runtime_identity, 'sanitize', side_effect=error):
            result = public.project(passing_raw(), passing_runner(),
                                    runtime_identity_cancellation=pending.append)
        self.assertIn(error, pending)
        self.assertEqual(result['state'], 'ERROR')
        with patch.object(ordinary.runtime_identity, 'sanitize', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                public.project(passing_raw(), passing_runner())

    def test_cli_preserves_exact_read_and_projection_cancellations(self):
        class Stop(BaseException):
            pass
        for error in (KeyboardInterrupt(), SystemExit(), RuntimeError('interrupted'), Stop()):
            for target in ('input_read', 'runner_read', 'projection'):
                args = ['summary', '--input', '/unused/input.json']
                if target == 'runner_read':
                    args += ['--runner-status', '/unused/runner.json']
                with self.subTest(target=target, kind=type(error).__name__):
                    with patch('sys.argv', args), patch('sys.stdout', new_callable=io.StringIO):
                        if target == 'projection':
                            with patch.object(Path, 'read_text', return_value='{}'), patch.object(public, 'project', side_effect=error):
                                with self.assertRaises(type(error)) as raised:
                                    public.main()
                        else:
                            with patch.object(Path, 'read_text', side_effect=error):
                                with self.assertRaises(type(error)) as raised:
                                    public.main()
                    self.assertIs(raised.exception, error)

    def test_fixed_fallback_preserves_first_deferred_cancellation(self):
        class Stop(BaseException):
            pass
        for error in (KeyboardInterrupt(), SystemExit(), RuntimeError('interrupted'), Stop()):
            pending = []
            with patch.object(ordinary, 'project', side_effect=[error, ValueError('second failure')]) as sanitizer:
                result = public.project(passing_raw(), passing_runner(),
                                        runtime_identity_cancellation=pending.append)
            self.assertEqual(pending, [error])
            self.assertIs(pending[0], error)
            self.assertEqual(sanitizer.call_count, 1)
            self.assertEqual(result['state'], 'ERROR')
            self.assertEqual(result['route_acceptance'], 'NOT_RUN')
            self.assertEqual(public.project(result), result)
            with patch.object(ordinary, 'project', side_effect=error):
                with self.assertRaises(type(error)) as raised:
                    public.project(passing_raw(), passing_runner())
            self.assertIs(raised.exception, error)

    def test_fixed_fallback_is_closed_and_does_not_mask_ordinary_error(self):
        with patch.object(ordinary, 'project', side_effect=ValueError('SECRET')) as sanitizer:
            result = public.project(passing_raw(), passing_runner())
        self.assertEqual(sanitizer.call_count, 1)
        self.assertEqual(result['state'], 'ERROR')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(public.project(result), result)

    def test_cli_two_pass_and_missing_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source, runner = path / 'input.json', path / 'runner.json'
            source.write_text(json.dumps(public.project(passing_raw(), require_runner=False)))
            runner.write_text(json.dumps(passing_runner()))
            args = ['summary', '--input', str(source), '--runner-status', str(runner)]
            with patch('sys.argv', args), patch('sys.stdout', new_callable=io.StringIO) as output:
                code = public.main()
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result['state'], 'PASS')
            self.assertEqual(result['route_acceptance'], 'NOT_RUN')
            for invalid in ('not-json', '[]', 'null', '{"state":"PASS"}'):
                runner.write_text(invalid)
                with patch('sys.argv', args), patch('sys.stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(public.main(), 1)
                self.assertNotEqual(json.loads(output.getvalue())['state'], 'PASS')
            source.unlink()
            with patch('sys.argv', args), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(public.main(), 1)
            self.assertNotEqual(json.loads(output.getvalue())['state'], 'PASS')


if __name__ == '__main__':
    unittest.main()
