#!/usr/bin/env python3
"""Real Gazebo runs through the agent, ending in the two-engine comparison.

Each case goes the way a person would: describe the task, add the simulation requirement to the contract, confirm,
approve, and wait for the report. The gateway must run on an isolated runtime. Only one physical acceptance at a time.

Usage: gazebo_sim2sim.py --out /new/absolute/path [--base http://127.0.0.1:4100] [--cases a,b]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CASES = {
    'one-free': '在 60×40 米的菜地里，1 台车访问 6 个点，单车容量 8',
    'two-free': '在 70×50 米的菜地里，2 台车访问 8 个点，单车容量 6',
    'two-rows': '在 60×40 米的果园里，2 台车访问 6 个采样点，单车容量 4',
    'three-obstacle': '在 90×60 米的果园里有一个池塘，3 台车访问 9 个点，单车容量 4',
}

parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--base', default='http://127.0.0.1:4100')
parser.add_argument('--cases', default=','.join(CASES))
parser.add_argument('--timeout', type=float, default=1500)
parser.add_argument('--mode', default='rule', choices=('rule', 'hybrid'))
args = parser.parse_args()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)
API = f'{args.base}/lab/api'


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f'{API}{path}', data=data, method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{method} {path}: {exc.code} {exc.read()[:400]!r}') from None


def until(sid, states, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        s = call('GET', f'/agent/sessions/{sid}')
        if s['state'] in states:
            return s
        time.sleep(1)
    raise TimeoutError(f"{sid} still {s['state']}")


def case(name, text):
    started = time.time()
    s = call('POST', '/agent/sessions', {'mode': args.mode, 'text': text})
    sid = s['session_id']
    s = until(sid, ('awaiting_confirmation', 'awaiting_user', 'failed'), 180)
    if s['state'] != 'awaiting_confirmation':
        raise RuntimeError(f"{name}: {s['state']} {s.get('error')}")
    call('PATCH', f'/agent/sessions/{sid}/contract', {'fields': {'simulate': True}})
    s = until(sid, ('awaiting_confirmation',), 30)
    call('POST', f'/agent/sessions/{sid}/control', {'action': 'confirm'})
    s = until(sid, ('awaiting_approval', 'done', 'failed'), 420)
    if s['state'] != 'awaiting_approval':
        raise RuntimeError(f"{name}: no approval request; {s['state']} {s.get('error')}")
    call('POST', f'/agent/sessions/{sid}/control', {'action': 'approve'})
    s = until(sid, ('done', 'failed', 'cancelled'), args.timeout)
    (out / f'{name}.session.json').write_text(json.dumps(s, ensure_ascii=False, indent=1))
    evidence = (s.get('report') or {}).get('evidence') or {}
    sim = evidence.get('simulation_result') or {}
    gap = sim.get('comparison') or {}
    row = {'case': name, 'text': text, 'session_id': sid, 'state': s['state'], 'seconds': round(time.time() - started, 1), 'field_session': sim.get('session_id'), 'run_id': sim.get('run_id'),
           'gazebo_state': sim.get('state'), 'gazebo_check_valid': sim.get('check_valid'), 'gazebo_errors': sim.get('errors'), 'gazebo': sim.get('metrics'),
           'physics': [{k: r.get(k) for k in ('run_id', 'algorithm', 'state', 'check_valid', 'wall_s', 'metrics', 'errors')} for r in evidence.get('physics') or []],
           'gap': {k: gap.get(k) for k in ('path_mean_m', 'path_max_m', 'time_rmse_m', 'metrics', 'physics_run_id')} if gap else None,
           'gap_vehicles': gap.get('vehicles'), 'report': (s.get('report') or {}).get('narrative'), 'error': s.get('error')}
    print(json.dumps({k: row[k] for k in ('case', 'state', 'seconds', 'gazebo_state', 'gazebo_check_valid')} | {'path_max_m': (row['gap'] or {}).get('path_max_m'), 'time_rmse_m': (row['gap'] or {}).get('time_rmse_m')}, ensure_ascii=False), flush=True)
    return row


rows, failed = [], 0
for name in args.cases.split(','):
    try:
        rows.append(case(name, CASES[name]))
    except Exception as exc:  # noqa: BLE001 - the summary records every case, including the ones that stopped
        failed += 1
        rows.append({'case': name, 'state': 'script_error', 'error': str(exc)})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    (out / 'summary.json').write_text(json.dumps({'base': args.base, 'cases': rows}, ensure_ascii=False, indent=1))
sys.exit(1 if failed or any(r.get('gazebo_state') != 'succeeded' for r in rows) else 0)
