#!/usr/bin/env python3
"""Real backend/ROS field acceptance; isolated store and ROS domain, never controls foreign sessions.

Run from src/lab: .venv/bin/python e2e/field_backend.py --out /absolute/evidence/path
Requires the existing ROS/Gazebo installation. Frontend files and production records are untouched.
"""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
p = argparse.ArgumentParser()
p.add_argument('--out', type=Path, required=True)
p.add_argument('--cases', default='free,rows,supply,cancel,clock_stall,bridge_exit,contact,restart,web')
p.add_argument('--physics', type=json.loads, default=None, help='Explicit physical-model overrides as JSON')
p.add_argument('--headless', action='store_true', help='Verify real web bridges without claiming native RViz window acceptance')
args = p.parse_args()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)
os.environ['CERES_LAB_RUNTIME'] = str(out / 'runtime')
os.environ['CERES_ROS_DOMAIN_ID'] = '141'

from fastapi.testclient import TestClient
from ceres_lab.gateway import Config, create_app
from ceres_lab.scenarios.validate import validate_instance


def save(name, data):
    (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))


def wait_for(read, predicate, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        state = read()
        if predicate(state):
            return state
        time.sleep(0.25)
    raise TimeoutError(str(state)[:1800])


def owned_pid(sid, needle):
    """Only this session's tagged process may receive a fault signal."""
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            env = (proc / 'environ').read_bytes().split(b'\0')
            cmd = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode()
            if f'IGN_PARTITION=field_{sid}'.encode() in env and needle in cmd:
                return int(proc.name)
        except (OSError, UnicodeError):
            pass
    raise RuntimeError(f'No owned {needle} in {sid}')


app = create_app(Config(runtime=out / 'runtime', port=4140, field_sim_port=4193,
                        background=False, archive_autostart=False, extra_hosts={'testserver'}))
records = []
try:
    with TestClient(app) as c:
        api = '/lab/api'
        sc_resp = c.post(api + '/compose', json={'text': '60×40米果园，2台车，8个采样点，每车容量4，1个池塘',
                                                'seed': 11, 'overrides': {'plots': 1}})
        assert sc_resp.status_code == 201, sc_resp.text
        sc = sc_resp.json()
        save('scenario.json', sc)
        runs = {}
        for travel in ('free', 'rows', *(['supply'] if 'supply' in args.cases.split(',') else [])):
            inst = json.loads(json.dumps(sc['instance']))
            algorithm = 'pyvrp.fleet'
            if travel == 'supply':
                inst = {'boundary': [[0, 0], [60, 0], [60, 30], [0, 30]],
                        'depot': [3, 15], 'unload_points': [[38, 15]], 'objective': 'makespan',
                        'tasks': [{'id': i + 1, 'xy': [45 + 4 * (i % 3), 8 + 12 * (i % 2)], 'demand': 8} for i in range(8)],
                        'vehicles': {'count': 2, 'capacity': 16, 'trips': 'multi'}, 'robot_radius': .5}
                algorithm = 'ortools.fleet'
            inst['vehicles'].update(travel='free' if travel == 'supply' else travel, speed_mps=1.0)
            for t in inst['tasks']:
                t['service_s'] = 2.0
            inst = validate_instance('fleet_routing', inst)
            rec = c.post(api + '/recommend', json={'scenario_id': sc['id'], 'instance': inst})
            assert rec.status_code == 200, rec.text
            save(f'recommend-{travel}.json', rec.json())
            submitted = c.post(api + '/solve', json={'scenario_id': sc['id'], 'instance': inst,
                               'algorithms': [{'id': algorithm, 'params': {'time_limit_s': 1, 'seed': 42}}]})
            assert submitted.status_code == 202, submitted.text
            rid = submitted.json()['runs'][0]
            r = wait_for(lambda: c.get(api + '/runs/' + rid).json(), lambda r: r['state'] not in ('queued', 'running'), 20)
            assert r['state'] == 'done' and r['status'] == 'feasible' and r['check']['valid'], r
            if travel == 'supply':
                assert r['raw_result']['model_contract']['schema'] == 'urp.fleet-model/2'
                assert any([38., 15.] in route['reload_stops'] for route in r['routes']), r
            exported = c.get(api + f'/runs/{rid}/export').json()
            assert exported['routes'] == r['routes'] and exported['instance'] == inst
            save(f'planning-{travel}.json', exported)
            runs[travel] = rid
        for case in args.cases.split(','):
            begin = time.monotonic()
            started = c.post(api + '/fieldsim', json={'run_id': runs[case if case in ('rows', 'supply') else 'free'],
                                                     'real_time_factor': 8, 'web': case == 'web', 'physics': args.physics})
            assert started.status_code == 202, started.text
            sid = started.json()['session_id']
            runtime = out / 'runtime' / 'fieldsim' / sid
            state = lambda: c.get(api + '/fieldsim').json()
            ready = wait_for(state, lambda s: s['phase'] in ('running', 'finished', 'failed') or not s['running'], 100)
            assert ready['phase'] in ('running', 'finished'), ready
            if case == 'web':
                from websockets.sync.client import connect
                with connect(ready['web']['ros']['url'], subprotocols=['foxglove.sdk.v1']) as ws:
                    channel = None
                    end = time.monotonic() + 10
                    while channel is None and time.monotonic() < end:
                        raw = ws.recv(timeout=5)
                        # Foxglove may send binary Time frames before advertising
                        # the requested topic; they are not JSON control messages.
                        if isinstance(raw, bytes):
                            assert raw and raw[0] == 2 and len(raw) == 9, raw[:20]
                            continue
                        msg = json.loads(raw)
                        if msg.get('op') == 'advertise':
                            channel = next((ch for ch in msg['channels'] if ch['topic'] == '/workbench/fleet_status'), None)
                    assert channel is not None
                    ws.send(json.dumps({'op': 'subscribe', 'subscriptions': [{'id': 1, 'channelId': channel['id']}]}))
                    while time.monotonic() < end:
                        msg = ws.recv(timeout=5)
                        if isinstance(msg, bytes) and msg[0] == 1:
                            assert b'vehicles' in msg
                            save('foxglove.json', {'channel': channel['topic'], 'schema': channel['schemaName'], 'message_bytes': len(msg)})
                            break
                    else:
                        raise RuntimeError('No Foxglove telemetry message')
                with connect(ready['web']['gazebo']['url']) as ws:
                    ws.send('auth,,,' + ready['web']['gazebo']['key'])
                    assert ws.recv(timeout=5) in ('authorized', b'authorized')
                    ws.send('protos,,,')
                    proto = ws.recv(timeout=5)
                    ws.send('worlds,,,')
                    worlds = ws.recv(timeout=5)
                    assert ready['world'].encode() in (worlds.encode() if isinstance(worlds, str) else worlds)
                    save('gazebo-web.json', {'world': ready['world'], 'protobuf_schema_bytes': len(proto), 'world_response_bytes': len(worlds)})
                save('native-window-scope.json', {'tested': not args.headless,
                     'reason': 'explicit headless acceptance' if args.headless else 'two RViz open/close cycles'})
                if not args.headless:
                    for _ in range(2):
                        assert c.post(api + '/fieldsim/window', json={'kind': 'rviz', 'on': True}).status_code == 200
                        wait_for(state, lambda s: s['components'].get('rviz') == 'running', 10)
                        time.sleep(1)
                        assert c.post(api + '/fieldsim/window', json={'kind': 'rviz', 'on': False}).status_code == 200
                        wait_for(state, lambda s: s['components'].get('rviz') == 'off', 15)
                    assert (runtime / 'logs/rviz.log').exists() and (runtime / 'logs/rviz-1.log').exists()
            if case == 'contact':
                # Fault only our own world: overlap the waiting robot with the active one.
                # This tests the actual physics -> bridge -> contact fail-stop chain.
                # The waiting robot remains at a fixed parking position while the CLI connects.
                pose = ready['vehicles'][1]['pose']
                request = f'name: "vehicle_1", position: {{x: {pose[0] + .1}, y: {pose[1]}, z: 0.02}}'
                cmd = ['ign', 'service', '-s', f"/world/{ready['world']}/set_pose", '--reqtype', 'ignition.msgs.Pose',
                       '--reptype', 'ignition.msgs.Boolean', '--timeout', '3000', '--req', request]
                result = subprocess.run(cmd, env={**os.environ, 'IGN_PARTITION': 'field_' + sid},
                                        capture_output=True, text=True, timeout=10)
                save('contact-fault.json', {'command': cmd, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
                assert result.returncode == 0 and 'data: true' in result.stdout
                ready = wait_for(state, lambda s: not s['running'] or s['phase'] == 'failed', 20)
                assert ready['phase'] == 'failed', ready
            elif case == 'cancel':
                time.sleep(1)
            elif case in ('clock_stall', 'bridge_exit'):
                needle = str(runtime / 'world.sdf') if case == 'clock_stall' else '/ros_gz_bridge/parameter_bridge'
                pid = owned_pid(sid, needle)
                sig = signal.SIGSTOP if case == 'clock_stall' else signal.SIGTERM
                os.kill(pid, sig)
                if case == 'clock_stall':
                    try:
                        time.sleep(4)
                    finally:
                        try:
                            os.kill(pid, signal.SIGCONT)
                        except ProcessLookupError:
                            pass
                ready = wait_for(state, lambda s: not s['running'] or s['phase'] == 'failed', 20)
                assert ready['phase'] == 'failed', ready
            else:
                ready = wait_for(state, lambda s: s['phase'] in ('finished', 'failed') or not s['running'], 180)
            stopped = c.post(api + '/fieldsim/stop', json={})
            assert stopped.status_code == 202, stopped.text
            ended = state()
            result = c.get(api + '/fieldsim/results/' + sid)
            assert result.status_code == 200, result.text
            result = result.json()
            save(f'experiment-{case}.json', result)
            assert result['planning_solution']['check']['valid'] and result['execution_provenance']['source_sha256']
            report = json.loads((runtime / 'session-report.json').read_text())
            record = {'case': case, 'session_id': sid, 'wall_s': round(time.monotonic() - begin, 2),
                      'state': result['state'], 'returncode': ended.get('returncode'),
                      'cleanup': report['cleanup_status'], 'session_status': report['status'],
                      'remaining': report['remaining_owned_processes'], 'errors': result['errors'],
                      'check': result.get('check'), 'metrics': result['metrics'], 'telemetry': result['telemetry']}
            records.append(record)
            save('summary.json', records)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            assert not report['remaining_owned_processes'] and not ended['running'], record
            if case == 'cancel':
                assert result['state'] == 'canceled' and ended['returncode'] == 0, record
            elif case in ('clock_stall', 'bridge_exit', 'contact'):
                assert result['state'] == 'failed' and ended['returncode'] != 0, record
                if case == 'contact':
                    assert any(len(x['vehicles']) == 2 and x['positions_m'] for x in result['contacts']), result['contacts']
            else:
                assert result['state'] == 'succeeded' and result['check']['valid'] and ended['returncode'] == 0, record
                assert result['metrics']['tasks_completed'] == 8 and result['metrics']['vehicles_done'] == 2, record
                measured = result['metrics']['total_distance_m']
                traced = sum(math.dist(a[1:3], b[1:3]) for trail in result['trails'].values()
                             for a, b in zip(trail, trail[1:]))
                assert measured > .5 * result['metrics']['planned_total_distance_m'], record
                assert abs(measured - traced) < max(.2, .01 * traced), (measured, traced)
                assert all(v['speed'] < .02 for v in result['vehicles']), result['vehicles']
                assert sum(result['truth_timing'][k] for k in ('tf_header_samples', 'latest_clock_samples')) > 0
                assert result['truth_timing']['tf_header_samples'] > 0 and result['truth_timing']['latest_clock_samples'] == 0
                assert result['physics_audit']['valid'] and result['physics']['sha256']
except BaseException as exc:
    save('failure.json', {'type': type(exc).__name__, 'error': str(exc)})
    raise
