#!/usr/bin/env python3
"""Bounded engineering audit of real non-ideal execution, not an algorithm ranking.

The route-independent rectangle and square visit problem are fixed before these
runs. Coplanar equal-mu tiles control for seams; low-mu tiles must be contacted
and change wheel/truth agreement. A low-bias synthetic sensor is the positive
control, while the old light case may miss the unchanged 0.20 m corner task radius.
Failures are accepted only with valid data and an explicit task/protection reason.
This does not validate a soil or tyre calibration. Never run parallel physics.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
INSTANCE = {'boundary': [[0,0],[24,0],[24,20],[0,20]], 'depot':[4,4],
    'tasks':[{'id':i+1,'xy':xy,'demand':1,'label':f'审计点{i+1}','service_s':2.0}
             for i,xy in enumerate(([18,4],[18,16],[4,16]))],
    'vehicles':{'count':1,'capacity':3,'speed_mps':.7,'travel':'free'},
    'objective':'total_distance','robot_radius':.5,'units':'m','plots':[], 'obstacles':[]}
ZONE = [14,2,21,9]
CASES = {
    'ideal': {},
    'geometry_control': {'execution':{'ground_zones':[{'rect':ZONE,'mu':1.0}]}},
    'ground_low': {'execution':{'ground_zones':[{'rect':ZONE,'mu':.12}]}},
    'localization': {'execution':{'localization_preset':'survey','seed':72}},
    'combined': {'execution':{'ground_zones':[{'rect':ZONE,'mu':.22}], 'localization_preset':'survey','seed':72}},
    'outage_hold': {'execution':{'localization_preset':'hold','seed':72}},
    'severe': {'execution':{'ground_zones':[{'rect':ZONE,'mu':.05}], 'localization_preset':'severe','seed':72}},
}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--cases',default=','.join(CASES))
    p.add_argument('--rtf',type=float,default=4)
    p.add_argument('--seed',type=int,default=72)
    args=p.parse_args()
    if not args.out.is_absolute(): p.error('--out must be absolute')
    selected=args.cases.split(',')
    if not selected or any(c not in CASES for c in selected): p.error('unknown or empty --cases')
    if not 0 <= args.seed < 2**31: p.error('--seed outside 0..2^31-1')
    cases=copy.deepcopy(CASES)
    for spec in cases.values():
        if spec.get('execution',{}).get('localization_preset'):
            spec['execution']['seed']=args.seed
    args.out.mkdir(parents=True,exist_ok=False)
    os.environ['CERES_LAB_RUNTIME']=str(args.out/'runtime')
    os.environ['CERES_ROS_DOMAIN_ID']='143'
    from fastapi.testclient import TestClient
    from ceres_lab.gateway import Config, create_app
    from ceres_lab.scenarios.validate import validate_instance

    def save(name,data):
        (args.out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    def wait(read,done,limit):
        end=time.monotonic()+limit
        while time.monotonic()<end:
            value=read()
            if done(value): return value
            time.sleep(.3)
        raise TimeoutError('bounded nonideal audit deadline')
    save('protocol.json',{'instance':INSTANCE,'cases':{k:cases[k] for k in selected},'rtf':args.rtf,
        'positive_controls':['ideal','geometry_control','ground_low','localization','combined'],
        'stress':['outage_hold','severe'],
        'acceptance':'valid recorded data, exact cleanup, actual contact and independent control replay; task truth gate unchanged'})
    app=create_app(Config(runtime=args.out/'runtime',port=4141,field_sim_port=4194,
                          background=False,archive_autostart=False,extra_hosts={'testserver'}))
    results=[]
    try:
        with TestClient(app) as client:
            api='/lab/api'
            sc=client.post(api+'/compose',json={'text':'24×20米田块，1台车，3个采样点，每车容量3','seed':72}).json()
            inst=validate_instance('fleet_routing',INSTANCE)
            solve=client.post(api+'/solve',json={'scenario_id':sc['id'],'instance':inst,
                'algorithms':[{'id':'pyvrp.fleet','params':{'time_limit_s':1,'seed':42}}]})
            solve.raise_for_status()
            rid=solve.json()['runs'][0]
            run=wait(lambda:client.get(api+'/runs/'+rid).json(),lambda r:r['state'] not in ('queued','running'),40)
            assert run['state']=='done' and run['check']['valid'],run
            save('planning.json',run)
            for case in selected:
                spec=cases[case]
                response=client.post(api+'/fieldsim',json={'run_id':rid,'real_time_factor':args.rtf,'web':False,**spec})
                response.raise_for_status()
                sid=response.json()['session_id']
                final=None
                try:
                    final=wait(lambda:client.get(api+'/fieldsim').json(),lambda r:r['phase'] in ('finished','failed') or not r['running'],500)
                finally:
                    client.post(api+'/fieldsim/stop',json={}).raise_for_status()
                    closed=wait(lambda:client.get(api+'/fieldsim').json(),lambda r:not r['running'],120)
                result=client.get(api+f'/fieldsim/results/{sid}').json()
                runtime=args.out/'runtime/fieldsim'/sid
                report=json.loads((runtime/'session-report.json').read_text())
                check=result.get('check') or {}
                expect={
                    'data_valid': check.get('data_valid') is True,
                    'cleanup': report.get('cleanup_status')=='PASS' and not report.get('remaining_owned_processes'),
                    'exit_code':closed.get('returncode')==(0 if result['state']=='succeeded' else 1),
                }
                contacts=result.get('ground_contact_observations') or []
                zones=[c for c in contacts if 'ground_zones::' in c['surface']]
                if spec.get('execution',{}).get('ground_zones'):
                    expect['physical_zone_contact']=bool(zones) and all(c['contact_entries']>0 for c in zones)
                if spec.get('execution',{}).get('localization_preset'):
                    expect['causal_chain_replay']=((check.get('nonideal_observations') or {}).get('chain_replay') or {}).get('valid') is True
                if case in ('ideal','geometry_control','ground_low','localization','combined'):
                    expect['mission_succeeded']=result['state']=='succeeded' and check.get('valid') is True
                else:
                    expect['honest_outcome']=bool(result.get('errors')) if result['state']=='failed' else check.get('valid') is True
                if case in ('ideal','geometry_control'):
                    dv=(check.get('dynamics') or {}).get('vehicles') or []
                    expect['nominal_wheel_truth_agreement']=bool(dv) and max(v['odom_position_max_m'] for v in dv)<.1
                if case=='ground_low':
                    control=next((r for r in results if r['case']=='geometry_control'),None)
                    if control:
                        nominal=max(v['odom_position_max_m'] for v in control['verdict']['dynamics']['vehicles'])
                        perturbed=max(v['odom_position_max_m'] for v in check.get('dynamics',{}).get('vehicles',[]))
                        expect['friction_effect_over_same_geometry']=perturbed>nominal+.1
                if case=='outage_hold':
                    events=[e['kind'] for e in result.get('execution_events') or []]
                    expect['hold_and_resume']='localization_hold' in events and 'localization_resume' in events
                if case=='severe':
                    expect['not_clean_success']=result['state']=='failed' or result['metrics'].get('interventions',0)>0
                row={'case':case,'session_id':sid,'state':result['state'],'returncode':closed.get('returncode'),
                    'cleanup':report['cleanup_status'],'remaining':report.get('remaining_owned_processes'),
                    'metrics':result['metrics'],'checks':expect,'verdict':check,'errors':result['errors'],
                    'ground_contacts':contacts,'events':result.get('execution_events'),
                    'world_sha256':result.get('execution_provenance',{}).get('world_sha256')}
                results.append(row)
                save(case+'-summary.json',row)
                save('summary.json',{'results':results,'all_expected':all(all(r['checks'].values()) for r in results)})
                print(json.dumps({'case':case,'state':row['state'],'failed_assertions':[k for k,v in expect.items() if not v]},ensure_ascii=False),flush=True)
    except Exception as exc:
        save('failure.json',{'type':type(exc).__name__,'message':str(exc),'completed':len(results)})
        raise
    return 0 if all(all(r['checks'].values()) for r in results) else 1


if __name__=='__main__':
    raise SystemExit(main())
