import copy
import pytest
from urp_lab.scenarios import get_scenario
from urp_lab.solvers import run_algorithm
from urp_lab.solvers.f2c_coverage import check_path,available
from urp_lab.simplan import to_sim_plan
from urp_lab.execution_check import check

@pytest.mark.skipif(not available()['available'],reason='optional F2C missing')
@pytest.mark.parametrize('name',['boustrophedon','snake'])
@pytest.mark.requires_maps('depot')
def test_f2c_segments_curvature_and_execution_legs(name):
    s=get_scenario('field-coverage-depot');r=run_algorithm(s,s['instance'],'f2c.'+name,{})
    assert r['check']['valid'] and r['map_check']['collision_free']
    assert r['metrics']['max_sampled_curvature_per_m']<=1/s['instance']['turn_radius']*1.001
    assert r['metrics']['working_area_fraction']==pytest.approx(1)
    assert r['metrics']['coverage_fraction']<.5 # headland is not counted as worked
    r.update(frame=s['frame'],instance=s['instance']);p=to_sim_plan(r,s,'follow_path')
    assert len(p['legs'])==len(r['raw_result']['swaths'])==5
    assert all(a[-1]==b[0] for a,b in zip(p['legs'],p['legs'][1:]))
    bad=copy.deepcopy(s['instance']);bad['headland_width']=.6
    r=run_algorithm(s,bad,'f2c.'+name,{})
    assert r['status']=='no_solution' and not r['check']['valid'] and not r['routes']
    assert r['raw_result']['generated_polyline'] # preserve rejected geometry evidence

def test_coverage_checker_rejects_corner_and_crossing_hole():
    inst=dict(field=[[0,0],[10,0],[10,10],[0,10]],obstacles=[[[4,4],[6,4],[6,6],[4,6]]],headland_width=1,robot_width=.35,swath_width=.5,turn_radius=1)
    c,_=check_path(inst,[[2,5],[8,5]],[[[2,5],[8,5]]])
    assert not c['valid'] and any('足迹' in x for x in c['errors'])
    inst['obstacles']=[]
    c,_=check_path(inst,[[2,2],[2.1,2],[2.1,2.1]],[[[2,2],[2.1,2]]])
    assert not c['valid'] and any('曲率' in x for x in c['errors'])

@pytest.mark.requires_maps('depot')
def test_partial_actual_coverage_is_not_complete():
    s=get_scenario('field-coverage-depot');i=s['instance']
    e=dict(tracking_trajectory=[[0,-12.7,-1,0],[1,-12.5,-1,0]])
    r=check({'coverage_instance':i,'path':[[-12.7,-1],[-8.3,-1]]},e,'depot')
    assert r['actual_working_coverage_fraction']<.05 and r['coverage_required_fraction']==.9

def test_learned_split_isolation():
    from urp_lab.learned import build, features
    from pathlib import Path
    import importlib.util
    spec=importlib.util.spec_from_file_location('study',Path(__file__).parents[1]/'e2e/train_selector.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    rows=[]
    for n in range(24):
        _,inst=m.generate(170000+n)
        rows.append(dict(id=str(n),instance=inst,features=features(inst),split='train' if n<10 else 'calibration' if n<20 else 'test',costs={'pyvrp.hgs':100+n,'ortools.gls':110-n}))
    before=build(rows)
    for r in rows:
        if r['split']=='test':r['costs']={'pyvrp.hgs':1e20,'ortools.gls':-1e20}
    assert before==build(rows)

def test_learned_model_integrity_and_scope(tmp_path,monkeypatch):
    import json
    from urp_lab import learned, recommend
    from urp_lab.scenarios.benchmark import cvrp_scenarios
    scenarios={s['id']:s for s in cvrp_scenarios()}
    sc=scenarios['bench-cvrp-uniform-n20']
    result=learned.recommend(sc,sc['instance'],budget_s=.25)
    assert result['status']=='available' and result['evaluation']['test_n']==30
    assert result['profile_match'] and result['budget_match'] and result['objective_match']
    assert result['scope_metadata']['evaluated_objective']=='sum_unrounded_euclidean_distance'
    assert result['scope_metadata']['evaluated_repetitions_per_algorithm']==2
    assert '未经验证' in result['scope_metadata']['caveat'] or '不证明' in result['scope_metadata']['caveat']
    unknown=learned.recommend(sc,sc['instance'])
    assert unknown['status']=='out_of_profile' and unknown['budget_match'] is None and not unknown['profile_match']
    assert unknown['prediction'] is None and unknown['evaluation']['test_n']==30
    mismatch=learned.recommend(sc,sc['instance'],budget_s=3)
    assert mismatch['status']=='out_of_profile' and mismatch['budget_match'] is False
    rounded=copy.deepcopy(sc['instance']);rounded['objective']='sum_rounded_euclidean_distance'
    rounded_result=learned.recommend(sc,rounded,budget_s=.25)
    assert rounded_result['status']=='out_of_scope' and not rounded_result['objective_match']
    assert rounded_result['prediction'] is None
    wrong_fleet=copy.deepcopy(sc['instance']);wrong_fleet['fleet']='limited_vehicles'
    assert learned.recommend(sc,wrong_fleet,budget_s=.25)['status']=='out_of_scope'
    advice=recommend.recommend(sc,sc['instance'])
    assert advice['policy']=='rules-v2' and advice['learned_selector']['status']=='out_of_profile'
    assert recommend.recommend(sc,sc['instance'],budget_s=.25)['policy']=='knn-crc-local-v1'
    assert recommend.recommend(sc,rounded,budget_s=.25)['policy']=='rules-v2'
    large=scenarios['bench-cvrp-uniform-n200']
    assert learned.recommend(large,large['instance'],budget_s=.25)['status']=='out_of_scope'
    document=json.loads(learned.MODEL.read_text());document['artifact']['model']['sbs']='tampered'
    model=tmp_path/'model.json';model.write_text(json.dumps(document));monkeypatch.setattr(learned,'MODEL',model)
    assert learned.recommend(sc,sc['instance'],budget_s=.25)['status']=='unavailable'
    model.unlink()
    assert learned.recommend(sc,sc['instance'],budget_s=.25)['status']=='unavailable'

def test_pre_http_simulator_failure_is_visible(tmp_path,monkeypatch):
    import sys,time
    from fastapi.testclient import TestClient
    from urp_lab.gateway import create_app,Config
    app=create_app(Config(runtime=tmp_path,background=True,sim_port=4197,extra_hosts={'testserver'}))
    # This is a process-exit propagation test, not a ROS world/asset acceptance.
    # Freeze valid preflight parameters so a missing system ROS install cannot
    # prevent the deliberately failing child from ever being started.
    monkeypatch.setattr(app.state.lab.services,'sim_params',lambda body: {
        'world':'maze','spawn':{'x':0.,'y':0.,'yaw':0.},
        'gui':False,'rviz':False,'localization':'ground_truth'})
    monkeypatch.setattr(app.state.lab.services,'ros',lambda:{'state':'ready'})
    monkeypatch.setattr(app.state.lab.services,'sim_command',lambda *args:[sys.executable,'-c','raise SystemExit(7)'])
    with TestClient(app) as c:
        response=c.post('/lab/api/sim/session',json={'world':'maze'})
        assert response.status_code==202,response.text
        end=time.monotonic()+6
        while time.monotonic()<end:
            state=c.get('/lab/api/sim/state').json()
            if state['phase']=='failed':break
            time.sleep(.1)
        assert state['phase']=='failed' and any('7' in x for x in state['errors']),state
