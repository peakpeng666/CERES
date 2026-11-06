#!/usr/bin/env python3
"""Run the supported engineering gates without overwriting research evidence."""
import argparse
from contextlib import contextmanager
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
UNITTEST_SUMMARY = re.compile(r'^Ran (\d+) tests? in [^\r\n]+$', re.MULTILINE)
REPORT_ENVIRONMENT = (
    'URP_CVRPLIB_TEST_LOG_DIR', 'ROUTE_SCENE_INTEGRATION_REPORT',
    'ROUTE_INTEGRATION_REPORT', 'MATURE_INTEGRATION_REPORT', 'PUBLIC_INTEGRATION_REPORT',
    'ROUTE_INTEGRATION_DB', 'MATURE_INTEGRATION_DB',
)


@contextmanager
def child_subreaper():
    """Serial Linux CLI scope: adopt descendants even if they create a session."""
    if sys.platform != 'linux':
        raise RuntimeError('Engineering process supervision requires Linux or WSL.')
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), 'Cannot enable child subreaper')
    try:
        yield
    finally:
        if libc.prctl(36, previous.value, 0, 0, 0):
            raise OSError(ctypes.get_errno(), 'Cannot restore child subreaper')


@contextmanager
def finish_cleanup_before_interrupting():
    pending = []
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in previous:
            signal.signal(sig, lambda number, _frame: pending.append(number))
        yield pending
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def child_processes(visible_pid='self'):
    """Map child namespace PIDs to procfs PIDs, including worker-thread children.

    A container can see an ancestor namespace's /proc mount. Visible host PIDs
    must never be passed directly to kill/waitpid. PPid scanning also works on
    kernels without the optional /proc/PID/task/TID/children interface.
    """
    namespace = next(line for line in Path('/proc/self/status').read_text().splitlines()
                     if line.startswith('NSpid:')).split()[1:]
    if int(namespace[-1]) != os.getpid():
        raise RuntimeError('Cannot identify the current PID namespace.')
    namespace_index = len(namespace) - 1
    parent = int(os.readlink('/proc/self')) if visible_pid == 'self' else int(visible_pid)
    children = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # The comm field can contain spaces and parentheses; fields after
            # its final ')' begin with state, then the visible parent PID.
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[1]) != parent:
                continue
            status = (entry / 'status').read_text()
            ids = next(line for line in status.splitlines() if line.startswith('NSpid:')).split()[1:]
            children[int(ids[namespace_index])] = int(entry.name)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return children


def cleanup_gate(proc, prior_children, grace=0.5):
    """Stop and reap only this serial gate's tree, including adopted orphans.

    Existing children are excluded. The standalone runner starts one gate at a
    time; callers must not create unrelated children concurrently in this scope.
    """
    reaped, sent, errors = {}, set(), []

    def signal_pid(pid, sig):
        if (pid, sig) in sent:
            return
        try:
            os.kill(pid, sig)
            sent.add((pid, sig))
        except ProcessLookupError:
            pass

    def remaining():
        proc.poll()  # Popen owns its leader's wait status.
        adopted = set(child_processes()) - prior_children - {proc.pid}
        for pid in adopted:
            try:
                found, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                continue
            if found:
                reaped[found] = os.waitstatus_to_exitcode(status)
        roots = {pid: visible for pid, visible in child_processes().items() if pid not in prior_children}
        pending, descendants = list(roots.items()), set()
        while pending:
            pid, visible = pending.pop()
            if pid in descendants:
                continue
            descendants.add(pid)
            pending.extend(child_processes(visible).items())
        return descendants

    alive = set()
    complete = False
    for sig, duration in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(str(exc))
        deadline = time.monotonic() + duration
        while True:
            alive = remaining()
            for pid in alive:
                try:
                    signal_pid(pid, sig)
                except OSError as exc:
                    errors.append(str(exc))
            try:
                os.killpg(proc.pid, 0)
                group_exists = True
            except ProcessLookupError:
                group_exists = False
            if not alive and not group_exists and proc.returncode is not None:
                complete = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        if complete:
            break
    return {'complete': complete and not errors, 'remaining_pids': sorted(alive),
            'reaped_descendants': [{'pid': pid, 'returncode': code} for pid, code in sorted(reaped.items())],
            'signals': [{'pid': pid, 'signal': int(sig)} for pid, sig in sorted(sent)], 'errors': errors}


def gate_environment(name, output):
    env = dict(os.environ)
    for variable in REPORT_ENVIRONMENT:
        env.pop(variable, None)
    if name == 'research':
        env['URP_CVRPLIB_TEST_LOG_DIR'] = str(output / 'research_worker_evidence')
    if name == 'backend_route_contracts':
        env['ROUTE_SCENE_INTEGRATION_REPORT'] = str(output / 'backend_route_scene.json')
    if name == 'backend_packages':
        env['RESEARCH_CONSOLE_PACKAGE_ROOT'] = str(ROOT / 'data/packages')
    if name == 'robotics_core':
        env['PYTHONPATH'] = str(ROOT / 'src/robotics/urp_route_sim')
    return env


