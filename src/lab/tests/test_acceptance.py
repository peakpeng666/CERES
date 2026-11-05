"""Failure semantics and scientific witness tests; isolated temp stores, no fake production runs."""
import copy
import json
import sys
import threading
import time
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from urp_lab.gateway import create_app, Config
from urp_lab.jobs import JobManager, skeleton, resolve_params
from urp_lab.store import Store
from urp_lab.solvers import catalog, run_algorithm
from urp_lab.simplan import to_sim_plan, NotSimulatable
from urp_lab.scenarios import get_scenario, validate_instance
from urp_lab.scenarios.maps import MapGrid, segment_clearance

@pytest.mark.parametrize('sid,aid',[
 pytest.param('maze-inspection','urp.turn_mode_dp', marks=pytest.mark.requires_maps('maze')),
 pytest.param('maze-inspection','urp.raw_oracle', marks=pytest.mark.requires_maps('maze')),
 pytest.param('maze-inspection','pyvrp.ils+urp.layered', marks=pytest.mark.requires_maps('maze')),
 pytest.param('maze-inspection','ortools.gls+urp.layered', marks=pytest.mark.requires_maps('maze')),
 pytest.param('farm-rows-warehouse','urp.turn_mode_dp', marks=pytest.mark.requires_maps('warehouse')),
 ('bench-cvrp-uniform-n20','pyvrp.ils'), ('bench-cvrp-uniform-n20','ortools.gls'),
 pytest.param('field-coverage-depot','panel.boustrophedon', marks=pytest.mark.requires_maps('depot'))])
def test_actual_solver_witnesses(sid,aid):
    s=get_scenario(sid);r=run_algorithm(s,s['instance'],aid,{'time_limit_s':.15,'seed':42})
    assert r['routes'] and r['check']['valid'],r
    assert r['check']['computed_cost']==pytest.approx(r['objective']['value'])
    if sid=='maze-inspection':assert r['objective']['value']==490

def test_unreachable_is_not_success():
    s=get_scenario('bench-dead_end_infeasible');r=run_algorithm(s,s['instance'],'urp.turn_mode_dp',{})
    assert r['status']=='infeasible' and r['routes']==[] and r['objective']['value'] is None and r['check'] is None

def test_map_segment_detects_between_endpoints_and_cell_boundary():
    clearance=np.ones((10,10));clearance[:,5]=0
    g=MapGrid('test',Path('x'),Path('x'),.1,(0,0,0),10,10,np.zeros((10,10)),clearance>0,clearance)
    assert g.rc(.05,0)==(9,0) and g.rc(.05,1)==(-1,0)
    assert g.clearance_at(.05,1)==0
    assert g.clearance_at(.15,.5)>.2 and g.clearance_at(.85,.5)>.2
    assert segment_clearance(g,(.15,.5),(.85,.5))[0]==0

def solved():
    s=get_scenario('maze-inspection');a=catalog()[0];sol=skeleton('test',s,s['instance'],a,{},None)
    sol.update(run_algorithm(s,s['instance'],a['id'],{}),state='done');return s,sol

@pytest.mark.requires_maps('maze')
def test_simulation_uses_immutable_edited_input():
    s,sol=solved();s['instance']['customers'][0]['node']=2
    plan=to_sim_plan(sol,s,'follow_path')
    assert next(t for t in plan['tasks'] if t['task']==0)['xy']==[-1.5,0]
    assert len(plan['legs'])==4 # closed route must not be prematurely accepted at its start

@pytest.mark.parametrize('mutation',[lambda r:r.update(frame={'kind':'benchmark','units':'unitless'}),lambda r:r.update(check=None),lambda r:r.update(map_check={'collision_free':False})])
@pytest.mark.requires_maps('maze')
def test_invalid_simulation_rejected(mutation):
    s,r=solved();mutation(r)
    with pytest.raises(NotSimulatable):to_sim_plan(r,s,'follow_path')

@pytest.mark.parametrize('v',[float('inf'),float('nan')])
def test_nonfinite_budget(v):
    with pytest.raises(ValueError):resolve_params(catalog()[0],{'time_limit_s':v})

def job(tmp_path, code):
    store=Store(tmp_path/'test.sqlite');manager=JobManager(store,command=[sys.executable,'-c',code],recover=False)
    s=get_scenario('bench-cvrp-uniform-n20');sol=skeleton('test',s,s['instance'],catalog()[0],{},None)
    return manager,store,sol

def test_nonzero_exit_never_becomes_done(tmp_path):
    m,db,s=job(tmp_path,'import sys; print(\'{"status":"feasible","routes":[]}\');sys.exit(7)')
    r=m.execute(s,{},5);assert r['state']=='error' and r['process']['returncode']==7;db.close()

def test_timeout_and_full_raw_logs(tmp_path):
    m,db,s=job(tmp_path,'import time;print("started",flush=True);time.sleep(30)')
    r=m.execute(s,{},.15)
    assert r['state']=='timeout' and r['routes']==[] and r['process']['returncode']!=0
    assert 'started' in (Path(r['process']['evidence'])/'stdout.json').read_text();db.close()

def test_cancel_active_process(tmp_path):
    m,db,s=job(tmp_path,'import time;time.sleep(30)');t=threading.Thread(target=m.execute,args=(s,{},20));t.start()
    end=time.monotonic()+3
    while not m._procs and time.monotonic()<end:time.sleep(.01)
    m.cancel(s['run_id']);t.join(4)
    assert not t.is_alive() and db.get_run(s['run_id'])['state']=='canceled';db.close()

