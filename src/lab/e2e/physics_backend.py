#!/usr/bin/env python3
"""Repeatable physics verification, with step-size/friction/payload sensitivity.

Uses existing Gazebo/ROS, an isolated ROS domain and session partitions. It does
not assert real-machine fidelity or hide perturbation-induced model mismatch.
"""
import argparse
import json
import math
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from xml.etree import ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ceres_lab import fieldworld, field_physics
from ceres_lab.field_dynamics import verify as verify_dynamics
from ceres_lab.scenarios.validate import fleet_routing
from ceres_lab.solvers import fleet


def analyse(data):
    poses = np.asarray(data['rigid_body_poses']['vehicle_1'])
    assert len(poses) > 50 and (np.diff(poses[:,0]) > 0).all()
    def pose(t):
        return np.array([np.interp(t, poses[:,0], poses[:,i]) for i in (1,2)] +
                        [np.interp(t, poses[:,0], np.unwrap(poses[:,6]))])
    stages = {s['name']:s for s in data['stages']}
    values = {}
    for name,s in stages.items():
        a,b = pose(s['begin_s']),pose(s['end_s'])
        values[name] = {'displacement_m':float(np.linalg.norm(b[:2]-a[:2])),
                        'rotation_rad':float(b[2]-a[2]), 'duration_s':s['end_s']-s['begin_s']}
    # Closed-form references for a straight accelerate/hold/brake and spin.
    limits = data['physics']['limits']
    straight = values['straight']; v = stages['straight']['v_mps']
    reference = v*straight['duration_s']-v*v/(2*limits['accel_mps2'])
    brake = v*v/(2*limits['decel_mps2'])
    w = stages['rotate']['w_radps']
    spin = w*values['rotate']['duration_s']-math.copysign(w*w/(2*limits['yaw_accel_radps2']), w)
    values['straight'].update(reference_m=reference, absolute_error_m=abs(straight['displacement_m']-reference))
    values['brake'].update(reference_m=brake, absolute_error_m=abs(values['brake']['displacement_m']-brake))
    values['rotate'].update(reference_rad=spin, absolute_error_rad=abs(values['rotate']['rotation_rad']-spin))
    check = verify_dynamics({**data,'vehicles':[{'name':'vehicle_1'}]})
    assert not data['contacts'], data['contacts']
    # Identical no-slip reference for every shape; retain failed diagnostics as failed.
    spin_bound = .15
    return {'stages':values,'dynamics':check,'spin_bound_rad':spin_bound,
            'spin_slip_ratio': abs(values['rotate']['rotation_rad'] - spin) / max(abs(spin), 1e-9),
            'spin_bound_basis':'same ideal rolling reference for every contact shape; no shape-specific relaxation',
            'reference_scope':'bounded ideal body-velocity acceleration; command transport latency is not subtracted or fitted; no real calibration'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--protocol',choices=['primary','holdout'],default='primary')
    p.add_argument('--cases',default='step4ms,step2ms,step1ms,low_grip,loaded')
    args = p.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    assert 1 <= args.repeats <= 10
    configs={'step4ms':{},'step2ms':{'max_step_size_s':.002},'step1ms':{'max_step_size_s':.001},
             'step4ms_bullet':{'collision_detector':'bullet'},
             'step1ms_bullet':{'collision_detector':'bullet','max_step_size_s':.001},
             'sphere_legacy':{'wheel_contact':'sphere'},
             'cylinder_reference':{'wheel_contact':'tyre'},
             'cylinder_width4':{'wheel_contact':'tyre', 'tyre_width_m':.04},
             'cylinder_width16':{'wheel_contact':'tyre', 'tyre_width_m':.16},
             'tyre_narrow':{'tyre_width_m':.10},
             'lat03_yaw0':{'wheel_lateral_friction':.3,'__initial_yaw':0.0},
             'lat03_yaw45':{'wheel_lateral_friction':.3,'__initial_yaw':math.pi/4},
             'lat03_yaw90':{'wheel_lateral_friction':.3,'__initial_yaw':math.pi/2},
             'lat06_yaw0':{'wheel_lateral_friction':.6,'__initial_yaw':0.0},
             'low_grip':{'ground_friction':.05,'wheel_friction':.05},'loaded':{'payload_kg':20},
             'torque_rich':{'actuator_mode':'torque_limited','wheel_torque_limit_nm':8.0},
             'torque_starved':{'actuator_mode':'torque_limited','wheel_torque_limit_nm':0.5},
             'torque_loaded':{'actuator_mode':'torque_limited','wheel_torque_limit_nm':0.5,'payload_kg':20}}
    rows=[]
    def save(name,data):(out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    for case in args.cases.split(','):
        for repeat in range(args.repeats):
            folder=out/f'{case}-{repeat}';folder.mkdir()
            inst=fleet_routing({'boundary':[[-20,-20],[20,-20],[20,20],[-20,20]],'depot':[0,0],
                'tasks':[{'id':1,'xy':[2,0],'demand':1}],'robot_radius':.5,
                'vehicles':{'count':1,'capacity':1,'speed_mps':1,'travel':'free'}})
            routes,check,*_=fleet.solve({},inst,'panel.sweep',{})
            assert check['valid']
            config = dict(configs[case])
            initial_yaw = config.pop('__initial_yaw', 0.0)
            plan=fieldworld.build(inst,routes,folder,'physics_probe',4,config)
            # Isolated analytic-response fixture at a controlled initial yaw/position; yaw sweeps
            # expose world-frame-locked friction directions (anisotropic mu2 diagnostics).
            doc=ET.parse(plan['sdf']);doc.find(".//model[@name='vehicle_1']/pose").text=f'0 0 0.02 0 0 {initial_yaw!r}'
            doc.write(plan['sdf'])
            plan['vehicles'][0].update(start=[0,0,0],path=[[0,0],[10,0]],tasks=[])
            sid=f'physics_{case}_{repeat}_{time.time_ns()}'
            plan.update(session_id=sid,run_id='physics-probe',scenario_id='physics-probe',field_layers=[],outlines=[],probe_protocol=args.protocol,
                        probe_ground_contacts=True)
            plan['physics_audit']=field_physics.audit_sdf(plan['sdf'],plan['physics'])
            (folder/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
            root=Path(__file__).resolve().parents[3]
            cmd='source /opt/ros/humble/setup.bash && exec bash setup/profile.sh simulation /usr/bin/python3 src/lab/ros/physics_probe.py --runtime '+shlex.quote(str(folder))
            begin=time.monotonic()
            with (folder/'supervisor.log').open('w') as log:
                proc=subprocess.Popen(['bash','-c',cmd],env={**os.environ,'CERES_ROS_DOMAIN_ID':'142'},
                    cwd=root,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                try:
                    rc=proc.wait(timeout=150)
                finally:
                    if proc.poll() is None:
                        proc.send_signal(signal.SIGINT)
                        proc.wait(timeout=45)
            report=json.loads((folder/'session-report.json').read_text())
            assert rc==0 and report['cleanup_status']=='PASS' and not report['remaining_owned_processes'],report
            data=json.loads((folder/'response.json').read_text());assert data['state']=='succeeded'
            r={'case':case,'repeat':repeat,'protocol':args.protocol,'wall_s':time.monotonic()-begin,'returncode':rc,
               'cleanup':report['cleanup_status'],'physics_sha256':data['physics']['sha256'],**analyse(data)}
            # Preserve every response, including failed model verification. A failed
            # maneuver must not prevent the remaining sensitivity measurements.
            r['reference_checks'] = {
                'straight_error_below_0.15_m': r['stages']['straight']['absolute_error_m'] < .15,
                'brake_error_below_0.10_m': r['stages']['brake']['absolute_error_m'] < .10,
                'rotation_error_within_bound_rad': r['stages']['rotate']['absolute_error_rad'] < r['spin_bound_rad'],
                'odometry_consistent': r['dynamics']['valid'],
            }
            r['reference_conformance'] = all(r['reference_checks'].values())
            rows.append(r);save('summary.json',rows)
            print(json.dumps({'case':case,'repeat':repeat,'wall_s':r['wall_s'],'stages':r['stages']},ensure_ascii=False),flush=True)
    metrics={}
    for case in args.cases.split(','):
        rs=[r for r in rows if r['case']==case]
        metrics[case]={stage:{'median':float(np.median([r['stages'][stage][key] for r in rs])),
                             'min':min(r['stages'][stage][key] for r in rs),'max':max(r['stages'][stage][key] for r in rs),
                             'n':len(rs),'quantity':key}
                       for stage,key in [('straight','displacement_m'),('brake','displacement_m'),('rotate','rotation_rad'),('arc','displacement_m')]}
    verification = [r for r in rows if r['case'].startswith('step') or r['case']=='loaded']
    passed = all(r['reference_conformance'] for r in verification) if verification else None
    save('aggregate.json',{'values':metrics,'nominal_reference_conformance':passed,
         'reference_verification_trials':len(verification),
         'sensitivity_only_cases':[r['case'] for r in rows if r not in verification],
         'scope':'small repeated simulation verification and sensitivity study; no statistical superiority or real fidelity claim'})
    return 1 if passed is False else 0


if __name__=='__main__':
    raise SystemExit(main())
