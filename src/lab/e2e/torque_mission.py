#!/usr/bin/env python3
"""Real finite-torque missions, data integrity and two-vehicle isolation.

Fresh output directory; sequential sessions; thresholds written before execution.
This verifies a synthetic rigid force-actuated model, not real motor calibration.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
INSTANCE = {'boundary': [[0,0],[34,0],[34,26],[0,26]], 'depot': [3,3], 'robot_radius': .5,
    'tasks': [{'id': 1, 'xy': [30,22], 'demand': 1, 'service_s': 1.},
              {'id': 2, 'xy': [28,24], 'demand': 1, 'service_s': 1.}],
    'vehicles': {'count': 1, 'capacity': 2, 'travel': 'free', 'speed_mps': 1.}, 'objective': 'total_distance'}
CASES = {'rich': {'wheel_torque_limit_nm': 8.}, 'starved': {'wheel_torque_limit_nm': .5},
         'loaded': {'wheel_torque_limit_nm': .5, 'payload_kg': 20.},
         'isolation': {'wheel_torque_limit_nm': 8.}, 'combined': {'wheel_torque_limit_nm': 8.}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cases', default=','.join(CASES))
    p.add_argument('--rtf', type=float, default=4.)
    args = p.parse_args()
    if not args.out.is_absolute(): p.error('--out must be absolute')
    selected = args.cases.split(',')
    if not selected or any(c not in CASES for c in selected): p.error('unknown --cases')
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    os.environ['CERES_LAB_RUNTIME'], os.environ['CERES_ROS_DOMAIN_ID'] = str(out/'runtime'), '145'
    from fastapi.testclient import TestClient
    from ceres_lab.gateway import Config, create_app

    def save(name, value):
        (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    def wait(read, done, seconds):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            value = read()
            if done(value): return value
            time.sleep(.3)
        raise TimeoutError('torque mission wall deadline')
    save('protocol.json', {'schema': 'urp.torque-mission-check/2', 'instance': INSTANCE, 'cases': selected,
        'rtf': args.rtf, 'physics': CASES, 'positive': ['rich','isolation','combined'],
        'stress': ['starved','loaded'], 'acceptance': 'unchanged fieldcheck and truth task dwell; complete joint feedback, data validity, real exit code, clean shutdown; stress may fail with valid data and explicit reason',
        'calibration': 'synthetic Coulomb ground and force PID; no real tyre, soil or motor calibration'})
    app = create_app(Config(runtime=out/'runtime', port=4143, field_sim_port=4196,
                            background=False, archive_autostart=False, extra_hosts={'testserver'}))
    rows = []
    try:
        with TestClient(app) as c:
            api = '/lab/api'
            sc = c.post(api+'/compose', json={'text':'34×26米田块，1台车，2个采样点，容量2','seed':72}).json()
            for case in selected:
                inst, params, execution = copy.deepcopy(INSTANCE), {'time_limit_s':1,'seed':42}, None
                if case == 'isolation':
                    inst['vehicles'].update(count=2, capacity=1, trips='single')
                if case == 'combined':
                    inst['tasks'][0]['xy'] = [24,14]
                    inst['vehicles']['speed_mps'] = .7
                    inst['ground'] = {'zones': [{'rect':[18,10,30,20], 'mu':.22}]}
                    params.update(ground_policy='risk_adjust', ground_risk_weight=1.)
                    execution = {'localization_preset':'survey','seed':72}
                sub = c.post(api+'/solve', json={'scenario_id':sc['id'], 'instance':inst,
                    'algorithms':[{'id':'pyvrp.fleet', 'params':params}]})
                sub.raise_for_status()
                run = wait(lambda:c.get(api+'/runs/'+sub.json()['runs'][0]).json(),
                           lambda r:r['state'] not in ('queued','running'), 40)
                assert run['state']=='done' and run['check']['valid'], run
                save(case+'-planning.json', run)
                started = c.post(api+'/fieldsim', json={'run_id':run['run_id'], 'real_time_factor':args.rtf,
                    'web':False, 'execution':execution, 'physics':{'actuator_mode':'torque_limited', **CASES[case]}})
                started.raise_for_status()
                sid = started.json()['session_id']
                state = lambda:c.get(api+'/fieldsim').json()
                try:
                    wait(state, lambda s:s['phase'] in ('finished','failed') or not s['running'], 700)
                finally:
                    c.post(api+'/fieldsim/stop',json={}).raise_for_status()
                    closed = wait(state, lambda s:not s['running'], 120)
                result = c.get(api+f'/fieldsim/results/{sid}').json()
                report = json.loads((out/'runtime/fieldsim'/sid/'session-report.json').read_text())
                check = result.get('check') or {}
                feedback = result.get('wheel_feedback') or {}
                checks = {'data_valid': check.get('data_valid') is True,
                    'cleanup': report.get('cleanup_status')=='PASS' and not report.get('remaining_owned_processes'),
                    'exit_code':closed.get('returncode')==(0 if result['state']=='succeeded' else 1),
                    'joint_feedback':len(feedback)==inst['vehicles']['count'] and all(v['accepted']>50 and not v['rejected'].get('source_gap') for v in feedback.values())}
                if case in ('rich','isolation','combined'):
                    checks['mission_succeeded'] = result['state']=='succeeded' and check.get('valid') is True
                else:
                    checks['honest_stress_outcome'] = (bool(result.get('errors')) and not check.get('valid')
                        if result['state']=='failed' else result['state']=='succeeded' and check.get('valid') is True)
                if case=='combined':
                    checks['map_and_cap'] = (check.get('ground_speed') or {}).get('valid') is True
                    checks['causal_replay'] = ((check.get('nonideal_observations') or {}).get('chain_replay') or {}).get('valid') is True
                if case=='isolation':
                    traces = result.get('torque_traces') or {}
                    second = traces.get('vehicle_2') or []
                    first_move = next((r[0] for r in second if max(abs(r[1]),abs(r[2]))>.05), None)
                    idle = [r for r in second if first_move is not None and r[0]<first_move-.2]
                    checks['other_vehicle_stays_idle'] = len(idle)>50 and max(max(abs(r[3]),abs(r[4])) for r in idle)<.05
                row = {'case':case, 'session_id':sid, 'state':result['state'], 'returncode':closed.get('returncode'),
                       'cleanup':report.get('cleanup_status'), 'remaining':report.get('remaining_owned_processes'),
                       'checks':checks, 'metrics':result['metrics'], 'verdict':check,
                       'errors':result.get('errors'), 'wheel_feedback':feedback}
                rows.append(row)
                save(case+'-summary.json', row)
                save('summary.json', {'schema':'urp.torque-mission-check/2', 'status':'PASS' if all(all(r['checks'].values()) for r in rows) else 'FAIL', 'cases':rows})
                print(json.dumps({'case':case,'state':result['state'],'failed_assertions':[k for k,v in checks.items() if not v]}, ensure_ascii=False), flush=True)
    except Exception as exc:
        save('failure.json', {'type':type(exc).__name__,'message':str(exc),'completed':len(rows)})
        raise
    return 0 if all(all(r['checks'].values()) for r in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
