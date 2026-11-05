"""Pure fake-clock/transport tests. No gateway, ROS, network or subprocesses."""
import copy
import math
import unittest

from warehouse_lifecycle_only import (
    CLEANUP_BUDGET_S, STARTUP_BUDGET_S, FINALIZATION_RESERVE_S,
    MIN_START_ENVELOPE_S, LifecycleOnlyAPI, Rejected, drive)


START = {'world': 'warehouse',
         'spawn': {'x': -5.08, 'y': -.75, 'yaw': 1.570796},
         'gui': False, 'rviz': False, 'localization': 'ground_truth'}
PLAN = {'path': [[0, 0], [1, 0]], 'tasks': [{'task': 1}], 'budget_s': 649.6}


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        assert math.isfinite(seconds) and seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


class API:
    def __init__(self, clock, hook=None):
        self.clock = clock
        self.hook = hook
        self.calls = []
        self.stopping = False

    def __call__(self, method, path, data=None, *, timeout):
        assert math.isfinite(timeout) and timeout > 0
        self.calls.append((self.clock.now, method, path, copy.deepcopy(data), timeout))
        if method == 'DELETE':
            self.stopping = True
        if self.hook is not None:
            value = self.hook(self, method, path, data, timeout)
            if value is not None:
                return value
        if method == 'POST' and path == '/sim/session':
            return {'phase': 'starting', 'session_id': 'sim_test'}
        if method == 'DELETE' and path == '/sim/session':
            return {'phase': 'stopping', 'session_id': 'sim_test'}
        if method == 'GET' and path == '/sim/state':
            return {'phase': 'stopped' if self.stopping else 'ready',
                    'session_id': 'sim_test', 'errors': []}
        raise AssertionError('Non-lifecycle API reached the fake transport')


