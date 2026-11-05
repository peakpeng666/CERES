"""Diagnostic start/ready/ordinary-stop driver. Never submit or execute a route.

This is deliberately separate from warehouse_lifecycle. A successful return is
only a lifecycle result, not a physical replay or the full six-predicate cleanup
acceptance result. API, clocks, callbacks and persistence are injectable.
"""
import math
import re
import time

from warehouse_diagnostics import STATES, failure as describe_failure, section
from startup_observer import is_cancellation


POST_WALL_S = 60
READY_WALL_S = 1200
DELETE_WALL_S = 180
CLEANUP_POLL_WALL_S = 240
POLL_WALL_S = 5
STARTUP_BUDGET_S = POST_WALL_S + READY_WALL_S
CLEANUP_BUDGET_S = POLL_WALL_S + DELETE_WALL_S + CLEANUP_POLL_WALL_S
FINALIZATION_RESERVE_S = 480
MIN_START_ENVELOPE_S = STARTUP_BUDGET_S + CLEANUP_BUDGET_S + FINALIZATION_RESERVE_S


class Rejected(RuntimeError):
    pass


def _start_payload():
    # Exact original payload; return a fresh object so an API cannot mutate it
    # for subsequent invocations.
    return {'world': 'warehouse',
            'spawn': {'x': -5.08, 'y': -.75, 'yaw': 1.570796},
            'gui': False, 'rviz': False, 'localization': 'ground_truth'}


def _positive_finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


class LifecycleOnlyAPI:
    """Hard fail-closed boundary before the supplied transport is invoked."""
    _allowed = frozenset({('POST', '/sim/session'),
                          ('GET', '/sim/state'),
                          ('DELETE', '/sim/session')})

    def __init__(self, api):
        self._api = api

    def __call__(self, method, path, data=None, *, timeout):
        if (method, path) not in self._allowed:
            raise Rejected('lifecycle_api_not_permitted')
        if not _positive_finite(timeout):
            raise Rejected('lifecycle_api_timeout_invalid')
        if method == 'POST':
            if data != _start_payload():
                raise Rejected('lifecycle_session_payload_changed')
        elif data is not None:
            raise Rejected('lifecycle_api_body_not_permitted')
        if data is None:
            return self._api(method, path, timeout=timeout)
        return self._api(method, path, data, timeout=timeout)


