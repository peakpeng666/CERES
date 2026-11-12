"""One request on stdin, one result on stdout; native solver output stays in the raw log."""
import contextlib
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
import traceback

# Start before solver imports: optional work must pay for imports and input too.
_STARTED = time.monotonic()
from .solvers import run_algorithm

ESTIMATE_BUDGET_S = 0.5
# Optional work may use only the original solver budget, never jobs grace.
OUTPUT_RESERVE_S = 0.25
_ESTIMATE_CODE = """
import contextlib, json, sys
with contextlib.redirect_stdout(sys.stderr):
    from ceres_lab.field_coordination import estimate
    request = json.load(sys.stdin)
    result = estimate(request['instance'], request['routes'])
print(json.dumps(result, allow_nan=False))
"""


def expired(*_):
    raise TimeoutError('求解计算超过 time_limit_s；工作进程已中止')


def limit(seconds: float):
    """Bound in-process Python solvers. Windows has no interval timer, so a watchdog ends the process with the same report."""
    if hasattr(signal, 'SIGALRM'):
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        return lambda: signal.setitimer(signal.ITIMER_REAL, 0)
    # main() sends sys.stdout to stderr while the solver runs; the gateway reads the terminal state from the real stdout only.
    out = sys.stdout

    def stop():
        print(json.dumps({'error': 'TimeoutError: 求解计算超过 time_limit_s；工作进程已中止', 'error_kind': 'timeout'}, ensure_ascii=False),
              file=out, flush=True)
        os._exit(1)
    timer = threading.Timer(seconds, stop)
    timer.daemon = True
    timer.start()
    return timer.cancel


def add_execution_estimate(request: dict, result: dict, *, deadline: float | None = None):
    """Best-effort annotation, isolated from both the solver and its watchdog.

    The child timeout includes its imports and JSON I/O. It is killed and reaped
    on timeout; no POSIX alarm or fork support is required. The gateway's wall
    budget stays unchanged and a missing/failed estimate never invalidates a plan.
    """
    if not ((request.get('scenario') or {}).get('problem_class') == 'fleet_routing'
            and result.get('routes') and result.get('status') in ('feasible', 'optimal')):
        return
    unavailable = {'status': 'unavailable', 'reason': 'estimate failed'}
    result.setdefault('metrics', {})['execution_estimate'] = unavailable
    proc = None
    try:
        deadline = min(deadline if deadline is not None else float('inf'),
                       time.monotonic() + ESTIMATE_BUDGET_S)
        if deadline <= time.monotonic():
            unavailable['reason'] = 'estimate budget exhausted'
            return
        payload = json.dumps({'instance': request['instance'], 'routes': result['routes']}, allow_nan=False)
        if deadline <= time.monotonic():
            unavailable['reason'] = 'estimate budget exhausted'
            return
        proc = subprocess.Popen([sys.executable, '-c', _ESTIMATE_CODE], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=sys.stderr, text=True)
        output, _ = proc.communicate(payload, timeout=max(0, deadline - time.monotonic()))
        if proc.returncode:
            unavailable['reason'] = 'estimate process failed'
            return
        found = json.loads(output)
        if not isinstance(found, dict) or not found:
            unavailable['reason'] = 'invalid estimate result'
            return
        # Reject non-finite child data before touching the successful result.
        json.dumps(found, allow_nan=False)
        metrics = result.setdefault('metrics', {})
        metrics['execution_estimate'] = found
        predicted, meetings = found.get('predicted_makespan_s'), found.get('meetings')
        if (type(predicted) in (int, float) and math.isfinite(predicted) and predicted >= 0
                and type(meetings) is int and meetings >= 0):
            metrics['predicted_makespan_s'] = predicted
            metrics['route_meetings'] = meetings
    except subprocess.TimeoutExpired:
        unavailable['reason'] = 'estimate timed out'
    except (ValueError, TypeError):
        unavailable['reason'] = 'invalid estimate data'
        traceback.print_exc(file=sys.stderr)
    except Exception:
        # Keep details in the raw log, never in the public annotation.
        traceback.print_exc(file=sys.stderr)
    finally:
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
                # Bounded reap: even child teardown cannot consume the solver result.
                proc.wait(timeout=0.1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass


def main():
    disarm = lambda: None  # noqa: E731
    try:
        request = json.load(sys.stdin)
        if request['algorithm_id'].startswith('urp.'):
            disarm = limit(float(request['params'].get('time_limit_s', 3)))
        with contextlib.redirect_stdout(sys.stderr):
            result = run_algorithm(request['scenario'], request['instance'], request['algorithm_id'], request['params'])
        disarm()
        disarm = lambda: None  # noqa: E731
        deadline = _STARTED + float(request['params'].get('time_limit_s', 60)) - OUTPUT_RESERVE_S
        add_execution_estimate(request, result, deadline=deadline)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        disarm()
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'error': f'{type(exc).__name__}: {exc}', 'error_kind': 'timeout' if isinstance(exc, TimeoutError) else 'solver_error'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