class LifecycleOnlyTests(unittest.TestCase):
    def run_case(self, hook=None, guard=None, saver=None, on_session=None, **budgets):
        clock = Clock()
        api = API(clock, hook)
        saved = {}
        def save(name, value):
            saved[name] = copy.deepcopy(value)
            if saver is not None:
                saver(name, value)
        result = drive(api, 'do_not_submit', PLAN, guard or (lambda: None), save,
                       clock.clock, clock.sleep, on_session, **budgets)
        return result, api, clock, saved

    def assert_no_runs(self, api):
        self.assertTrue(all((method, path) in {
            ('POST', '/sim/session'), ('GET', '/sim/state'),
            ('DELETE', '/sim/session')} for _, method, path, _, _ in api.calls))

    def test_ready_stops_without_any_execution_or_plan_mutation(self):
        original = copy.deepcopy(PLAN)
        observed = []
        row, api, _, saved = self.run_case(on_session=observed.append)
        self.assertTrue(row['passed'])
        self.assertTrue(row['ready_observed'])
        self.assertEqual(row['lifecycle_outcome'], 'passed')
        self.assertEqual(row['result'], {})
        self.assertEqual(row['route_acceptance'], 'NOT_RUN')
        self.assertIs(row['task_not_submitted'], True)
        self.assertEqual(PLAN, original)
        self.assert_no_runs(api)
        self.assertEqual(api.calls[0][1:], ('POST', '/sim/session', START, 60))
        self.assertEqual(api.calls[2][1:], ('DELETE', '/sim/session', None, 180))
        self.assertEqual(observed, ['sim_test'] * 4)
        self.assertEqual(saved['lifecycle-only.json'], row)

    def test_plan_and_run_id_are_never_accessed_or_transmitted(self):
        class Unusable:
            def __getattribute__(self, name):
                raise AssertionError('Plan/run ID must not be used')
        clock = Clock(); api = API(clock)
        row = drive(api, Unusable(), Unusable(), lambda: None, lambda *args: None,
                    clock.clock, clock.sleep)
        self.assertTrue(row['passed'])
        self.assert_no_runs(api)

    def test_ready_rule_failed_and_stopped_do_not_pass(self):
        for phase in ('failed', 'stopped'):
            with self.subTest(phase=phase):
                def hook(api, method, path, data, timeout):
                    if method == 'GET' and not api.stopping:
                        return {'phase': phase, 'session_id': 'sim_test'}
                row, api, _, _ = self.run_case(hook)
                self.assertFalse(row['passed'])
                self.assertFalse(row['ready_observed'])
                self.assertEqual(row['diagnostics']['failure_code'], 'session_not_ready')
                self.assertTrue(api.stopping)
                self.assert_no_runs(api)

    def test_unknown_phase_polls_until_ready(self):
        def hook(api, method, path, data, timeout):
            if method == 'GET' and not api.stopping and api.clock.now < 7:
                return {'phase': 'undocking', 'session_id': 'sim_test'}
        row, _, clock, _ = self.run_case(hook)
        self.assertTrue(row['passed'])
        self.assertEqual(clock.now, 7)

    def test_ready_at_1199_passes_but_1200_does_not(self):
        for at, passed in ((1199, True), (1200, False)):
            with self.subTest(at=at):
                def hook(api, method, path, data, timeout):
                    if method == 'GET' and not api.stopping and api.clock.now < at:
                        return {'phase': 'starting', 'session_id': 'sim_test'}
                row, api, clock, _ = self.run_case(hook)
                self.assertEqual(row['passed'], passed)
                self.assertEqual(clock.now, at)
                self.assertTrue(api.stopping)

    def test_ready_response_received_at_deadline_is_rejected(self):
        def hook(api, method, path, data, timeout):
            if method == 'GET' and not api.stopping:
                api.clock.now = 1200
        row, api, _, _ = self.run_case(hook)
        self.assertFalse(row['passed'])
        self.assertEqual(row['diagnostics']['failure_code'], 'lifecycle_timeout')
        self.assertTrue(api.stopping)

    def test_startup_total_budget_caps_post_and_each_ready_poll(self):
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                self.assertEqual(timeout, 2.5)
                api.clock.now += 2
            if method == 'GET' and not api.stopping:
                self.assertLessEqual(timeout, 2.5 - api.clock.now)
                return {'phase': 'starting', 'session_id': 'sim_test'}
        row, api, clock, _ = self.run_case(hook, startup_budget_s=2.5)
        self.assertFalse(row['passed'])
        self.assertEqual(clock.now, 2.5)
        self.assertTrue(api.stopping)
        self.assertEqual([c[4] for c in api.calls if c[1] == 'GET'][0], .5)

    def test_start_timeout_consumes_startup_budget_but_cleanup_is_independent(self):
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                api.clock.now += timeout
                raise TimeoutError('uncertain')
        row, api, clock, _ = self.run_case(hook, startup_budget_s=1)
        self.assertFalse(row['passed'])
        self.assertEqual(clock.now, 1)
        self.assertEqual([c[4] for c in api.calls if c[1] == 'DELETE'], [180])
        self.assertTrue(row['diagnostics']['identity_probe_attempted'])

    def test_uncertain_post_probes_identity_then_deletes(self):
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                raise TimeoutError('private error text')
        row, api, _, saved = self.run_case(hook)
        self.assertFalse(row['passed'])
        self.assertEqual(row['session_id'], 'sim_test')
        self.assertEqual([(c[1], c[2]) for c in api.calls], [
            ('POST', '/sim/session'), ('GET', '/sim/state'),
            ('DELETE', '/sim/session'), ('GET', '/sim/state')])
        self.assertNotIn('private error text', str(saved))

    def test_identity_probe_failure_cannot_skip_delete(self):
        def hook(api, method, path, data, timeout):
            if method == 'POST' or method == 'GET' and not api.stopping:
                raise TimeoutError('private')
        row, api, _, _ = self.run_case(hook)
        self.assertFalse(row['passed'])
        self.assertTrue(api.stopping)
        self.assertEqual(row['diagnostics']['identity_probe_exception_kind'], 'timeout')

    def test_invalid_identifiers_and_identity_changes_rejected(self):
        for value in ('wrong', '', 'sim_a/b', 'sim_' + 'x' * 125, 17, [], 'sim_other'):
            with self.subTest(value=value):
                def hook(api, method, path, data, timeout):
                    if method == 'GET' and not api.stopping:
                        return {'phase': 'ready', 'session_id': value}
                row, api, _, _ = self.run_case(hook)
                self.assertFalse(row['passed'])
                self.assertTrue(api.stopping)
                self.assertIn(row['diagnostics']['failure_code'],
                              ('session_identifier_invalid', 'session_identity_changed'))

    def test_no_session_identifier_never_passes_readiness(self):
        def hook(api, method, path, data, timeout):
            if not api.stopping:
                return {'phase': 'ready'}
        row, api, _, _ = self.run_case(hook)
        self.assertFalse(row['passed'])
        self.assertTrue(api.stopping)

    def test_non_dictionary_start_response_still_cleans_up(self):
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                return []
        row, api, _, _ = self.run_case(hook)
        self.assertFalse(row['passed'])
        self.assertTrue(api.stopping)

    def test_cleanup_original_falsy_errors_semantics(self):
        for errors in (None, [], '', False, 0, {}):
            with self.subTest(errors=errors):
                def hook(api, method, path, data, timeout):
                    if method == 'GET' and api.stopping:
                        return {'phase': 'stopped', 'errors': errors}
                row, _, _, _ = self.run_case(hook)
                self.assertTrue(row['passed'])
        def missing(api, method, path, data, timeout):
            if method == 'GET' and api.stopping:
                return {'phase': 'stopped'}
        self.assertTrue(self.run_case(missing)[0]['passed'])

    def test_cleanup_errors_failed_and_identity_changes_fail(self):
        for state in ({'phase': 'stopped', 'errors': ['failed']},
                      {'phase': 'failed'},
                      {'phase': 'stopped', 'session_id': 'sim_other'}):
            with self.subTest(state=state):
                def hook(api, method, path, data, timeout):
                    if method == 'GET' and api.stopping:
                        return state
                row, _, _, _ = self.run_case(hook)
                self.assertFalse(row['passed'])

    def test_malformed_stop_response_keeps_return_shape_safe(self):
        def hook(api, method, path, data, timeout):
            if api.stopping:
                return []
        row, _, _, _ = self.run_case(hook)
        self.assertFalse(row['passed'])
        self.assertEqual(row['stop'], {})
        self.assertEqual(row['diagnostics']['failure_code'], 'session_identifier_invalid')

    def test_uncertain_delete_is_followed_by_ordinary_poll(self):
        def hook(api, method, path, data, timeout):
            if method == 'DELETE':
                raise TimeoutError('uncertain stop')
        row, api, _, _ = self.run_case(hook)
        self.assertFalse(row['passed'])
        self.assertEqual(api.calls[-1][1:3], ('GET', '/sim/state'))
        self.assertEqual(row['stop']['phase'], 'stopped')

    def test_cleanup_poll_240_and_each_timeout_bounded(self):
        def hook(api, method, path, data, timeout):
            if method == 'GET' and api.stopping:
                self.assertLessEqual(timeout, 240 - api.clock.now)
                return {'phase': 'stopping', 'session_id': 'sim_test'}
        row, api, clock, _ = self.run_case(hook)
        self.assertFalse(row['passed'])
        self.assertEqual(clock.now, 240)
        self.assertEqual(api.calls[-1][4], 1)

    def test_cleanup_total_budget_bounds_delete_and_poll(self):
        def hook(api, method, path, data, timeout):
            if method == 'DELETE':
                self.assertEqual(timeout, 1.25)
                api.clock.now += 1
            if method == 'GET' and api.stopping:
                self.assertLessEqual(timeout, 1.25 - api.clock.now)
                return {'phase': 'stopping'}
        row, _, clock, _ = self.run_case(hook, cleanup_budget_s=1.25)
        self.assertFalse(row['passed'])
        self.assertEqual(clock.now, 1.25)

    def test_exhausted_cleanup_budget_does_not_issue_another_request(self):
        def hook(api, method, path, data, timeout):
            if method == 'DELETE':
                api.clock.now += timeout
        row, api, _, _ = self.run_case(hook, cleanup_budget_s=.1)
        self.assertFalse(row['passed'])
        self.assertEqual(api.calls[-1][1], 'DELETE')

    def test_exhausted_probe_budget_does_not_issue_nonpositive_delete(self):
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                raise TimeoutError('uncertain')
            if method == 'GET':
                api.clock.now += timeout
                raise TimeoutError('probe')
        row, api, _, _ = self.run_case(hook, cleanup_budget_s=.1)
        self.assertFalse(row['passed'])
        self.assertEqual(len(api.calls), 2)
        self.assertFalse(api.stopping)

    def test_cleanup_late_terminal_response_cannot_pass(self):
        def hook(api, method, path, data, timeout):
            if method == 'GET' and api.stopping:
                api.clock.now += 240
        row, _, _, _ = self.run_case(hook)
        self.assertFalse(row['passed'])

    def test_invalid_budgets_fail_before_any_api_call(self):
        for key in ('startup_budget_s', 'cleanup_budget_s'):
            for value in (0, -1, float('inf'), float('-inf'), float('nan'), True, '1', None):
                with self.subTest(key=key, value=value):
                    clock = Clock(); api = API(clock)
                    with self.assertRaises(ValueError):
                        drive(api, None, None, lambda: None, lambda *a: None,
                              clock.clock, clock.sleep, **{key: value})
                    self.assertEqual(api.calls, [])

    def test_larger_budgets_do_not_extend_original_limits(self):
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                api.clock.now += 60
            if method == 'GET':
                return {'phase': 'stopping' if api.stopping else 'starting',
                        'session_id': 'sim_test'}
        row, api, clock, _ = self.run_case(hook, startup_budget_s=1e100,
                                         cleanup_budget_s=1e100)
        self.assertFalse(row['passed'])
        self.assertEqual(clock.now, STARTUP_BUDGET_S + 240)
        self.assertEqual(max(c[4] for c in api.calls if c[1] == 'POST'), 60)
        self.assertEqual(max(c[4] for c in api.calls if c[1] == 'DELETE'), 180)
        self.assertLessEqual(clock.now, STARTUP_BUDGET_S + CLEANUP_BUDGET_S)

    def test_guard_failure_before_post_does_not_stop_unowned_session(self):
        def guard():
            raise ValueError('private drift')
        row, api, _, _ = self.run_case(guard=guard)
        self.assertFalse(row['passed'])
        self.assertEqual(api.calls, [])

    def test_second_guard_failure_cleans_up_without_route(self):
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError('drift')
        row, api, _, _ = self.run_case(guard=guard)
        self.assertFalse(row['passed'])
        self.assertTrue(row['ready_observed'])
        self.assertTrue(api.stopping)
        self.assert_no_runs(api)

    def test_cancellation_objects_preserved_after_cleanup_and_save(self):
        class CustomCancellation(BaseException):
            pass
        for original in (RuntimeError('interrupted'), KeyboardInterrupt(),
                         SystemExit(19), CustomCancellation('cancel')):
            for stage in ('post', 'ready', 'callback', 'guard', 'delete', 'cleanup', 'save'):
                with self.subTest(original=type(original).__name__, stage=stage):
                    clock = Clock(); saved = []; observed = 0; guard_count = 0
                    def hook(api, method, path, data, timeout):
                        if ((stage == 'post' and method == 'POST') or
                            (stage == 'ready' and method == 'GET' and not api.stopping) or
                            (stage == 'delete' and method == 'DELETE') or
                            (stage == 'cleanup' and method == 'GET' and api.stopping)):
                            raise original
                    api = API(clock, hook)
                    def callback(sid):
                        nonlocal observed
                        observed += 1
                        if stage == 'callback' and observed == 1:
                            raise original
                    def guard():
                        nonlocal guard_count
                        guard_count += 1
                        if stage == 'guard' and guard_count == 2:
                            raise original
                    def save(name, row):
                        saved.append(copy.deepcopy(row))
                        if stage == 'save':
                            raise original
                    with self.assertRaises(type(original)) as caught:
                        drive(api, None, None, guard, save, clock.clock,
                              clock.sleep, callback)
                    self.assertIs(caught.exception, original)
                    self.assertTrue(api.stopping)
                    self.assertEqual(len(saved), 1)
                    self.assert_no_runs(api)

    def test_cancellation_is_not_replaced_by_cleanup_or_save_errors(self):
        original = RuntimeError('interrupted'); clock = Clock()
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                raise original
            raise ValueError('secondary cleanup failure')
        api = API(clock, hook)
        def save(*args):
            raise OSError('secondary save failure')
        with self.assertRaises(RuntimeError) as caught:
            drive(api, None, None, lambda: None, save, clock.clock, clock.sleep)
        self.assertIs(caught.exception, original)
        self.assertTrue(api.stopping)

    def test_ordinary_primary_diagnostics_survive_cleanup_and_save_failures(self):
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                raise TimeoutError('original')
            raise ValueError('cleanup')
        def saver(*args):
            raise OSError('save')
        row, api, _, _ = self.run_case(hook, saver=saver)
        self.assertFalse(row['passed'])
        self.assertEqual(row['failure_kind'], 'TimeoutError')
        self.assertEqual(row['diagnostics']['exception_kind'], 'timeout')
        self.assertTrue(row['diagnostics']['artifact_save_failed'])
        self.assertTrue(api.stopping)

    def test_save_failure_after_success_is_encoded_not_silent(self):
        def saver(*args):
            raise OSError('private disk path')
        row, _, _, _ = self.run_case(saver=saver)
        self.assertFalse(row['passed'])
        self.assertEqual(row['lifecycle_outcome'], 'failed')
        self.assertEqual(row['diagnostics']['exception_kind'], 'os')
        self.assertTrue(row['diagnostics']['artifact_save_failed'])

    def test_cleanup_cancellation_propagates_even_after_ordinary_primary_failure(self):
        cancellation = SystemExit(23); clock = Clock(); saved = []
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                raise TimeoutError('primary')
            if method == 'DELETE':
                raise cancellation
        api = API(clock, hook)
        with self.assertRaises(SystemExit) as caught:
            drive(api, None, None, lambda: None,
                  lambda name, row: saved.append(copy.deepcopy(row)),
                  clock.clock, clock.sleep)
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(saved[0]['failure_kind'], 'TimeoutError')
        self.assertEqual(saved[0]['lifecycle_outcome'], 'interrupted')
        self.assertEqual(api.calls[-1][1:3], ('GET', '/sim/state'))

    def test_probe_cancellation_cannot_skip_delete_or_replace_primary_diagnostics(self):
        original = KeyboardInterrupt(); clock = Clock(); saved = []
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                raise TimeoutError('original failure')
            if method == 'GET' and not api.stopping:
                raise original
        api = API(clock, hook)
        with self.assertRaises(KeyboardInterrupt) as caught:
            drive(api, None, None, lambda: None,
                  lambda name, row: saved.append(copy.deepcopy(row)),
                  clock.clock, clock.sleep)
        self.assertIs(caught.exception, original)
        self.assertTrue(api.stopping)
        self.assertEqual(saved[0]['failure_kind'], 'TimeoutError')

    def test_first_cancellation_survives_later_cancellations(self):
        original = RuntimeError('interrupted'); clock = Clock()
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                raise original
            if method == 'DELETE':
                raise KeyboardInterrupt()
        api = API(clock, hook)
        def save(*args):
            raise SystemExit(31)
        with self.assertRaises(RuntimeError) as caught:
            drive(api, None, None, lambda: None, save, clock.clock, clock.sleep)
        self.assertIs(caught.exception, original)
        self.assertTrue(api.stopping)

    def test_save_cancellation_propagates_after_ordinary_failure(self):
        original = SystemExit(42)
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                raise TimeoutError('ordinary original')
        def saver(*args):
            raise original
        with self.assertRaises(SystemExit) as caught:
            self.run_case(hook, saver=saver)
        self.assertIs(caught.exception, original)

    def test_runtime_error_other_than_exact_interrupted_is_ordinary(self):
        class RuntimeSubclass(RuntimeError):
            pass
        for exc in (RuntimeError('interrupted later'), RuntimeSubclass('interrupted'),
                    RuntimeError(), RuntimeError('interrupted', None)):
            with self.subTest(exc=type(exc).__name__, args=exc.args):
                def hook(api, method, path, data, timeout):
                    if method == 'POST':
                        raise exc
                row, api, _, _ = self.run_case(hook)
                self.assertFalse(row['passed'])
                self.assertEqual(row['lifecycle_outcome'], 'failed')
                self.assertTrue(api.stopping)

    def test_cancellation_survives_cleanup_clock_and_elapsed_clock_failures(self):
        original = RuntimeError('interrupted'); clock = Clock(); failed = False; saved = []
        def hook(api, method, path, data, timeout):
            nonlocal failed
            if method == 'POST':
                failed = True
                raise original
        api = API(clock, hook)
        def broken_clock():
            if failed:
                raise OSError('clock failure')
            return clock.now
        with self.assertRaises(RuntimeError) as caught:
            drive(api, None, None, lambda: None,
                  lambda name, row: saved.append(copy.deepcopy(row)),
                  broken_clock, clock.sleep)
        self.assertIs(caught.exception, original)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['failure_kind'], 'RuntimeError')

    def test_guard_cannot_report_success_after_startup_budget_expires(self):
        clock = Clock(); api = API(clock); count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 2:
                clock.now += STARTUP_BUDGET_S
        row = drive(api, None, None, guard, lambda *args: None,
                    clock.clock, clock.sleep)
        self.assertFalse(row['passed'])
        self.assertTrue(api.stopping)

    def test_absolute_deadline_preflight_consumed_3000_of_3600_does_not_start(self):
        clock = Clock(); clock.now = 3000; api = API(clock); saved = []
        row = drive(api, None, None, lambda: None,
                    lambda name, item: saved.append(item), clock.clock,
                    clock.sleep, absolute_deadline=3600)
        self.assertFalse(row['passed'])
        self.assertEqual(api.calls, [])
        self.assertFalse(api.stopping)
        self.assertEqual(row['diagnostics']['failure_phase'], 'preflight')
        self.assertFalse(row['diagnostics']['start_envelope_admitted'])
        self.assertFalse(row['diagnostics']['session_start_attempted'])
        self.assertEqual(row['route_acceptance'], 'NOT_RUN')
        self.assertIs(row['task_not_submitted'], True)
        self.assertEqual(len(saved), 1)

    def test_absolute_deadline_exact_envelope_admitted_and_below_rejected(self):
        self.assertEqual(MIN_START_ENVELOPE_S, 2165)
        self.assertEqual(FINALIZATION_RESERVE_S, 480)
        for remaining, admitted in ((2165, True), (2164.999, False),
                                    (600, False), (480, False), (0, False), (-1, False)):
            with self.subTest(remaining=remaining):
                row, api, _, _ = self.run_case(absolute_deadline=remaining)
                self.assertEqual(row['passed'], admitted)
                self.assertEqual(bool(api.calls), admitted)
                self.assertEqual(row['diagnostics']['start_envelope_admitted'], admitted)
                if not admitted:
                    self.assertEqual(row['diagnostics']['failure_phase'], 'preflight')

    def test_admission_rechecks_after_guard_spends_absolute_budget(self):
        clock = Clock(); api = API(clock)
        def guard():
            clock.now += 3000
        row = drive(api, None, None, guard, lambda *args: None,
                    clock.clock, clock.sleep, absolute_deadline=3600)
        self.assertFalse(row['passed'])
        self.assertEqual(api.calls, [])
        self.assertEqual(clock.now, 3000)
        self.assertEqual(row['diagnostics']['failure_phase'], 'preflight')

    def test_exact_envelope_after_guard_gets_fresh_full_startup_budget(self):
        clock = Clock(); api = API(clock); calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 1:
                clock.now = 3600 - MIN_START_ENVELOPE_S
        row = drive(api, None, None, guard, lambda *args: None,
                    clock.clock, clock.sleep, absolute_deadline=3600)
        self.assertTrue(row['passed'])
        self.assertEqual(api.calls[0][0], 1435)
        self.assertEqual(api.calls[0][4], 60)

    def test_tiny_stage_budgets_do_not_relax_absolute_admission_envelope(self):
        row, api, _, _ = self.run_case(startup_budget_s=.1,
                                     cleanup_budget_s=.1, absolute_deadline=1000)
        self.assertFalse(row['passed'])
        self.assertEqual(api.calls, [])

    def test_invalid_absolute_deadlines_rejected_before_any_call(self):
        for value in (float('inf'), float('-inf'), float('nan'), True, '2165', []):
            with self.subTest(value=value):
                clock = Clock(); api = API(clock)
                with self.assertRaises(ValueError):
                    drive(api, None, None, lambda: None, lambda *args: None,
                          clock.clock, clock.sleep, absolute_deadline=value)
                self.assertEqual(api.calls, [])

    def test_post_ready_delete_and_cleanup_timeouts_respect_absolute_reserve(self):
        clock = Clock(); deadline = MIN_START_ENVELOPE_S
        session_end = deadline - FINALIZATION_RESERVE_S
        def hook(api, method, path, data, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, session_end - api.clock.now)
            if method == 'POST':
                self.assertEqual(timeout, 60)
                api.clock.now += timeout
            elif method == 'DELETE':
                self.assertEqual(timeout, 180)
                api.clock.now += timeout
            elif method == 'GET':
                self.assertLessEqual(timeout, 5)
                if not api.stopping:
                    self.assertLessEqual(timeout, 1260 - api.clock.now)
                    return {'phase': 'starting', 'session_id': 'sim_test'}
                self.assertLessEqual(timeout, 1680 - api.clock.now)
                return {'phase': 'stopping', 'session_id': 'sim_test'}
        api = API(clock, hook)
        row = drive(api, None, None, lambda: None, lambda *args: None,
                    clock.clock, clock.sleep, absolute_deadline=deadline)
        self.assertFalse(row['passed'])
        self.assertEqual(clock.now, 1680)
        self.assertGreaterEqual(deadline - clock.now, FINALIZATION_RESERVE_S)
        self.assertTrue(api.stopping)

    def test_cleanup_total_cap_preserves_reserve_after_late_start_response(self):
        clock = Clock(); deadline = MIN_START_ENVELOPE_S
        session_end = deadline - FINALIZATION_RESERVE_S
        def hook(api, method, path, data, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, session_end - api.clock.now)
            if method == 'POST':
                # A misbehaving/late transport cannot make later operations
                # spend the protected finalization reserve.
                api.clock.now = session_end - .25
                raise TimeoutError('late uncertain POST')
            if method == 'GET' and not api.stopping:
                api.clock.now += .05
            if method == 'DELETE':
                self.assertAlmostEqual(timeout, .2)
                api.clock.now += .1
            if method == 'GET' and api.stopping:
                self.assertAlmostEqual(timeout, .1)
                return {'phase': 'stopping', 'session_id': 'sim_test'}
        api = API(clock, hook)
        row = drive(api, None, None, lambda: None, lambda *args: None,
                    clock.clock, clock.sleep, absolute_deadline=deadline)
        self.assertFalse(row['passed'])
        self.assertEqual(clock.now, session_end)
        self.assertEqual(deadline - clock.now, FINALIZATION_RESERVE_S)
        self.assertTrue(api.stopping)

    def test_no_session_operation_after_absolute_reserve_boundary(self):
        clock = Clock(); deadline = MIN_START_ENVELOPE_S
        session_end = deadline - FINALIZATION_RESERVE_S
        def hook(api, method, path, data, timeout):
            if method == 'POST':
                api.clock.now = session_end
                raise TimeoutError('late uncertain POST')
        api = API(clock, hook)
        row = drive(api, None, None, lambda: None, lambda *args: None,
                    clock.clock, clock.sleep, absolute_deadline=deadline)
        self.assertFalse(row['passed'])
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0][1], 'POST')
        self.assertFalse(api.stopping)