def drive(api, run_id, plan, guard, save, clock=time.monotonic,
          sleep=time.sleep, on_session=None, *,
          startup_budget_s=STARTUP_BUDGET_S,
          cleanup_budget_s=CLEANUP_BUDGET_S, absolute_deadline=None):
    """Start, await ready/failed/stopped, and always attempt ordinary cleanup.

    run_id and plan are accepted for harness compatibility but never submitted.
    Budget arguments are positive finite wall seconds. Startup and cleanup have
    independent monotonic deadlines; operation limits remain 60/1200/180/240.
    Production callers must pass the runner's absolute monotonic child deadline.
    After preflight, starting requires the full 2165-second envelope, including
    480 seconds reserved exclusively for gateway shutdown and finalization.
    Session operations never use that reserve. None is for pure tests only.
    The transport must honor its timeout. Ordinary errors are encoded; the exact
    first cancellation object is propagated after cleanup and persistence. An
    earlier ordinary failure remains the primary diagnostic even if cancellation
    arrives during cleanup. No persistence error replaces an earlier exception.
    """
    if not all(_positive_finite(v) for v in
               (startup_budget_s, cleanup_budget_s)):
        raise ValueError('lifecycle_budget_invalid')
    if absolute_deadline is not None and (type(absolute_deadline) not in (int, float)
                                          or not math.isfinite(absolute_deadline)):
        raise ValueError('lifecycle_deadline_invalid')
    api = LifecycleOnlyAPI(api)
    began = clock()
    session_deadline = (absolute_deadline - FINALIZATION_RESERVE_S
                        if absolute_deadline is not None else None)
    start_envelope_admitted = False
    attempted = False
    ready_observed = False
    cleanup_passed = False
    sid = None
    stop = {}
    primary = None
    cancellation = None
    diagnostic = {'phase': 'preflight', 'session_id_observed': False}

    def mark(phase):
        diagnostic['phase'] = phase

    def record(exc, origin='failure'):
        nonlocal primary, cancellation
        detail = describe_failure(exc)
        if primary is None:
            primary = exc
            diagnostic.update(failure_phase=diagnostic['phase'], **detail)
        if cancellation is None and is_cancellation(exc):
            cancellation = exc
        if origin != 'failure':
            diagnostic.update({origin + '_failure_code': detail['failure_code'],
                               origin + '_exception_kind': detail['exception_kind'],
                               origin + '_http_status': detail['http_status']})

    def remaining(deadline, limit):
        value = min(limit, deadline - clock())
        if not _positive_finite(value):
            raise Rejected('lifecycle_timeout')
        return value

    def stage_deadline(budget):
        end = clock() + budget
        return min(end, session_deadline) if session_deadline is not None else end

    def remember(row, cleanup=False):
        nonlocal sid
        if not isinstance(row, dict):
            raise Rejected('session_identifier_invalid')
        phase = row.get('phase')
        diagnostic['cleanup_session_phase' if cleanup else 'last_session_phase'] = (
            phase if isinstance(phase, str) and phase in STATES else 'unknown')
        value = row.get('session_id')
        if value is None:
            return
        if (not isinstance(value, str) or len(value) > 128 or
                not re.fullmatch(r'sim_[A-Za-z0-9_]+', value)):
            raise Rejected('session_identifier_invalid')
        if sid is not None and sid != value:
            raise Rejected('session_identity_changed')
        sid = value
        diagnostic['session_id_observed'] = True
        if on_session is not None:
            on_session(sid)

    def poll(deadline, limit, cleanup=False):
        nonlocal stop
        end = min(deadline, clock() + limit)
        while True:
            timeout = remaining(end, POLL_WALL_S)
            row = api('GET', '/sim/state', timeout=timeout)
            if cleanup:
                stop = row
            remember(row, cleanup=cleanup)
            # A response at or beyond its deadline is not timely evidence.
            if clock() >= end:
                raise Rejected('lifecycle_timeout')
            terminal = ('stopped', 'failed') if cleanup else ('ready', 'failed', 'stopped')
            if row.get('phase') in terminal:
                return row
            sleep(remaining(end, 1))

    def ordinary_cleanup():
        nonlocal stop, cleanup_passed
        cleanup_deadline = stage_deadline(min(cleanup_budget_s, CLEANUP_BUDGET_S))
        if sid is None:
            diagnostic['identity_probe_attempted'] = True
            try:
                remember(api('GET', '/sim/state',
                             timeout=remaining(cleanup_deadline, POLL_WALL_S)))
            except BaseException as exc:
                # A failed identity probe must never prevent DELETE, provided
                # time remains. Never issue a zero-timeout request.
                detail = describe_failure(exc)
                diagnostic.update(identity_probe_exception_kind=detail['exception_kind'],
                                  identity_probe_http_status=detail['http_status'])
                if is_cancellation(exc):
                    record(exc, 'cleanup')
        mark('session_stop')
        try:
            stop = api('DELETE', '/sim/session',
                       timeout=remaining(cleanup_deadline, DELETE_WALL_S))
            remember(stop, cleanup=True)
        except BaseException as exc:
            record(exc, 'cleanup')
        # Even an uncertain DELETE or a bad response is followed by the
        # ordinary state poll while budget remains. No signals or kills.
        mark('cleanup_wait')
        try:
            stop = poll(cleanup_deadline, CLEANUP_POLL_WALL_S, cleanup=True)
            if stop.get('session_id') not in (None, sid):
                raise Rejected('cleanup_identity_mismatch')
            if stop.get('phase') != 'stopped' or stop.get('errors'):
                raise Rejected('session_cleanup_failed')
            cleanup_passed = True
        except BaseException as exc:
            record(exc, 'cleanup')

    try:
        guard()
        if (absolute_deadline is not None and
                absolute_deadline - clock() < MIN_START_ENVELOPE_S):
            raise Rejected('lifecycle_timeout')
        start_envelope_admitted = True
        # Preflight time counts against the absolute runner budget, not the
        # fresh startup stage. Larger budgets cannot extend operation limits.
        startup_deadline = stage_deadline(min(startup_budget_s, STARTUP_BUDGET_S))
        mark('session_start')
        timeout = remaining(startup_deadline, POST_WALL_S)
        attempted = True  # An exception now leaves the POST outcome uncertain.
        state = api('POST', '/sim/session', _start_payload(), timeout=timeout)
        remember(state)
        mark('session_ready')
        state = poll(startup_deadline, READY_WALL_S)
        if (state.get('phase') != 'ready' or state.get('session_id') != sid or
                not isinstance(sid, str) or
                not re.fullmatch(r'sim_[A-Za-z0-9_]+', sid)):
            raise Rejected('session_not_ready')
        ready_observed = True
        guard()
        remaining(startup_deadline, POST_WALL_S)
    except BaseException as exc:
        record(exc)
    finally:
        if attempted:
            try:
                ordinary_cleanup()
            except BaseException as exc:
                record(exc, 'cleanup')

    def outcome():
        if cancellation is not None:
            return 'interrupted'
        return 'passed' if primary is None else 'failed'

    try:
        diagnostic['elapsed_wall_s'] = max(0, clock() - began)
    except BaseException as exc:
        record(exc)
    diagnostic = section(diagnostic)
    diagnostic.update(startup_budget_s=startup_budget_s,
                      cleanup_budget_s=cleanup_budget_s,
                      absolute_deadline_supplied=absolute_deadline is not None,
                      finalization_reserve_s=FINALIZATION_RESERVE_S,
                      required_start_envelope_s=MIN_START_ENVELOPE_S,
                      start_envelope_admitted=start_envelope_admitted,
                      session_start_attempted=attempted,
                      ready_observed=ready_observed,
                      ordinary_cleanup_passed=cleanup_passed,
                      task_not_submitted=True,
                      route_acceptance='NOT_RUN',
                      lifecycle_outcome=outcome(),
                      artifact_save_failed=False)
    row = {'result': {}, 'session_id': sid,
           'stop': stop if isinstance(stop, dict) else {},
           'passed': primary is None,
           'ready_observed': ready_observed,
           'task_not_submitted': True,
           'route_acceptance': 'NOT_RUN',
           'lifecycle_outcome': outcome(),
           'failure_kind': type(primary).__name__ if primary is not None else None,
           'diagnostics': diagnostic}
    try:
        save('lifecycle-only.json', row)
    except BaseException as exc:
        record(exc, 'save')
        diagnostic['artifact_save_failed'] = True
        diagnostic['lifecycle_outcome'] = outcome()
        row.update(passed=False, lifecycle_outcome=outcome(),
                   failure_kind=type(primary).__name__)
    if cancellation is not None:
        raise cancellation
    return row
