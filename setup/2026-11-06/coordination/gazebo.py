#!/usr/bin/env python3
"""Real Gazebo runs of the plans the agent picks, with every vehicle moving at once, then the same plan.json replayed in MuJoCo.

Needs a backend whose Gazebo session accepts ``traffic: coordinated`` (see 后端对接需求.md). Only one physical run at a time.

Usage: gazebo.py --out /new/absolute/dir [--base http://127.0.0.1:4100] [--cases a,b] [--timeout 1500]
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

CASES = {
    'two-free': '在 70×50 米的菜地里，2 台车访问 8 个点，单车容量 6',
    'three-pond': '在 90×60 米的果园里有一个池塘，3 台车访问 9 个点，单车容量 4',
    'five-field': '在 150×100 米的菜地里，5 台车访问 30 个点，单车容量 8',
}
parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--base', default='http://127.0.0.1:4100')
parser.add_argument('--cases', default=','.join(CASES))
parser.add_argument('--timeout', type=float, default=1500)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=False)
API = f'{args.base}/lab/api'


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f'{API}{path}', data=data, method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{method} {path}: {exc.code} {exc.read()[:400]!r}') from None


def until(fetch, done, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = fetch()
        if done(value):
            return value
        time.sleep(1)
    raise TimeoutError(str(value)[:200])


def case(name, text):
    s = call('POST', '/agent/sessions', {'mode': 'rule', 'text': text})
    sid = s['session_id']
    s = until(lambda: call('GET', f'/agent/sessions/{sid}'), lambda x: x['state'] in ('awaiting_confirmation', 'failed'), 120)
    call('POST', f'/agent/sessions/{sid}/control', {'action': 'confirm'})
    s = until(lambda: call('GET', f'/agent/sessions/{sid}'), lambda x: x['state'] in ('done', 'failed'), 300)
    evidence = s['report']['evidence']
    rows = {r['run_id']: r for r in evidence['physics']}
    best = next(r for r in sorted(rows.values(), key=lambda r: (r.get('coordinated') or {}).get('makespan_s') or 1e9) if (r.get('coordinated') or {}).get('check_valid'))
    field = call('POST', '/fieldsim', {'run_id': best['run_id'], 'real_time_factor': 4.0, 'traffic': 'coordinated'})
    session = field.get('session_id')
    started = time.monotonic()
    state = until(lambda: call('GET', '/fieldsim'), lambda x: x.get('phase') in ('finished', 'failed', 'idle', 'stopped', 'done', 'succeeded') and x.get('session_id', session) == session, args.timeout)
    result = call('GET', f'/fieldsim/results/{session}')
    call('POST', '/fieldsim/stop', {})
    until(lambda: call('GET', '/fieldsim'), lambda x: x.get('phase') == 'idle' or not x.get('running'), 60)
    (args.out / f'{name}.gazebo.json').write_text(json.dumps({k: result.get(k) for k in ('session_id', 'run_id', 'state', 'errors', 'metrics', 'check', 'vehicles', 'traffic')}, ensure_ascii=False, indent=1))
    replay = call('POST', '/physics/runs', {'session_id': session})
    replay = until(lambda: call('GET', f"/physics/runs/{replay['physics_run_id']}"), lambda x: x.get('state') not in ('queued', 'running'), 300)
    gap = replay.get('comparison') or {}
    row = {'case': name, 'run_id': best['run_id'], 'algorithm': best['algorithm'], 'session_id': session, 'gazebo_state': result.get('state'), 'gazebo_check_valid': (result.get('check') or {}).get('valid'),
           'gazebo_errors': result.get('errors'), 'wall_s': round(time.monotonic() - started, 1), 'planned_s': best['metrics'].get('planned_makespan_s'), 'predicted_s': best['coordinated'].get('predicted_makespan_s'),
           'mujoco_serial_s': best['metrics'].get('makespan_s'), 'mujoco_coordinated_s': best['coordinated'].get('makespan_s'), 'gazebo_s': (result.get('metrics') or {}).get('makespan_s'),
           'mujoco_replay_s': (replay.get('metrics') or {}).get('makespan_s'), 'replay_state': replay.get('state'), 'replay_valid': (replay.get('check') or {}).get('valid'),
           'path_mean_m': gap.get('path_mean_m'), 'path_max_m': gap.get('path_max_m'), 'time_rmse_m': gap.get('time_rmse_m'),
           'gazebo_min_gap_m': min((p['min_footprint_gap_m'] for p in (((result.get('check') or {}).get('traffic') or {}).get('pairs') or [])), default=None),
           'holds': sum(e.get('event') == 'hold' for e in ((result.get('traffic') or {}).get('events') or []))}
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


rows = []
for name in args.cases.split(','):
    try:
        rows.append(case(name, CASES[name]))
    except Exception as exc:  # noqa: BLE001 - the summary keeps the cases that stopped
        rows.append({'case': name, 'error': str(exc)})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    (args.out / 'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1))
