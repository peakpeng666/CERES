"""One request on stdin, one result on stdout; native solver output stays in the raw log."""
import contextlib
import json
import os
import signal
import sys
import threading
import traceback
from .solvers import run_algorithm


def expired(*_):
    raise TimeoutError('求解计算超过 time_limit_s；工作进程已中止')


def limit(seconds: float):
    """Bound in-process Python solvers. Windows has no interval timer, so a watchdog ends the process with the same report."""
    if hasattr(signal, 'SIGALRM'):
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        return lambda: signal.setitimer(signal.ITIMER_REAL, 0)

    def stop():
        print(json.dumps({'error': 'TimeoutError: 求解计算超过 time_limit_s；工作进程已中止', 'error_kind': 'timeout'}, ensure_ascii=False), flush=True)
        os._exit(1)
    timer = threading.Timer(seconds, stop)
    timer.daemon = True
    timer.start()
    return timer.cancel


def main():
    disarm = lambda: None  # noqa: E731
    try:
        request = json.load(sys.stdin)
        if request['algorithm_id'].startswith('urp.'):
            disarm = limit(float(request['params'].get('time_limit_s', 3)))
        with contextlib.redirect_stdout(sys.stderr):
            result = run_algorithm(request['scenario'], request['instance'], request['algorithm_id'], request['params'])
        disarm()
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        disarm()
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'error': f'{type(exc).__name__}: {exc}', 'error_kind': 'timeout' if isinstance(exc, TimeoutError) else 'solver_error'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