def test_gateway_validation_and_snapshot(tmp_path):
    app=create_app(Config(runtime=tmp_path,background=False,extra_hosts={'testserver'}))
    with TestClient(app) as c:
        assert c.post('/lab/api/solve',json={},headers={'Origin':'http://evil.invalid'}).status_code==403
        assert c.post('/lab/api/solve',content='{}').status_code==415
        assert c.post('/lab/api/solve',json={'scenario_id':'missing','algorithms':['urp.turn_mode_dp']}).status_code==404
        assert c.post('/lab/api/solve',json={'scenario_id':'bench-cvrp-uniform-n20','algorithms':['missing']}).status_code==400
        j=c.post('/lab/api/solve',json={'scenario_id':'bench-cvrp-uniform-n20','algorithms':['urp.js.savings']})
        assert j.status_code==202
        rid=j.json()['runs'][0];end=time.monotonic()+8
        while time.monotonic()<end:
            r=c.get('/lab/api/runs/'+rid).json()
            if r['state'] not in ('queued','running'):break
            time.sleep(.05)
        assert r['state']=='done' and r['check']['valid']
        exported=c.get(f'/lab/api/runs/{rid}/export?format=json').json()
        assert exported['instance']==r['instance'] and exported['scenario_snapshot']['id']=='bench-cvrp-uniform-n20'
        assert r['process']['returncode']==0
        app.state.lab.store.put_sim_run({'sim_run_id':'sim_export_test','run_id':rid,'state':'succeeded','actual':{},'created_utc':r['created_utc']})
        experiment=c.get('/lab/api/sim/runs/sim_export_test/export?format=json').json()
        assert experiment['planning_solution']['instance_sha256']==r['instance_sha256']
        assert experiment['planning_solution']['raw_result']==r['raw_result']

def test_required_node_clean_exit_is_a_session_failure():
    from urp_lab.process_health import unexpected_launch_exit
    assert unexpected_launch_exit('nav','[INFO] [planner_server-3]: process has finished cleanly [pid 123]')
    assert unexpected_launch_exit('nav','CRITICAL FAILURE: SERVER planner_server IS DOWN')
    assert unexpected_launch_exit('sim','[INFO] [parameter_bridge-2]: process has finished cleanly [pid 124]')
    assert unexpected_launch_exit('sim','[ERROR] [create-7]: process has died [pid 125]')
    assert unexpected_launch_exit('sim','[INFO] [create-7]: process has finished cleanly [pid 125]') is None
    assert unexpected_launch_exit('sim','[INFO] [spawner-32]: process has finished cleanly [pid 126]') is None

def test_final_sim_record_waits_for_completed_metrics(tmp_path, monkeypatch):
    from urp_lab.simclient import SimClient
    from urp_lab.services import ServiceManager
    db=Store(tmp_path/'test.sqlite')
    manager=ServiceManager(runtime=tmp_path)
    sim=SimClient(db,None,manager)
    base={'sim_run_id':'sim_test','run_id':'run_test','state':'executing','created_utc':'2026-10-15T00:00:00Z','finished_utc':None,'metrics':{}}
    db.put_sim_run(base)
    monkeypatch.setattr('urp_lab.simclient.http_json',lambda *a,**kw:(404,{}))
    sim._handle_execution({**base,'state':'canceled'})
    assert db.get_sim_run('sim_test')['state']=='executing'
    full={**base,'state':'canceled','finished_utc':'2026-10-15T00:00:00Z','metrics':{'duration_wall_s':2}}
    monkeypatch.setattr('urp_lab.simclient.http_json',lambda *a,**kw:(200,full))
    sim._handle_execution(full)
    assert db.get_sim_run('sim_test')['metrics']['duration_wall_s']==2
    db.close()

def test_fuel_model_identity_and_required_texture(tmp_path, monkeypatch):
    from urp_lab import worlds
    share=tmp_path/'share';cache=tmp_path/'cache'
    monkeypatch.setattr(worlds,'SHARE',share);monkeypatch.setattr(worlds,'CACHE',cache)
    sdf=share/'turtlebot4_ignition_bringup/worlds/warehouse.sdf';sdf.parent.mkdir(parents=True)
    sdf.write_text('<sdf><world><include><uri>https://fuel.gazebosim.org/1.0/Vendor/models/Test</uri></include></world></sdf>')
    model=cache/'fuel.gazebosim.org/vendor/models/test/1';model.mkdir(parents=True)
    (model/'model.sdf').write_text('<sdf><model><link><visual><material><script><uri>file://classic.material</uri></script><pbr><metal><albedo_map>https://fuel.gazebosim.org/1.0/vendor/models/test/1/files/Texture.png</albedo_map></metal></pbr></material></visual></link></model></sdf>')
    r=worlds.inspect_world('warehouse')
    assert not r['complete'] and r['unique_fuel_models']==1 and any('Texture.png' in p for p in r['missing'])
    (model/'Texture.png').write_bytes(b'asset fixture')
    r=worlds.inspect_world('warehouse')
    assert r['complete'] and r['unique_fuel_models']==1 and r['fuel_uri_occurrences']==2 and r['inactive_classic_materials']
