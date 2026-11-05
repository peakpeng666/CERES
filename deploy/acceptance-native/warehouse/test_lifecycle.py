import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from warehouse_lifecycle import drive, physical_evidence, OVERALL_WALL_S, EXECUTION_POLL_WALL_S
from warehouse_public_summary import project, HASHES, BOOLS
from execution_protocol import expected_policy, receipt
from historical_input import SOURCE

PLAN={'path':[[0,0],[1,0]],'tasks':[{'task':i,'label':str(i),'xy':[i,0]} for i in range(5)],'budget_s':649.6}

def policy_record():
    return {'run_id':'replay_id','session_id':'sim_test','sim_run_id':'sim_run_test',
            'mode':'follow_path','budget_s':PLAN['budget_s'],'time_basis':'simulation','wall_watchdog_s':5400,
            'budget_policy':expected_policy(PLAN['budget_s'],'warehouse')}

class Clock:
    def __init__(self):self.now=0
    def clock(self):return self.now
    def sleep(self,n):self.now+=n

class API:
    def __init__(self,case='success'):
        self.case=case;self.calls=[];self.timeouts=[];self.stopping=False
    def __call__(self,method,path,data=None,timeout=5):
        self.calls.append((method,path,data))
        self.timeouts.append((method,path,timeout))
        if method=='DELETE':self.stopping=True;return {'phase':'stopping'}
        if path=='/sim/session':
            if self.case=='start_uncertain':raise TimeoutError('private start error')
            return {'phase':'starting'}
        if path=='/sim/state':
            if self.stopping:return {'phase':'failed' if self.case=='cleanup_failed' else 'stopped','session_id':'sim_other' if self.case=='cleanup_identity_changed' else None,'errors':[]}
            return {'phase':'failed' if self.case=='startup_failed' else 'ready','session_id':'sim_test'}
        if method=='POST' and path=='/sim/runs':
            row={**policy_record(),'run_id':'replay_id','session_id':'sim_test','sim_run_id':'sim_run_test','planned':{'path':copy.deepcopy(PLAN['path'])},'tasks':copy.deepcopy(PLAN['tasks'])}
            if self.case=='plan_changed':row['planned']['path'][0][0]=9
            if self.case=='identity_changed':row['run_id']='other'
            return row
        if path=='/sim/runs/sim_run_test':
            if self.case=='timeout':return {'state':'executing'}
            return {**policy_record(),'run_id':'replay_id','session_id':'sim_test','sim_run_id':'sim_run_test','state':'failed' if self.case=='sim_failed' else 'succeeded','finished_utc':'now',
                    'planned':{'path':copy.deepcopy(PLAN['path'])},'tasks':[dict(t,state='reached') for t in PLAN['tasks']],
                    'mode':'follow_path','localization':{'mode':'ground_truth'},'world':'warehouse'}
        raise AssertionError('unexpected API path')

