"""Optional execution estimates must never turn a solved plan into a timeout."""
import copy
import io
import json
import subprocess
import sys
import time

import pytest

from urp_lab import worker


@pytest.fixture
def request_and_result():
    return ({'scenario': {'problem_class': 'fleet_routing'}, 'instance': {},
             'algorithm_id': 'urp.fake', 'params': {'time_limit_s': 1}},
            {'status': 'feasible', 'routes': [{'path': [[0, 0], [1, 0]]}],
             'objective': {'value': 7}, 'metrics': {'cost': 7}})


def assert_unavailable_preserves_solution(result, before, reason=None):
    actual = copy.deepcopy(result)
    annotation = actual['metrics'].pop('execution_estimate')
    assert actual == before
    assert set(annotation) == {'status', 'reason'}
    assert annotation['status'] == 'unavailable'
    assert annotation['reason'] in {
        'estimate failed', 'estimate timed out', 'estimate budget exhausted',
        'estimate process failed', 'invalid estimate result', 'invalid estimate data',
    }
    if reason:
        assert annotation['reason'] == reason


def test_slow_estimate_is_killed_and_success_is_preserved(monkeypatch, request_and_result):
    request, result = request_and_result
    before = copy.deepcopy(result)
    monkeypatch.setattr(worker, '_ESTIMATE_CODE', 'import time; time.sleep(30)')
    monkeypatch.setattr(worker, 'ESTIMATE_BUDGET_S', 0.1)
    original_popen = subprocess.Popen
    children = []

    def record_child(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(worker.subprocess, 'Popen', record_child)
    started = time.monotonic()
    worker.add_execution_estimate(request, result)
    assert time.monotonic() - started < 1
    assert_unavailable_preserves_solution(result, before)
    assert len(children) == 1
    assert children[0].poll() is not None


def test_expired_budget_does_not_start_child(monkeypatch, request_and_result):
    def forbidden(*args, **kwargs):
        pytest.fail('No estimate should start after the budget expires')
    monkeypatch.setattr(worker.subprocess, 'Popen', forbidden)
    before = copy.deepcopy(request_and_result[1])
    worker.add_execution_estimate(*request_and_result, deadline=time.monotonic() - 1)
    assert_unavailable_preserves_solution(request_and_result[1], before, 'estimate budget exhausted')


@pytest.mark.parametrize('code', [
    'raise RuntimeError("estimate failure")',
    'print("not JSON")',
    'print(\'{"predicted_makespan_s": NaN}\')',
    'print("null")',
])
def test_estimate_failure_preserves_result(monkeypatch, request_and_result, code):
    request, result = request_and_result
    before = copy.deepcopy(result)
    monkeypatch.setattr(worker, '_ESTIMATE_CODE', code)
    worker.add_execution_estimate(request, result)
    assert_unavailable_preserves_solution(result, before)


def test_launch_failure_preserves_result(monkeypatch, request_and_result):
    request, result = request_and_result
    before = copy.deepcopy(result)
    def unavailable(*args, **kwargs):
        raise OSError('process unavailable')
    monkeypatch.setattr(worker.subprocess, 'Popen', unavailable)
    worker.add_execution_estimate(request, result)
    assert_unavailable_preserves_solution(result, before)


@pytest.mark.parametrize('found, promoted', [
    ({'model': 'test', 'scope': 'calibrated', 'predicted_makespan_s': 12.5, 'meetings': 2}, True),
    ({'model': 'test', 'scope': 'unsupported', 'reason': 'outside domain'}, False),
    ({'predicted_makespan_s': -1, 'meetings': 2}, False),
    ({'predicted_makespan_s': True, 'meetings': 2}, False),
])
def test_preserves_metadata_and_only_promotes_valid_metrics(monkeypatch, request_and_result, found, promoted):
    monkeypatch.setattr(worker, '_ESTIMATE_CODE', f'print({json.dumps(found)!r})')
    request, result = request_and_result
    worker.add_execution_estimate(request, result)
    assert result['metrics']['execution_estimate'] == found
    assert ('predicted_makespan_s' in result['metrics']) == promoted
    assert ('route_meetings' in result['metrics']) == promoted


def test_solver_watchdog_disarmed_before_estimate(monkeypatch, request_and_result, capfd):
    request, result = request_and_result
    events = []
    def limit(seconds):
        events.append(('armed', seconds))
        return lambda: events.append(('disarmed', None))
    def estimate(req, res, *, deadline):
        assert events == [('armed', 1.0), ('disarmed', None)]
        assert deadline == worker._STARTED + 1 - worker.OUTPUT_RESERVE_S
        events.append(('estimate', None))
    monkeypatch.setattr(worker, 'limit', limit)
    monkeypatch.setattr(worker, 'run_algorithm', lambda *args: result)
    monkeypatch.setattr(worker, 'add_execution_estimate', estimate)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request)))
    assert worker.main() == 0
    assert json.loads(capfd.readouterr().out) == result


