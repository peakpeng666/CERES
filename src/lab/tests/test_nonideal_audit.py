import copy
import math
from xml.etree import ElementTree as ET
import numpy as np
import pytest
from ceres_lab import field_nonideal as ni, fieldworld
from ceres_lab.field_localization import Estimator
from ceres_lab.field_observation_check import verify
from test_backend_integrity import instance, solution


def test_sensor_keeps_declared_bias_and_interpolates_measurement_time():
    cfg = dict(ni.LOCALIZATION_PRESETS['severe']['gnss'], noise_std_m=0, walk_std_m_sqrt_s=0, outages=[])
    sensor = ni.LocalizationModel(cfg, 12)
    for k in range(61):
        t = k*.07
        sensor.on_truth(t, 2*t, t)
    assert len(sensor.observations) == 4
    for r in sensor.observations:
        assert r[2] == pytest.approx(r[0]+.8, abs=1e-8)
        assert r[3] == pytest.approx(2*r[0]-.6, abs=1e-8)


def test_sensor_cannot_deliver_before_its_interpolation_source_is_available():
    cfg=ni.LOCALIZATION_PRESETS['survey']['gnss']
    sensor=ni.LocalizationModel(cfg,1)
    for t in (0,.49,.70): sensor.on_truth(t,0,t)
    assert sensor.observations[0][1] == .65
    assert sensor.deliver(.65)==[]  # .70 truth callback preceded a queued .65 odom callback
    assert len(sensor.deliver(.70))==1


def test_circular_motion_and_rate_independent_noise_density():
    estimates = []
    for hz in (20,100):
        e = Estimator((0,0),0)
        for k in range(hz*2+1): e.on_odom(1,1,k/hz)
        assert np.allclose(e.pose(), [math.sin(2),1-math.cos(2),2], atol=1e-10)
        estimates.append(e.cov)
    assert np.allclose(estimates[0], estimates[1], atol=.0007)


def test_delayed_fix_replays_turns_and_rejects_future_or_old_input():
    e = Estimator((0,0),0,{'noise_std_m':0})
    for k in range(41): e.on_odom(1,1,k*.05)
    assert e.on_fix(math.sin(1),1-math.cos(1),1,2)
    assert e.pose() == pytest.approx((math.sin(2),1-math.cos(2),2),abs=1e-9)
    assert not e.on_fix(100,100,3,2)
    with pytest.raises(ValueError): e.on_odom(0,0,1)


def test_multiple_delayed_fixes_inside_one_odometry_interval_keep_previous_posterior():
    delayed=Estimator((0,0),0,{'noise_std_m':.1})
    live=Estimator((0,0),0,{'noise_std_m':.1})
    for t in (0,.1): delayed.on_odom(0,0,t)
    live.on_odom(0,0,0)
    for t in (.025,.05):
        assert delayed.on_fix(1,0,t,.1)
        live.on_odom(0,0,t)
        assert live.on_fix(1,0,t,t)
    live.on_odom(0,0,.1)
    assert delayed.pose()==pytest.approx(live.pose(),abs=1e-10)
    assert np.allclose(delayed.cov,live.cov,atol=1e-10)


def test_ground_is_algorithm_independent_and_audit_rejects_transforms(tmp_path):
    inst, routes = instance(), solution()['routes']
    a = ni.validate_execution({'ground_preset':'wet_patch'},inst,routes)
    b = ni.validate_execution({'ground_preset':'wet_patch'},inst,[])
    assert a['ground']['zones'] == b['ground']['zones']
    fieldworld.build(inst,routes,tmp_path,'audit_zones',execution=a)
    original = (tmp_path/'world.sdf').read_text()
    for node,path,value in [('collision','pose',None),('collision','pose','1 1 1 0 0 0'),
                             ('visual','geometry/box/size','nan 2 .004')]:
        tree = ET.fromstring(original)
        element = tree.find(f"world/model[@name='ground_zones']/link/{node}/{path}")
        element.text = value
        ET.ElementTree(tree).write(tmp_path/'world.sdf')
        assert not ni.audit_ground_zones(tmp_path/'world.sdf',a)['valid']
    tree = ET.fromstring(original)
    ET.SubElement(tree.find("world/model[@name='ground_zones']"),'pose').text='10 0 0 0 0 0'
    ET.ElementTree(tree).write(tmp_path/'world.sdf')
    assert not ni.audit_ground_zones(tmp_path/'world.sdf',a)['valid']