class LifecycleTests(unittest.TestCase):
    def run_case(self,case,guard=None):
        api=API(case);clock=Clock();saved={}
        result=drive(api,'replay_id',PLAN,guard or (lambda:None),lambda name,data:saved.update({name:data}),clock.clock,clock.sleep)
        return result,api,saved

    def delayed_readiness(self,ready_at,post_delay=0,overall=OVERALL_WALL_S):
        api=API();clock=Clock();saved={}
        def delayed(method,path,data=None,timeout=5):
            if method=='POST' and path=='/sim/session':clock.now+=post_delay
            if method=='GET' and path=='/sim/state' and not api.stopping and clock.now<ready_at:
                return {'phase':'undocking','session_id':'sim_test'}
            return api(method,path,data,timeout)
        result=drive(delayed,'replay_id',PLAN,lambda:None,lambda name,data:saved.update({name:data}),clock.clock,clock.sleep,overall=overall)
        return result,api,clock

    def test_ready_after_600_before_1200_remains_allowed(self):
        for at in (700,1199):
            result,api,clock=self.delayed_readiness(at)
            self.assertTrue(result['passed']);self.assertEqual(clock.now,at)
            self.assertIn('/sim/runs',[path for _,path,_ in api.calls])

    def test_ready_at_1200_hits_readiness_limit(self):
        result,api,clock=self.delayed_readiness(1200)
        self.assertFalse(result['passed']);self.assertEqual(clock.now,1200)
        self.assertNotIn('/sim/runs',[path for _,path,_ in api.calls]);self.assertTrue(api.stopping)

    def test_7200_outer_deadline_wins_over_fresh_1200_allowance(self):
        result,api,clock=self.delayed_readiness(7500,post_delay=6500)
        self.assertFalse(result['passed']);self.assertEqual(clock.now,7200)
        self.assertNotIn('/sim/runs',[path for _,path,_ in api.calls]);self.assertTrue(api.stopping)

    def test_old_outer_budget_rejected_before_start(self):
        result,api,clock=self.delayed_readiness(0,overall=1800)
        self.assertFalse(result['passed']);self.assertEqual(api.calls,[])

    def test_slow_startup_cannot_dispatch_without_full_wall_envelope(self):
        result,api,clock=self.delayed_readiness(1681,post_delay=500)
        self.assertFalse(result['passed']);self.assertEqual(clock.now,1681)
        self.assertNotIn('/sim/runs',[path for _,path,_ in api.calls]);self.assertTrue(api.stopping)

    def test_success_uses_original_sim_api_no_solve_and_stops(self):
        result,api,_=self.run_case('success');self.assertTrue(result['passed'])
        paths=[path for _,path,_ in api.calls];self.assertNotIn('/solve',paths)
        self.assertIn(('DELETE','/sim/session',None),api.calls)
        start=next(body for method,path,body in api.calls if path=='/sim/session' and method=='POST')
        self.assertEqual(start['localization'],'ground_truth');self.assertFalse(start['gui']);self.assertFalse(start['rviz'])
        self.assertEqual(start['spawn']['yaw'],1.570796)
        submitted=next(body for method,path,body in api.calls if path=='/sim/runs' and method=='POST')
        self.assertEqual(submitted,{'run_id':'replay_id','mode':'follow_path','time_basis':'simulation','wall_watchdog_s':5400})
        self.assertIn(('POST','/sim/runs',30),api.timeouts)
        self.assertEqual(result['execution_protocol']['selected_clock_budget_s'],649.6)
        self.assertTrue(result['execution_protocol']['requested_policy_verified'])
        self.assertTrue(result['execution_protocol']['terminal_policy_verified'])

    def test_terminal_poll_uses_watchdog_plus_finalization_not_sim_budget(self):
        for terminal_at in (1000,EXECUTION_POLL_WALL_S-1,EXECUTION_POLL_WALL_S):
            with self.subTest(terminal_at=terminal_at):
                api=API();clock=Clock()
                def delayed(method,path,data=None,timeout=5):
                    if method=='GET' and path=='/sim/runs/sim_run_test' and clock.now<terminal_at:return {'state':'executing'}
                    return api(method,path,data,timeout)
                result=drive(delayed,'replay_id',PLAN,lambda:None,lambda *args:None,clock.clock,clock.sleep)
                self.assertEqual(result['passed'],terminal_at<EXECUTION_POLL_WALL_S)
                self.assertEqual(clock.now,terminal_at)
                self.assertTrue(api.stopping)
                self.assertEqual(PLAN['budget_s'],649.6)

    def test_requested_and_terminal_policy_must_each_match_exact_plan(self):
        mutations=[
            lambda row:row.pop('budget_policy'),
            lambda row:row.update(budget_s=650),
            lambda row:row.update(time_basis='wall'),
            lambda row:row.update(wall_watchdog_s=5401),
            lambda row:row.update(mode='nav2_waypoints'),
            lambda row:row['budget_policy']['requested'].update(budget_s=649.6),
            lambda row:row['budget_policy']['requested'].update(time_basis=None),
            lambda row:row['budget_policy']['requested'].update(wall_watchdog_s=5401),
            lambda row:row['budget_policy']['effective'].update(budget_s=650),
            lambda row:row['budget_policy']['effective'].update(time_basis='wall'),
            lambda row:row['budget_policy']['effective'].update(wall_watchdog_s=5401),
            lambda row:row['budget_policy'].update(budget_provenance='explicit'),
            lambda row:row['budget_policy'].update(planner_budget_s=650),
        ]
        for location in ('submitted','terminal'):
            for mutate in mutations:
                with self.subTest(location=location,mutation=mutate):
                    api=API();clock=Clock();saved={}
                    def changed(method,path,data=None,timeout=5):
                        row=api(method,path,data,timeout)
                        if (location=='submitted' and method=='POST' and path=='/sim/runs') or (location=='terminal' and method=='GET' and path=='/sim/runs/sim_run_test'):mutate(row)
                        return row
                    result=drive(changed,'replay_id',PLAN,lambda:None,lambda name,row:saved.update({name:row}),clock.clock,clock.sleep)
                    self.assertFalse(result['passed']);self.assertTrue(api.stopping)
                    self.assertFalse(result['execution_protocol'][('requested' if location=='submitted' else 'terminal')+'_policy_verified'])
                    self.assertEqual(saved['lifecycle.json']['execution_protocol'],result['execution_protocol'])

    def test_wall_and_sim_durations_never_replace_planner_budget(self):
        api=API();clock=Clock()
        def measured(method,path,data=None,timeout=5):
            row=api(method,path,data,timeout)
            if path=='/sim/runs/sim_run_test':row['metrics']={'duration_sim_s':123,'duration_wall_s':4000}
            return row
        result=drive(measured,'replay_id',PLAN,lambda:None,lambda *args:None,clock.clock,clock.sleep)
        self.assertTrue(result['passed']);self.assertEqual(result['execution_protocol']['selected_clock_budget_s'],649.6)
        self.assertFalse(result['execution_protocol']['real_time_feasibility_measured'])

    def test_response_received_after_poll_deadline_cannot_pass(self):
        for suffix,late_at in (('/sim/state',1200),('/sim/runs/sim_run_test',EXECUTION_POLL_WALL_S)):
            with self.subTest(suffix=suffix):
                api=API();clock=Clock()
                def late(method,path,data=None,timeout=5):
                    if method=='GET' and path==suffix and not api.stopping:clock.now=late_at
                    return api(method,path,data,timeout)
                result=drive(late,'replay_id',PLAN,lambda:None,lambda *args:None,clock.clock,clock.sleep)
                self.assertFalse(result['passed']);self.assertTrue(api.stopping)

    def test_every_failure_attempts_session_cleanup_and_never_passes(self):
        for case in ('start_uncertain','startup_failed','plan_changed','identity_changed','cleanup_identity_changed','timeout','sim_failed','cleanup_failed'):
            result,api,_=self.run_case(case)
            self.assertFalse(result['passed'],case);self.assertIn(('DELETE','/sim/session',None),api.calls,case)

    def test_guard_drift_before_dispatch_stops_without_sim_run(self):
        count=0
        def guard():
            nonlocal count;count+=1
            if count==2:raise ValueError('map drift')
        result,api,_=self.run_case('success',guard)
        self.assertFalse(result['passed']);self.assertNotIn('/sim/runs',[p for _,p,_ in api.calls])
        self.assertTrue(api.stopping)

    def test_success_evidence_requires_motion_and_actual_map_check(self):
        life,_,_=self.run_case('success');sim=life['result'];sim['actual']={'ground_truth':[[0,0,0],[1,1,0]]}
        sim['metrics']={'tasks_total':5,'tasks_reached':5,'actual_length_m':1,'actual_map_check':{'checked':True,'collision_free':True}}
        session={'status':'PASS','remaining_owned_processes':[],'abnormal_exits':[],'errors':[],
                 'assets':{'complete':True},'compatibility':{'tf':'private','gui':'private'},'bridge_runtime':{'mode':'source_shutdown_join'}}
        row=physical_evidence(sim,session,life,PLAN);self.assertTrue(row['trajectory_motion_verified']);self.assertTrue(row['cleanup_pass'])
        sim['actual']['ground_truth']=[[0,0,0],[1,0,0]]
        self.assertFalse(physical_evidence(sim,session,life,PLAN)['trajectory_motion_verified'])
        self.assertTrue(row['execution_mode_verified'])
        sim['localization']={'mode':'amcl'}
        self.assertFalse(physical_evidence(sim,session,life,PLAN)['execution_mode_verified'])
        session['remaining_owned_processes']=[123]
        self.assertFalse(physical_evidence(sim,session,life,PLAN)['cleanup_pass'])

    def good(self):
        from historical_fuel import ARCHIVE_SHA256,SELECTED_SHA256,COHORT,ALIAS_PROJECTION
        return {'state':'PASS','source_sha':SOURCE,'harness_sha':'a'*40,
                'raw_resource_manifest_sha256':'2c3acde82c25c0372c8793225abcbc94d4e74be4b441ad130de7c3a475790d66',
                'raw_resources_match_historical':False,'historical_alias_projection':ALIAS_PROJECTION,
                'resource_cache_cohort':COHORT,'historical_cache_verified':True,'historical_fuel_archive_sha256':ARCHIVE_SHA256,
                'historical_selected_manifest_sha256':SELECTED_SHA256,'historical_materialized_file_count':61,
                **{k:'b'*64 for k in HASHES},**{k:True for k in BOOLS},
                'tasks_total':5,'tasks_reached':5,'trajectory_samples':2,'actual_length_m':1,
                'remaining_owned_process_count':0,'gateway_exit_code':0,'physical_budget_s':649.6,
                'overall_wall_budget_s':7200,'execution_protocol':receipt('warehouse',649.6,policy_record(),policy_record()),
                'historical_resource_count':45,'current_resource_count':45,'missing_resource_count':0,
                'additional_resource_count':0,'changed_resource_count':0}

    def test_historical_cache_provenance_required_and_idempotent(self):
        row=self.good();first=project(row,require_runner=False);self.assertEqual(first,project(first,require_runner=False))
        for key,value in [('resource_cache_cohort','current_official_assets_v1'),('historical_cache_verified',False),
                          ('historical_fuel_archive_sha256','0'*64),('historical_selected_manifest_sha256',None),
                          ('historical_materialized_file_count',55),('historical_alias_projection',None),('raw_resource_manifest_sha256','0'*64),('raw_resources_match_historical',True)]:
            changed=dict(row);changed[key]=value;self.assertEqual(project(changed,require_runner=False)['state'],'FAIL')

    def test_projection_replay_pass_has_no_fake_solver_or_full_loop(self):
        result=project(self.good(),require_runner=False);self.assertEqual(result['state'],'PASS');self.assertFalse(result['solver_executed'])
        self.assertNotIn('full_loop',json.dumps(result));self.assertEqual(result['route_origin'],'historical_fixed_witness')

    def test_private_then_public_projection_needs_runner_receipt(self):
        stage=project(self.good(),require_runner=False)
        self.assertEqual(stage['state'],'PASS')
        self.assertEqual(project(stage)['state'],'FAIL')
        good={'exit_code':0,'cleanup_remaining':0,'forced_kill_count':0}
        self.assertEqual(project(stage,good)['state'],'PASS')
        for key in good:
            bad=dict(good);bad[key]=1
            self.assertEqual(project(stage,bad)['state'],'FAIL')

    def test_all_boolean_evidence_gates_required(self):
        for field in BOOLS:
            row=self.good();row[field]=None;self.assertEqual(project(row,require_runner=False)['state'],'FAIL',field)
        for field in HASHES:
            row=self.good();row[field]=None;self.assertEqual(project(row,require_runner=False)['state'],'FAIL',field)

    def test_missing_or_mismatched_clock_protocol_cannot_pass(self):
        for field,value in (('requested_policy_verified',False),('terminal_policy_verified',False),
                            ('selected_clock_budget_s',650),('wall_watchdog_s',900),
                            ('overall_wall_budget_s',1800),('time_basis','wall'),('mode','nav2_waypoints')):
            row=self.good();row['execution_protocol'][field]=value
            self.assertEqual(project(row,require_runner=False)['state'],'FAIL',field)
        row=self.good();row.pop('execution_protocol')
        self.assertEqual(project(row,require_runner=False)['state'],'FAIL')

    def test_clock_projection_drops_raw_errors_and_nonfinite_values(self):
        row=self.good();row['execution_protocol'].update(secret='/private/token',time_basis='SECRET',wall_watchdog_s=float('inf'))
        row['execution_diagnostics']={'budget_policy':{'secret':'SECRET'},'execution_timing':{'secret':'SECRET'},'error':'SECRET'}
        result=project(row,require_runner=False);text=json.dumps(result,allow_nan=False)
        self.assertEqual(result['state'],'FAIL');self.assertNotIn('SECRET',text);self.assertNotIn('/private',text)

    def test_missing_resources_and_wrapper_failure_override_pass(self):
        raw=self.good();raw['resources_match_historical']=None
        self.assertEqual(project(raw)['state'],'FAIL')
        self.assertEqual(project(self.good(),{'exit_code':0,'cleanup_remaining':1})['state'],'FAIL')

    def test_projection_drops_raw_strings_and_missing_measurements_stay_null(self):
        raw=self.good();raw.update(error='/private/PATH',current_execution_sha256='SECRET',duration_sim_s=float('nan'))
        result=project(raw);text=json.dumps(result,allow_nan=False)
        self.assertNotIn('SECRET',text);self.assertNotIn('/private',text)
        self.assertIsNone(result['duration_sim_s']);self.assertEqual(result['measurement_availability']['duration_sim_s'],'not_measured')

    def test_missing_summary_keeps_runner_diagnostics(self):
        result=project({}, {'stage':'installation','exit_code':7,'cleanup_remaining':0})
        self.assertEqual(result['runner_exit_code'],7);self.assertEqual(result['runner_stage'],'installation')
        self.assertEqual(result['state'],'FAIL')


class MapBindingTests(unittest.TestCase):
    def test_live_map_snapshot_binds_yaml_and_image(self):
        from warehouse_acceptance import map_snapshot
        class Cached:
            def __call__(self,world):return grid
            def cache_clear(self):self.cleared=True
        with tempfile.TemporaryDirectory() as tmp:
            grid=Mock();grid.yaml=Path(tmp)/'map.yaml';grid.image_path=Path(tmp)/'map.pgm'
            grid.yaml.write_bytes(b'configuration');grid.image_path.write_bytes(b'pixels')
            maps=Mock();maps.load=Cached();maps._SIGNATURES={};maps.fingerprint=lambda _: 'a'*64
            first=map_snapshot(maps)
            grid.image_path.write_bytes(b'changed pixels')
            self.assertNotEqual(map_snapshot(maps)['current_map_bytes_sha256'], first['current_map_bytes_sha256'])
            self.assertTrue(maps.load.cleared)

if __name__=='__main__':unittest.main()