def run_gate(name, cwd, command, output, *, timeout=240, require_tests=False, cleanup_grace=0.5):
    started = time.monotonic()
    log = output / f'{name}.log'
    proc = None
    status, returncode, error = 'unavailable', None, None
    cleanup = {'complete': True, 'remaining_pids': [], 'reaped_descendants': [], 'signals': [], 'errors': []}
    with log.open('x', encoding='utf-8') as stream:
        try:
            with child_subreaper():
                prior_children = set(child_processes())
                try:
                    proc = subprocess.Popen(command, cwd=ROOT / cwd, env=gate_environment(name, output),
                                            stdin=subprocess.DEVNULL, stdout=stream,
                                            stderr=subprocess.STDOUT, start_new_session=True)
                    returncode = proc.wait(timeout=timeout)
                    status = 'passed' if returncode == 0 else 'failed'
                except subprocess.TimeoutExpired:
                    status = 'timeout'
                except KeyboardInterrupt as exc:
                    status, error = 'interrupted', str(exc) or 'KeyboardInterrupt'
                except Exception as exc:
                    status, error = ('unavailable' if proc is None else 'error'), f'{type(exc).__name__}: {exc}'
                finally:
                    if proc is not None:
                        with finish_cleanup_before_interrupting() as interruptions:
                            cleanup = cleanup_gate(proc, prior_children, cleanup_grace)
                        returncode = proc.returncode
                        if interruptions:
                            status, error = 'interrupted', f'Signal {interruptions[0]} received during cleanup'
        except KeyboardInterrupt as exc:
            status, error = 'interrupted', str(exc) or 'KeyboardInterrupt'
            cleanup['complete'] = proc is None
        except Exception as exc:
            status, error = 'error', f'{type(exc).__name__}: {exc}'
            cleanup['complete'] = proc is None
        if error:
            stream.write(f'\nGate runner: {error}\n')
        if not cleanup['complete']:
            status = 'cleanup_failed'
    contents = log.read_bytes()
    counts = UNITTEST_SUMMARY.findall(contents.decode('utf-8', errors='replace'))
    tests_run = int(counts[-1]) if counts else None
    if status == 'passed' and (tests_run == 0 or (require_tests and tests_run is None)):
        status = 'no_tests' if tests_run == 0 else 'test_summary_missing'
    return {'name': name, 'status': status, 'returncode': returncode, 'command': command,
            'cwd': str(cwd), 'elapsed_seconds': round(time.monotonic() - started, 3),
            'log': log.name, 'log_sha256': hashlib.sha256(contents).hexdigest() if cleanup['complete'] else None,
            'tests_run': tests_run, 'cleanup': cleanup, 'error': error}


def gate_commands(py, output, skip_build=False):
    commands = [
        ('backend', 'src/backend', [py, '-m', 'unittest', 'discover', '-s', 'tests', '-v']),
        ('backend_packages', 'src/backend', [py, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'integration_backend.py', '-v']),
        ('backend_route_contracts', 'src/backend', [py, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'integration_route_scene.py', '-v']),
        ('research', 'src/research', [py, 'scripts/test.py']),
        ('generator', 'src/research/generator', [py, '-m', 'unittest', '-v', 'test_runner']),
        ('supervision', 'src/research/execution', [py, '-m', 'unittest', 'discover', '-s', 'tests', '-v']),
        ('baseline_aggregation', '.', [py, '-m', 'unittest', 'discover', '-s', 'protocol', '-p', 'test_*.py', '-v']),
        ('independent_validator', '.', [py, '-m', 'unittest', 'discover', '-s', 'independent', '-p', 'test_*.py', '-v']),
        ('public_cli', '.', [py, '-m', 'unittest', 'discover', '-s', 'tools/tests', '-v']),
        ('robotics_core', 'src/robotics', [py, '-m', 'unittest', 'discover', '-s', 'urp_route_sim/test', '-v']),
        ('frontend_data', 'src/frontend', [py, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v']),
        ('frontend_ui', 'src/frontend', ['npm', 'run', 'test:ui']),
        ('desktop_shell', 'src/desktop', ['npm', 'test']),
        ('frontend_replay', 'src/frontend', ['node', '--test', 'core/routing/tests/model-result.test.js',
                                          'tests/replay-client.test.mjs', 'tests/replay-real.test.mjs', 'tests/replay.test.mjs']),
        ('bpp_scientific_readback', 'src/frontend', [py, 'scripts/validate_data.py', '--output', str(output / 'bpp_validation.json')]),
    ]
    if not skip_build:
        commands.append(('frontend_build', 'src/frontend', ['npm', 'run', 'build']))
    return commands


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New report directory; existing directories are rejected')
    parser.add_argument('--skip-build', action='store_true', help='Run tests only; report explicitly omits the production build')
    args = parser.parse_args()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = (args.output or ROOT / '.local-state' / 'checks' / stamp).resolve()
    output.mkdir(parents=True, exist_ok=False)
    commands = gate_commands(sys.executable, output, args.skip_build)
    results = []
    omitted = ['frontend_build'] if args.skip_build else []

    def stop(signum, _frame):
        raise KeyboardInterrupt(f'Signal {signum}')

    previous_term = signal.signal(signal.SIGTERM, stop)
    try:
        for index, (name, cwd, command) in enumerate(commands):
            print(f'{name}: running', flush=True)
            result = run_gate(name, cwd, command, output,
                              require_tests=name not in {'frontend_ui', 'frontend_replay', 'desktop_shell',
                                                         'bpp_scientific_readback', 'frontend_build'})
            results.append(result)
            print(f'{name}: {result["status"]}', flush=True)
            if result['status'] in {'interrupted', 'cleanup_failed'}:
                omitted.extend(name for name, _, _ in commands[index + 1:])
                break
    finally:
        signal.signal(signal.SIGTERM, previous_term)
    report = {'schema': 'urp-engineering-checks-v1', 'generated_utc': stamp,
              'python': sys.version, 'status': 'passed' if all(r['status'] == 'passed' for r in results) else 'failed',
              'gates': results, 'omitted': omitted,
              'scope': 'Engineering regressions and frozen BPP readback; no formal baseline re-execution, live LLM, ROS or Gazebo physics.'}
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(output / 'summary.json')}), flush=True)
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