@pytest.mark.parametrize('timer_fallback', [False, True])
def test_real_watchdog_still_times_out_solver(request_and_result, timer_fallback):
    request, _ = request_and_result
    request['params']['time_limit_s'] = 0.05
    code = '''
import time
from urp_lab import worker
worker.run_algorithm = lambda *args: time.sleep(10)
raise SystemExit(worker.main())
'''
    if timer_fallback:
        code = "import signal; vars(signal).pop('SIGALRM', None)\n" + code
    process = subprocess.run([sys.executable, '-c', code], input=json.dumps(request),
                             capture_output=True, text=True, timeout=5)
    assert process.returncode == 1
    assert json.loads(process.stdout)['error_kind'] == 'timeout'


@pytest.mark.parametrize('timer_fallback', [False, True])
def test_main_with_slow_estimate_returns_one_success_json(request_and_result, timer_fallback):
    request, result = request_and_result
    request['params']['time_limit_s'] = 0.05
    code = f'''
import json
from urp_lab import worker
worker.run_algorithm = lambda *args: json.loads({json.dumps(result)!r})
worker._ESTIMATE_CODE = 'import time; time.sleep(30)'
raise SystemExit(worker.main())
'''
    if timer_fallback:
        code = "import signal; vars(signal).pop('SIGALRM', None)\n" + code
    started = time.monotonic()
    process = subprocess.run([sys.executable, '-c', code], input=json.dumps(request),
                             capture_output=True, text=True, timeout=2.05)
    assert time.monotonic() - started < 2.05
    assert process.returncode == 0
    assert_unavailable_preserves_solution(json.loads(process.stdout), result)


def test_remaining_budget_shortens_estimate_timeout(monkeypatch, request_and_result):
    monkeypatch.setattr(worker, '_ESTIMATE_CODE', 'import time; time.sleep(30)')
    # A long per-estimate cap must not override the job's shorter remainder.
    monkeypatch.setattr(worker, 'ESTIMATE_BUDGET_S', 10)
    started = time.monotonic()
    worker.add_execution_estimate(*request_and_result, deadline=started + 0.05)
    assert time.monotonic() - started < 1


def test_consumed_solver_budget_never_borrows_gateway_grace(monkeypatch, request_and_result, capfd):
    request, result = request_and_result
    before = copy.deepcopy(result)
    # Solver's one-second budget is spent; jobs would still have two seconds.
    now = time.monotonic()
    monkeypatch.setattr(worker, '_STARTED', now - 1)
    monkeypatch.setattr(worker, 'limit', lambda seconds: lambda: None)
    monkeypatch.setattr(worker, 'run_algorithm', lambda *args: result)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request)))

    def forbidden(*args, **kwargs):
        pytest.fail('Gateway grace must never fund optional estimation')

    monkeypatch.setattr(worker.subprocess, 'Popen', forbidden)
    assert worker.main() == 0
    assert_unavailable_preserves_solution(json.loads(capfd.readouterr().out), before,
                                          'estimate budget exhausted')