def test_coplanar_ground_rejects_missing_overlapping_and_raised_support(tmp_path):
    inst,routes=instance(),solution()['routes']
    cfg=ni.validate_execution({'ground_preset':'wet_patch'},inst,routes)
    fieldworld.build(inst,routes,tmp_path,'coplanar',execution=cfg)
    original=(tmp_path/'world.sdf').read_text()
    tree=ET.fromstring(original)
    assert not tree.findall('.//collision/geometry/plane')
    for defect in ('missing','overlap','raised','plane'):
        tree=ET.fromstring(original)
        link=tree.find("world/model[@name='ground']/link")
        col=link.find('collision')
        if defect=='missing': link.remove(col)
        if defect=='overlap': link.append(copy.deepcopy(col))
        if defect=='raised':
            xyz=col.find('pose').text.split();xyz[2]='0';col.find('pose').text=' '.join(xyz)
        if defect=='plane':
            geom=col.find('geometry');geom.remove(geom.find('box'));ET.SubElement(geom,'plane')
        ET.ElementTree(tree).write(tmp_path/'world.sdf')
        assert not ni.audit_ground_zones(tmp_path/'world.sdf',cfg)['valid'],defect


def chain_fixture():
    gnss = copy.deepcopy(ni.LOCALIZATION_PRESETS['light']['gnss'])
    plan={'execution':{'schema':ni.SCHEMA,'seed':13,'localization':{'gnss':gnss}},
          'vehicles':[{'name':'v','start':[0,0,0]}]}
    sensor, est = ni.LocalizationModel(gnss,13), Estimator([0,0],0,gnss)
    saved,truth,ctrl=[],[],[]
    for k in range(301):
        t=round(k*.02,4)
        sensor.on_truth(t,0,t)
        truth.append([t,t,0,0,0,0,0])
        if k%5==0:
            est.on_odom(1,0,t)
            for f in sensor.deliver(t): est.on_fix(f['x'],f['y'],f['t_measure'],t)
            pose=list(est.pose())
            saved.append([t,*pose,'fused'])
            ctrl.append([t,t,0,1,0,'driving',0,pose,t])
    sensor.finalize(6)
    result={'localization_inputs':{'v':{'odometry':est.inputs,'truth_sensor':sensor.truth_inputs}},
            'wheel_odometry':{'v':[[t,t,0,0,v,w] for t,v,w in est.inputs]},
            'rigid_body_poses':{'v':truth},'pose_estimates':{'v':saved},
            'localization_observations':{'v':sensor.observations},'control_traces':{'v':ctrl}}
    return plan,result


def test_actual_control_chain_reconstruction_rejects_truth_bypass_and_corrupt_fixes():
    plan,result=chain_fixture()
    report=verify(plan,result)
    assert report['valid'],report['errors']
    corrupt=copy.deepcopy(result)
    corrupt['localization_observations']['v'][2][2]+=1
    assert not verify(plan,corrupt)['valid']
    leak=copy.deepcopy(result)
    c=leak['control_traces']['v'][-1]
    c[7]=[c[0],0,0]
    assert not verify(plan,leak)['valid']
    early=copy.deepcopy(result)
    early['localization_observations']['v'][2][6]-=.2
    assert not verify(plan,early)['valid']
    wheels=copy.deepcopy(result)
    wheels['wheel_odometry']['v'][-1][4]+=.1
    assert not verify(plan,wheels)['valid']


def test_localization_only_cannot_relax_physical_reference_gate():
    assert not ni.slip_experiment({'mode':'nonideal','ground':{'zones':[]}},None)
    assert ni.slip_experiment(None,{'wheel_lateral_friction':.2})


def task_fixture():
    v={'name':'v','label':'v','color':'red','path':[[1,1],[2,1],[2,2]],
       'tasks':[{'id':1,'xy':[2,1],'service_s':1.0}]}
    # A stationary service interval and final body stop, with the same unchanged
    # corner acceptance radius used by the physical route follower.
    rows=[[k*.02,2,1,0] for k in range(101)]
    result={'trails':{'v':rows},'tasks':[{'vehicle':'v','id':1,'state':'reached',
        't_sim':.1,'service_started_at':.2,'serviced_at':1.2}]}
    return {'vehicles':[v]},result


