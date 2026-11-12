"""Real TB4 cancellation, required-bridge failure and restart on an isolated gateway."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import time

from full_loop import Client

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--gateway', required=True)
p.add_argument('--runtime', required=True, type=Path)
p.add_argument('--output', required=True, type=Path)
p.add_argument('--scenario', default='maze-inspection')
args = p.parse_args()
if not hasattr(os, 'pidfd_open'):
    raise SystemExit('Run this Linux process-identity test with /usr/bin/python3 (pidfd_open required)')
args.output.mkdir(parents=True, exist_ok=False)
api = Client(args.gateway)
current = None


def call(method, path, body=None):
    code, value, _ = api.call(method, '/lab/api' + path, body, timeout=180)
    assert 200 <= code < 300, (code, value)
    return value


def save(name, value):
    (args.output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False))


def wait(read, predicate, seconds=120):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        data = read()
        if predicate(data):
            return data
        time.sleep(.3)
    raise TimeoutError('Expected terminal state was not reached')


def bridges(sid):
    out = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            exe = os.readlink(proc / 'exe')
            if 'parameter_bridge' not in exe:
                continue
            env = (proc / 'environ').read_bytes().split(b'\0')
            if f'CERES_SIM_SESSION={sid}'.encode() not in env:
                continue
            out.append({'pid': int(proc.name), 'exe': exe,
                        'sha256': hashlib.sha256((proc / 'exe').read_bytes()).hexdigest(),
                        'exit_guard_loaded': 'liburp_bridge_exit_guard' in (proc / 'maps').read_text()})
        except OSError:
            pass
    return out


try:
    state = call('GET', '/sim/state')
    assert state['phase'] in ('stopped', 'failed'), 'Gateway must be idle before test'
    scenario = call('GET', '/scenarios/' + args.scenario)
    job = call('POST', '/solve', {'scenario_id': scenario['id'], 'algorithms': [{'id': 'urp.turn_mode_dp'}]})
    run = wait(lambda: call('GET', '/runs/' + job['runs'][0]), lambda r: r['state'] not in ('queued', 'running'))
    assert run['check']['valid']
    for cycle in ('cancel_only', 'cancel_and_fault', 'recovered'):
        session = call('POST', '/sim/session', {'world': scenario['sim']['world'],
                                              'spawn': scenario['sim']['spawn'], 'localization': 'amcl'})
        save(cycle + '-start.json', session)
        current = session['session_id']
        ready = wait(lambda: call('GET', '/sim/state'), lambda s: s['phase'] in ('ready', 'failed', 'stopped'))
        save(cycle + '-ready.json', ready)
        assert ready['phase'] == 'ready', ready.get('errors')
        assert ready['session_id'] == current, 'Restart returned a stale session identity'
        live = bridges(current)
        save(cycle + '-live-bridges.json', live)
        assert live and all('/runtime/native/bridge/' in b['exe'] and not b['exit_guard_loaded'] for b in live)
        if cycle in ('cancel_only', 'cancel_and_fault'):
            execution = call('POST', '/sim/runs', {'run_id': run['run_id'], 'mode': 'follow_path', 'budget_s': 120})
            rid = execution['sim_run_id']
            wait(lambda: call('GET', '/sim/runs/' + rid), lambda r: r['state'] == 'executing')
            time.sleep(1)
            call('POST', '/sim/runs/' + rid + '/cancel', {})
            canceled = wait(lambda: call('GET', '/sim/runs/' + rid), lambda r: r['state'] in ('canceled', 'failed', 'succeeded'))
            assert canceled['state'] == 'canceled', canceled.get('error')
            save(cycle + '-canceled-run.json', canceled)
            wait(lambda: call('GET', '/sim/state'), lambda s: s['phase'] == 'ready')
        if cycle == 'cancel_and_fault':
            leaf = bridges(current)[0]
            fd = os.pidfd_open(leaf['pid'])
            try:
                assert f'CERES_SIM_SESSION={current}'.encode() in (Path('/proc') / str(leaf['pid']) / 'environ').read_bytes().split(b'\0')
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            finally:
                os.close(fd)
            failed = wait(lambda: call('GET', '/sim/state'), lambda s: s['phase'] in ('failed', 'stopped'))
            assert failed['errors'], failed
            save('failed-state.json', failed)
        call('DELETE', '/sim/session')
        final = wait(lambda: call('GET', '/sim/state'), lambda s: s['phase'] in ('stopped', 'failed'))
        report_path = args.runtime / 'sim' / current / 'session-report.json'
        wait(lambda: report_path.exists(), bool, 60)
        report = json.loads(report_path.read_text())
        assert not report['remaining_owned_processes']
        expected = 'FAIL' if cycle == 'cancel_and_fault' else 'PASS'
        assert report['status'] == expected, report['errors']
        save(cycle + '-session.json', report)
        print(cycle, report['status'], 'remaining=0', flush=True)
        current = None
    save('summary.json', {'status': 'PASS', 'scenario': args.scenario, 'sessions': 3,
                          'cancel': 'canceled', 'required_bridge_exit': 'failed',
                          'restarted': 'ready', 'cleanup_remaining': 0})
finally:
    if current:
        call('DELETE', '/sim/session')