class HardBoundaryTests(unittest.TestCase):
    def test_all_route_and_nonwhitelisted_operations_fail_before_transport(self):
        called = []
        api = LifecycleOnlyAPI(lambda *args, **kwargs: called.append(args))
        for method, path in [('POST', '/sim/runs'), ('GET', '/sim/runs/sim_run_x'),
                             ('POST', '/solve'), ('GET', '/sim/session'),
                             ('DELETE', '/sim/runs'), ('PATCH', '/sim/session'),
                             ('POST', '/sim/session/../runs'),
                             ('POST', '/sim/session?route=1'), ('post', '/sim/session')]:
            with self.subTest(method=method, path=path), self.assertRaises(Rejected):
                api(method, path, START, timeout=1)
        self.assertEqual(called, [])

    def test_wrapper_rejects_payload_changes_and_nonpositive_timeouts(self):
        called = []
        api = LifecycleOnlyAPI(lambda *args, **kwargs: called.append(args))
        for payload in ({}, dict(START, gui=True), dict(START, world='other'),
                        dict(START, route='secret')):
            with self.assertRaises(Rejected):
                api('POST', '/sim/session', payload, timeout=1)
        for timeout in (0, -1, float('inf'), float('nan'), None, True):
            with self.assertRaises(Rejected):
                api('GET', '/sim/state', timeout=timeout)
        with self.assertRaises(Rejected):
            api('DELETE', '/sim/session', {}, timeout=1)
        self.assertEqual(called, [])


if __name__ == '__main__':
    unittest.main()