@pytest.mark.parametrize('defect',[None,'outside_radius','moving_during_service','spinning_during_service','short_dwell','body_not_stopped','body_still_spinning'])
def test_task_success_requires_truth_occupancy_and_stationary_dwell(defect):
    from ceres_lab.fieldcheck import verify_task_truth
    plan,result=task_fixture()
    if defect=='outside_radius':
        for r in result['trails']['v']: r[1]+=.21
    if defect=='moving_during_service':
        for r in result['trails']['v']:
            if .4<=r[0]<.6: r[1]+=.02
    if defect=='spinning_during_service':
        for r in result['trails']['v']:
            r[3]=.1*max(0,min(r[0]-.2,1.0))
    if defect=='short_dwell': result['tasks'][0]['service_started_at']=.3
    if defect=='body_not_stopped':
        for r in result['trails']['v']:
            if r[0]>1.5: r[1]+=.1*(r[0]-1.5)
    if defect=='body_still_spinning':
        for r in result['trails']['v']:
            if r[0]>1.5: r[3]+=.1*(r[0]-1.5)
    report=verify_task_truth(plan,result)
    assert report['valid'] is (defect is None),report


def test_truth_stationary_gate_handles_yaw_wrap():
    from ceres_lab.fieldcheck import verify_task_truth
    plan,result=task_fixture()
    for row in result['trails']['v']:
        row[3]=(math.pi-.005+.005*row[0]+math.pi)%(2*math.pi)-math.pi
    assert verify_task_truth(plan,result)['valid']


def test_stationary_service_clock_excludes_braking():
    from ceres_lab.field_tracking import Driver
    plan,_=task_fixture()
    d=Driver(plan['vehicles'][0],.7)
    d.state='driving'
    d.strict_service=True
    d.observe((2,1,0),0)
    d.step(0)
    assert d.state=='servicing'
    for t,speed in ((.1,.1),(.2,0),(1.1,0),(1.21,0),(2.1,0)):
        d.observe((2,1,0),t)
        d.speed_now=speed
        d.step(t)
    assert d.tasks[0]['service_started_at']==1.21
    assert d.tasks[0]['serviced_at'] is None
    d.observe((2,1,0),2.22)
    d.step(2.22)
    assert d.tasks[0]['serviced_at']==2.22


def test_moving_outcome_is_failure_but_recorded_distance_can_remain_valid():
    from ceres_lab.fieldcheck import verify_motion_metrics
    result={'vehicles':[{'name':'v','distance_m':1,'speed':.1}],
            'trails':{'v':[[0,0,0,0],[10,1,0,0]]},'metrics':{'total_distance_m':1}}
    report=verify_motion_metrics(result)
    assert report['data_valid'] and not report['valid']
    result['vehicles'][0]['distance_m']=0
    assert not verify_motion_metrics(result)['data_valid']


def test_result_flag_cannot_select_acceptance_regime():
    from ceres_lab.fieldcheck import verify as check_result
    plan={'instance':instance(),'vehicles':[],'travel':'free','robot_radius_m':.5}
    result={'trails':{},'execution':{'mode':'nonideal'}}
    report=check_result(plan,result)
    assert not report['data_valid']
    assert any('冻结计划' in e for e in report['errors'])


GUARD = {'slip_gate_m':.6, 'slip_sustain_s':3, 'slip_clear_m':.3,
         'slip_clear_s':2, 'slip_creep_max_s':30}


@pytest.mark.parametrize('interruption', ['middle_band', 'stale', 'hold'])
def test_guard_recovery_requires_uninterrupted_fresh_low_innovation(interruption):
    state={'since':78, 'creep_since':80, 'clear_since':None}
    advance=lambda t, value=.2, fresh=True: ni.advance_innovation_guard(state,value,fresh,True,t,GUARD)
    assert advance(100) is None
    if interruption=='hold': ni.reset_guard_evidence(state)
    else: assert advance(101, .4 if interruption=='middle_band' else .2, interruption!='stale') is None
    assert state['creep_since']==80 and state['clear_since'] is None
    assert advance(102.2) is None  # old implementation recovered too early here
    assert advance(104.3)=='recovered'


def test_guard_stale_evidence_does_not_clear_degraded_deadline_or_trigger_waiting_robot():
    state={'since':None, 'creep_since':None, 'clear_since':None}
    advance=lambda t, fresh=True, driving=True: ni.advance_innovation_guard(state,.9,fresh,driving,t,GUARD)
    assert advance(0) is None and advance(4,driving=False) is None
    assert advance(5,fresh=False) is None and state['since'] is None
    assert advance(6) is None and advance(9)=='creep'
    assert advance(40,fresh=False)=='stop' and state['creep_since']==9
