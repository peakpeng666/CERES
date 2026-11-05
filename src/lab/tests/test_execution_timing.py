"""Clock-policy and API plumbing contracts, with fake time. No native acceptance."""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from urp_lab.execution_timing import (ExecutionTiming, MAX_BUDGET_S, MAX_WALL_WATCHDOG_S,
                                     budget_metadata, execution_poll_wall_s, freeze_plan_budget,
                                     resolve_budget_policy, validate_plan_budget, record_telemetry)
from test_session_action_cancellation import session, Interrupted, Future
from test_simclient_startup_budget import StartupDispatch

LAB = Path(__file__).parents[1]


def policy(budget=5, basis='simulation', watchdog=100):
    return resolve_budget_policy(103, budget_s=budget, time_basis=basis,
                                 wall_watchdog_s=watchdog if basis == 'simulation' else None)


def arrivals(wall):
    return {key: wall for key in ('clock', 'scan', 'odom', 'ground_truth')}


def samples(wall, sim, at=None):
    result = {}
    for key, arrival in (arrivals(wall) if at is None else at).items():
        epoch = sim-(wall-arrival) if type(sim) in (int,float) else sim
        record_telemetry(result,key,arrival,epoch,epoch)
    return result


def instrument_session(s, c):
    s.timing_telemetry = samples(c.wall, s.sim_time())
    spin = s.spin
    def tick(*args, **kwargs):
        spin(*args, **kwargs)
        for key, arrived in s.at.items():
            if arrived != s.timing_telemetry[key]['received_wall_s']:
                record_telemetry(s.timing_telemetry,key,arrived,s.sim_time(),s.sim_time())
    s.spin = tick


class PolicyValidation(unittest.TestCase):
    def test_legacy_default_is_wall_and_unchanged_planner_budget(self):
        p = resolve_budget_policy(103)
        self.assertEqual(p['effective']['time_basis'], 'wall')
        self.assertEqual(p['effective']['budget_s'], 103)
        self.assertEqual(p['effective']['wall_watchdog_s'], 103)
        self.assertEqual(p['requested'], dict(budget_s=None, time_basis=None, wall_watchdog_s=None))
        self.assertEqual(p['budget_provenance'], 'planner')

    def test_explicit_override_is_not_dropped_or_clamped(self):
        p = resolve_budget_policy(103, budget_s=25, time_basis='simulation', wall_watchdog_s=900)
        self.assertEqual(p['effective']['budget_s'], 25)
        self.assertEqual(p['planner_budget_s'], 103)
        self.assertEqual(p['budget_provenance'], 'explicit')
        self.assertEqual(execution_poll_wall_s({'budget_policy': p}), 990)

    def test_all_numeric_bounds_and_bool_string_rejected(self):
        for key, limit in [('budget_s', MAX_BUDGET_S), ('wall_watchdog_s', MAX_WALL_WATCHDOG_S)]:
            for invalid in [0, -1, float('nan'), float('inf'), float('-inf'), True, '5', limit+1]:
                kwargs = dict(budget_s=10, time_basis='simulation', wall_watchdog_s=100)
                kwargs[key] = invalid
                with self.subTest(key=key, value=invalid), self.assertRaises(ValueError):
                    resolve_budget_policy(103, **kwargs)
        with self.assertRaises(ValueError): resolve_budget_policy(103, time_basis='simulation')
        with self.assertRaises(ValueError): resolve_budget_policy(103, time_basis='ros')
        with self.assertRaises(ValueError): resolve_budget_policy(103, wall_watchdog_s=104)
        self.assertEqual(resolve_budget_policy(103, budget_s=1800, time_basis='simulation',
                                             wall_watchdog_s=7200)['effective']['wall_watchdog_s'], 7200)

    def test_policy_freeze_revalidation_and_no_aliasing(self):
        original = {'budget_s': 103, 'path': [[0, 0], [1, 0]], 'planned_length_m': 4.3}
        frozen = freeze_plan_budget(original, time_basis='simulation', wall_watchdog_s=900)
        self.assertEqual(validate_plan_budget(frozen), frozen)
        original['path'][0][0] = 9
        self.assertEqual(frozen['path'][0][0], 0)
        metadata = budget_metadata(frozen)
        metadata['budget_policy']['effective']['budget_s'] = 1
        self.assertEqual(frozen['budget_s'], 103)
        for corrupt in ['budget_s', 'time_basis', 'wall_watchdog_s']:
            broken = copy.deepcopy(frozen); broken[corrupt] = -1
            with self.assertRaises(ValueError): validate_plan_budget(broken)
        broken = copy.deepcopy(frozen); broken['budget_policy']['requested']['budget_s'] = 1
        with self.assertRaises(ValueError): validate_plan_budget(broken)
        self.assertEqual(validate_plan_budget({'budget_s': 103})['time_basis'], 'wall')
        json.dumps(frozen, allow_nan=False)


class ClockPolicy(unittest.TestCase):
    def observe(self, p, wall, sim, at, **kwargs):
        return p.observe(wall, sim, at, telemetry=samples(wall,sim,at), **kwargs)

    def test_selected_time_boundary_and_wall_watchdog(self):
        p = ExecutionTiming(policy(), 10, 100)
        self.assertIsNone(self.observe(p, 14.9, 104.9, arrivals(14.9)))
        self.assertEqual(self.observe(p, 15, 105, arrivals(15)), 'simulation_budget')
        p = ExecutionTiming(policy(watchdog=10), 0, 0)
        self.assertIsNone(self.observe(p, 9.9, .99, arrivals(9.9)))
        self.assertEqual(self.observe(p, 10, 1, arrivals(10)), 'wall_watchdog')
        p = ExecutionTiming(policy(basis='wall'), 0, 0)
        self.assertIsNone(self.observe(p, 4.99, 1000, arrivals(4.99)))
        self.assertEqual(self.observe(p, 5, 1000, arrivals(5)), 'budget')

    def test_wall_clock_remains_wall_when_sim_clock_slow_frozen_or_backwards(self):
        for sim in [.01, 0, -10, float('nan')]:
            p = ExecutionTiming(policy(basis='wall'), 0, 0)
            self.assertEqual(self.observe(p, 5, sim, arrivals(5)), 'budget')

    def test_missing_invalid_backward_and_duplicates(self):
        p = ExecutionTiming(policy(), 0, 100)
        self.assertEqual(self.observe(p, 1, 100, {'scan': 1, 'odom': 1, 'ground_truth': 1}), 'clock_missing')
        for bad in [None, float('nan'), float('inf')]:
            p = ExecutionTiming(policy(), 0, bad)
            self.assertEqual(self.observe(p, 1, 100, arrivals(1)), 'clock_invalid')
            p.trigger('clock_invalid', 1, bad); p.finish(2, bad, 'failed')
            json.dumps(p.data, allow_nan=False)
            self.assertIsNone(p.data['start_sim_s'])
        p = ExecutionTiming(policy(), 0, 100)
        self.assertEqual(self.observe(p, 1, 99, arrivals(1)), 'clock_backwards')
        p = ExecutionTiming(policy(), 0, 100)
        for wall in range(1, 30): self.assertIsNone(self.observe(p, wall, 100, arrivals(wall)))
        self.assertEqual(self.observe(p, 30, 100, arrivals(30)), 'clock_no_progress')
        # A genuine advance, not a duplicate arrival, restarts only the stall timer.
        p = ExecutionTiming(policy(), 0, 100)
        self.assertIsNone(self.observe(p, 29, 101, arrivals(29)))
        self.assertIsNone(self.observe(p, 58, 101, arrivals(58)))
        self.assertEqual(self.observe(p, 59, 101, arrivals(59)), 'clock_no_progress')

    def test_each_required_stream_missing_stale_and_exact_boundary(self):
        for key in arrivals(0):
            p = ExecutionTiming(policy(), 0, 0)
            at = arrivals(1); at.pop(key)
            self.assertEqual(self.observe(p, 1, 1, at), 'clock_missing' if key == 'clock' else 'stream_missing:'+key)
            p = ExecutionTiming(policy(basis='wall'), 0, 0)
            self.assertIsNone(self.observe(p, 3, 1, arrivals(0)))
            self.assertEqual(self.observe(p, 3.01, 1, arrivals(0)), 'stream_stale:clock')
            at = arrivals(4); at[key] = 0
            self.assertEqual(self.observe(p, 4, 1, at), 'stream_stale:'+key)

    def test_trigger_is_sticky_and_cannot_be_reset_by_new_leg(self):
        p = ExecutionTiming(policy(budget=2), 0, 100, telemetry=samples(0,100))
        with self.assertRaisesRegex(TimeoutError, 'simulation_budget'): p.check(2, 102, arrivals(2))
        with self.assertRaisesRegex(TimeoutError, 'simulation_budget'): p.check(3, 100, arrivals(3))
        self.assertEqual(p.data['trigger_elapsed_sim_s'], 2)

    def test_terminal_health_checks_do_not_invent_deadline(self):
        p = ExecutionTiming(policy(), 0, 0)
        self.assertIsNone(self.observe(p, 10, 10, arrivals(10), deadlines=False))
        self.assertEqual(self.observe(p, 11, 0, arrivals(11), deadlines=False), 'clock_backwards')


class ExecutionActions(unittest.TestCase):
    def setup_action(self, budget=5, watchdog=100, basis='simulation', **kwargs):
        s, h, c = session(**kwargs)
        s.action.__func__.__globals__['ExecutionTiming'] = ExecutionTiming
        instrument_session(s,c)
        guard = ExecutionTiming(policy(budget, basis, watchdog), c.wall, s.sim_time(), telemetry=s.timing_telemetry)
        return s, h, c, guard

    def run_action(self, s, guard, seconds=90):
        return s.action(None, '/follow_path', None, seconds, execution_timing=guard)

    def test_slow_simulation_exceeds_same_numeric_wall_budget(self):
        s,h,c,p = self.setup_action(result_at=20, rtf=.1)
        self.assertEqual(self.run_action(s,p).status,4)
        self.assertEqual(h.cancel_calls,0);self.assertGreater(c.wall,5)
        s,h,c,p = self.setup_action(result_at=20, rtf=.1, basis='wall')
        with self.assertRaisesRegex(TimeoutError,'预算耗尽'):self.run_action(s,p)
        self.assertEqual(h.cancel_calls,1);self.assertLess(c.wall,7)

    def test_shared_budget_no_fresh_allowance_on_later_leg(self):
        s,h,c,p = self.setup_action(result_at=2, budget=2)
        self.assertEqual(self.run_action(s,p).status,4)
        self.assertIsNone(s.goal)
        old = len(s.actions)
        with self.assertRaisesRegex(TimeoutError,'simulation_budget'):self.run_action(s,p)
        self.assertEqual(len(s.actions),old);self.assertEqual(h.cancel_calls,0)

    def test_watchdog_freeze_and_staleness_cancel_owned_goal(self):
        for kwargs, expected in [(dict(rtf=.001,watchdog=5),'wall_watchdog'),
                                 (dict(rtf=0),'clock_no_progress'), (dict(stale=True),'stream_stale')]:
            s,h,c,p = self.setup_action(result_at=10000, **kwargs)
            with self.assertRaisesRegex(TimeoutError,expected):self.run_action(s,p)
            self.assertEqual(h.cancel_calls,1);self.assertIsNone(s.goal)
            self.assertIn(expected,p.data['trigger'])
            self.assertLess(c.wall,33)

    def test_discovery_uses_wall_time_and_rechecks_before_sending(self):
        s,h,c,p = self.setup_action(result_at=100, budget=1)
        sent=[]
        class Client:
            def __init__(self,*args):pass
            def server_is_ready(self):return c.wall>=2
            def send_goal_async(self,*args,**kwargs):sent.append(True);return Future(c,c.wall,h)
        s.action.__func__.__globals__['ActionClient']=Client
        with self.assertRaisesRegex(TimeoutError,'simulation_budget'):self.run_action(s,p)
        self.assertEqual(sent,[]);self.assertIsNone(s.goal)
        s,h,c,p = self.setup_action(result_at=100, rtf=0)
        class Missing:
            def __init__(self,*args):pass
            def server_is_ready(self):return False
        s.action.__func__.__globals__['ActionClient']=Missing
        with self.assertRaisesRegex(TimeoutError,'动作服务不可用'):self.run_action(s,p)
        self.assertLessEqual(c.wall,25)

    def test_acceptance_delay_does_not_restart_budget(self):
        s,h,c,p = self.setup_action(result_at=100, budget=1)
        class Client:
            def __init__(self,*args):pass
            def server_is_ready(self):return True
            def send_goal_async(self,*args,**kwargs):return Future(c,c.wall+2,h)
        s.action.__func__.__globals__['ActionClient']=Client
        with self.assertRaisesRegex(TimeoutError,'simulation_budget'):self.run_action(s,p)
        self.assertEqual(h.cancel_calls,1);self.assertLess(c.wall,4)

    def test_expired_before_immediate_terminal_goal_not_sent(self):
        s,h,c,p = self.setup_action(result_at=0,budget=1)
        c.wall=1;s.at=arrivals(1)
        with self.assertRaisesRegex(TimeoutError,'simulation_budget'):self.run_action(s,p)
        self.assertEqual(s.actions,[]);self.assertEqual(h.cancel_calls,0)

    def test_terminal_callback_cannot_hide_clock_fault_or_stale_telemetry(self):
        for failure in ['backwards','invalid','stale']:
            s,h,c,p = self.setup_action(result_at=2,budget=100)
            spin=s.spin
            def tick(*args,**kwargs):
                spin(*args,**kwargs)
                if c.wall>=2:
                    if failure=='backwards':s.sim_time=lambda:-1
                    elif failure=='invalid':s.sim_time=lambda:float('nan')
                    else:s.at['scan']=-40
            s.spin=tick
            expected={'backwards':'clock_backwards','invalid':'clock_invalid','stale':'stream_stale_wall:scan'}[failure]
            with self.subTest(failure=failure),self.assertRaisesRegex(TimeoutError,expected):self.run_action(s,p)
            self.assertEqual(h.cancel_calls,0);self.assertIsNone(s.goal)
            self.assertFalse(s.execution['nav2'][0]['cancel_requested'])
            json.dumps(s.execution,allow_nan=False);json.dumps(p.data,allow_nan=False)

    def test_late_processing_cannot_invent_timeout(self):
        s,h,c,p = self.setup_action(result_at=2,terminal_processing=1000)
        self.assertEqual(self.run_action(s,p).status,4)
        self.assertEqual(h.cancel_calls,0);self.assertIsNone(p.data['trigger'])

    def test_cancel_races_stop_and_failed_cleanup_keep_ownership(self):
        for kwargs in [dict(ack_accept=False,terminal_delay=1),dict(ack_delay=100,terminal_delay=.25)]:
            s,h,c,p = self.setup_action(result_at=100, budget=1, **kwargs)
            with self.assertRaisesRegex(TimeoutError,'simulation_budget'):self.run_action(s,p)
            self.assertIsNone(s.goal);self.assertEqual(h.cancel_calls,1)
            self.assertLess(c.wall,4)
        for flag in ['stop_at','cancel_at']:
            s,h,c,p=self.setup_action(result_at=100,rtf=0,**{flag:1})
            with self.assertRaises(Interrupted):self.run_action(s,p)
            self.assertIsNone(s.goal);self.assertEqual(p.data['trigger'],flag.removesuffix('_at'))
        s,h,c,p=self.setup_action(result_at=100, budget=1,terminal_delay=None)
        with self.assertRaisesRegex(TimeoutError,'取消未收敛'):self.run_action(s,p)
        self.assertIsNotNone(s.goal)
        s.cancel_active();self.assertTrue(s.stop.is_set());self.assertIsNotNone(s.goal)


class MetadataPlumbing(unittest.TestCase):
    def test_submit_dispatch_and_terminal_roundtrip(self):
        fixture=StartupDispatch();c,t,workers,posts,rows,cap=fixture.client(ready_at=0)
        solution={'run_id':'solution1','frame':{'kind':'map'},'state':'done','routes':[[0,1]]}
        sc={'sim':{'supported':True,'world':'warehouse'}}
        run=c.submit(solution,sc,'follow_path',budget_s=25,time_basis='simulation',wall_watchdog_s=900)
        frozen=copy.deepcopy(run['budget_policy'])
        self.assertEqual(run['budget_s'],25);self.assertEqual(run['wall_watchdog_s'],900)
        workers[0].target(*workers[0].args)
        plan=posts[0][1]['json']
        self.assertEqual(plan['budget_policy'],frozen)
        self.assertEqual(validate_plan_budget(plan)['budget_policy'],frozen)
        c._finish_local(run,'failed','test failure')
        self.assertEqual(rows['run1']['budget_policy'],frozen)
        json.dumps(rows['run1'],allow_nan=False)

    def test_invalid_budget_rejected_before_autostart_or_queue(self):
        fixture=StartupDispatch();c,t,workers,posts,rows,cap=fixture.client(alive=False)
        with self.assertRaises(Exception):
            c.submit({'run_id':'s','frame':{'kind':'map'},'state':'done','routes':[[0,1]]},
                     {'sim':{'supported':True,'world':'warehouse'}},'follow_path',autostart=True,
                     time_basis='simulation',wall_watchdog_s=float('inf'))
        self.assertEqual(c.services.starts,[]);self.assertEqual(workers,[]);self.assertEqual(rows,{})

    def test_gateway_forwards_policy_keywords(self):
        tree=ast.parse((LAB/'urp_lab/gateway.py').read_text())
        endpoint=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='sim_run_start')
        call=next(n for n in ast.walk(endpoint) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='submit')
        self.assertTrue({'budget_s','time_basis','wall_watchdog_s'} <= {k.arg for k in call.keywords})

    def test_full_loop_explicit_optin_and_wall_polling_contract(self):
        text=(LAB/'e2e/full_loop.py').read_text()
        self.assertIn('--execution-time-basis',text)
        self.assertIn('--execution-budget',text)
        self.assertIn('--execution-wall-watchdog',text)
        self.assertIn('end = deadline(execution_poll_wall_s(sim_run))',text)
        self.assertIn('remaining_wall >= poll_wall',text)
        self.assertNotIn("deadline(sim_run.get('budget_s')",text)
        self.assertIn("**expected['budget_policy']['requested']",text)


if __name__=='__main__':unittest.main()


def test_sim_export_keeps_complete_json_policy_and_exact_legacy_csv_schema():
    import csv
    import io
    from urp_lab.export import export_sim_run
    legacy_fields=['sim_run_id','source','t_sim','x_m','y_m','yaw_rad']
    header='sim_run_id,source,t_sim,x_m,y_m,yaw_rad\r\n'
    metadata_variants=[{}, budget_metadata(freeze_plan_budget({'budget_s':103})),
                       budget_metadata(freeze_plan_budget({'budget_s':103},time_basis='simulation',wall_watchdog_s=900))]
    for metadata in metadata_variants:
        for trace in [[[1,2,3,0]], []]:
            run={'sim_run_id':'s','actual':{'ground_truth':trace},**metadata}
            encoded,_,_=export_sim_run(run,'json')
            decoded=json.loads(encoded)
            assert decoded==run
            if metadata:assert decoded['budget_policy']==run['budget_policy']
            else:assert 'budget_policy' not in decoded
            encoded,_,_=export_sim_run(run,'csv')
            text=encoded.decode('utf-8-sig')
            assert text==header+('s,ground_truth,1,2,3,0\r\n' if trace else '')
            reader=csv.DictReader(io.StringIO(text))
            assert reader.fieldnames==legacy_fields
            rows=list(reader)
            assert len(rows)==len(trace)
            if rows:assert list(rows[0])==legacy_fields


def test_actual_execute_shares_one_epoch_across_approach_and_legs(tmp_path, monkeypatch):
    """Execute the real orchestration method, fake only ROS actions/check subprocess."""
    import math
    import traceback
    import urp_lab.localization_check
    source=LAB/'ros/urp_sim_session.py';tree=ast.parse(source.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Session')
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('execute','navigate','distance_to_path')]
    wall=NS(value=0.)
    def pose():return NS(header=NS(),pose=NS(position=NS(),orientation=NS()))
    ns={'time':NS(monotonic=lambda:wall.value), 'math':math,'copy':copy,'json':json,'traceback':traceback,
        'Path':Path,'LAB':LAB,'now':lambda:'fake-time','ExecutionTiming':ExecutionTiming,'Interrupted':Interrupted,
        'PoseStamped':pose, 'NavigateToPose':NS(Goal=lambda:NS()),'FollowPath':NS(Goal=lambda:NS()),
        'subprocess':NS(run=lambda *a,**k:NS(returncode=0,stdout='{}')), 'save':lambda p,x:p.write_text(json.dumps(x,allow_nan=False))}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Session',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])),str(source),'exec'),ns)
    s=ns['Session']();s.runtime=tmp_path;(tmp_path/'runs').mkdir();s.runs={}
    s.args=NS(world='maze',localization='ground_truth');s.stop=NS(is_set=lambda:False);s.cancel=NS(is_set=lambda:False)
    s.timing_telemetry=samples(0,0)
    s.at=arrivals(0);s.sim_time=lambda:wall.value;s.phase=lambda _:None;s.settle=lambda:None;s.cancel_active=lambda:None
    s.state=lambda:{'telemetry':{}};s.path_msg=lambda p:p;s.plan_pub=NS(publish=lambda _:None)
    s.st={'robot':{'ground_truth':[0,0,0]}};s.latest={}
    s.execution={'sim_run_id':'s','run_id':'r','state':'queued','tasks':[],'metrics':{},'tracking_trajectory':[],
                 'actual':{'ground_truth':[],'amcl':[]},'nav2':[],'error':None}
    calls=[];epochs=[]
    def action(typ,topic,goal,seconds,execution_timing):
        execution_timing.check(wall.value,s.sim_time(),s.at)
        calls.append(topic);epochs.append(id(execution_timing))
        wall.value+=.75;s.at=arrivals(wall.value)
        s.timing_telemetry.update(samples(wall.value,wall.value))
        if topic=='/navigate_to_pose':s.st['robot']['ground_truth']=[goal.pose.pose.position.x,goal.pose.pose.position.y,0]
        else:s.st['robot']['ground_truth']=[*goal.path[-1],0]
    s.action=action
    plan=freeze_plan_budget({'budget_s':103,'path':[[1,0],[2,0],[3,0]],'legs':[[[1,0],[2,0]],[[2,0],[3,0]]],
                             'mode':'follow_path','problem_class':'turn_routing'},budget_s=1.5,
                            time_basis='simulation',wall_watchdog_s=100)
    s.execute(plan)
    assert calls==['/navigate_to_pose','/follow_path']
    assert len(set(epochs))==1
    assert s.execution['state']=='failed' and 'simulation_budget' in s.execution['error']
    assert s.execution['execution_timing']['start_sim_s']==0
    assert s.execution['execution_timing']['trigger_elapsed_sim_s']==1.5
    assert s.execution['leg_results'][0]['endpoint_error_m']==0
    assert (tmp_path/'runs/s.json').is_file()


def test_gateway_http_policy_roundtrip_and_invalid_before_queue(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from urp_lab.gateway import Config, create_app
    app=create_app(Config(runtime=tmp_path,background=False,archive_autostart=False,extra_hosts={'testserver'}))
    lab=app.state.lab
    solution={'run_id':'r','scenario_id':'fixture','scenario_snapshot':{'sim':{'supported':True,'world':'maze'}},
              'frame':{'kind':'map'},'state':'done','routes':[[0,1]]}
    lab.store.put_run(solution)
    monkeypatch.setattr('urp_lab.simplan.to_sim_plan',lambda *a:{'budget_s':103,'path':[[0,0],[1,0]],'tasks':[],'planned_length_m':4.3})
    monkeypatch.setattr(lab.services,'sim_alive',lambda:True)
    lab.services.sim_meta={'world':'maze','session_id':'session1'}
    lab.sim.state={'phase':'ready','session_id':'session1'}
    monkeypatch.setattr(lab.sim,'_dispatch',lambda *a:None)
    with TestClient(app) as client:
        bad=client.post('/lab/api/sim/runs',json={'run_id':'r','time_basis':'simulation'})
        assert bad.status_code==422
        assert not lab.store.active_sim_runs()
        response=client.post('/lab/api/sim/runs',json={'run_id':'r','budget_s':25,'time_basis':'simulation','wall_watchdog_s':900})
        assert response.status_code==202
        queued=response.json();rid=queued['sim_run_id']
        assert queued['budget_s']==25 and queued['time_basis']=='simulation'
        assert queued['budget_policy']['effective']['wall_watchdog_s']==900
        fetched=client.get('/lab/api/sim/runs/'+rid).json()
        assert fetched['budget_policy']==queued['budget_policy']
        full={**queued,'state':'failed','finished_utc':'2026-10-22T00:00:00Z','error':'clock_backwards'}
        monkeypatch.setattr('urp_lab.simclient.http_json',lambda *a,**kw:(200,full))
        lab.sim._handle_execution(full)
        terminal=client.get('/lab/api/sim/runs/'+rid).json()
        assert terminal['budget_policy']==queued['budget_policy']
        exported=client.get('/lab/api/sim/runs/'+rid+'/export?format=json').json()
        assert exported['budget_policy']==queued['budget_policy']


def test_session_handler_freezes_policy_before_queue_and_rejects_corruption():
    import io
    import queue
    import threading
    from http.server import BaseHTTPRequestHandler
    source=LAB/'ros/urp_sim_session.py';tree=ast.parse(source.read_text())
    handler=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='handler')
    ns={'BaseHTTPRequestHandler':BaseHTTPRequestHandler,'json':json,'FINAL':{'succeeded','failed','canceled'},
        'validate_plan_budget':validate_plan_budget,'budget_metadata':budget_metadata,'now':lambda:'fake-time'}
    exec(compile(ast.Module(body=[handler],type_ignores=[]),str(source),'exec'),ns)
    def request(body):
        s=NS(lock=threading.RLock(),st={'phase':'ready'},execution=None,commands=queue.Queue(),cancel=threading.Event(),
             args=NS(session_id='session1',world='maze',localization='amcl'),report={'navigation_profile':{}})
        h=ns['handler'](s).__new__(ns['handler'](s));raw=json.dumps(body).encode();result=[]
        h.headers={'Content-Type':'application/json','Content-Length':str(len(raw)),'Host':'127.0.0.1'}
        h.path='/execute';h.rfile=io.BytesIO(raw);h.answer=lambda code,obj:result.append((code,obj))
        h.do_POST();return s,result
    body=freeze_plan_budget({'budget_s':103,'path':[[0,0],[1,0]],'mode':'follow_path','sim_run_id':'s','run_id':'r',
                             'sources':{},'planned_length_m':4.3,'tasks':[],'task_radius_m':.35},
                            budget_s=25,time_basis='simulation',wall_watchdog_s=900)
    s,response=request(body)
    assert response[0][0]==202
    assert s.execution['budget_policy']==body['budget_policy']
    assert s.execution['planned']['length_m']==4.3
    assert s.commands.get_nowait()[1]['budget_s']==25
    bad=copy.deepcopy(body);bad['budget_s']=26
    s,response=request(bad)
    assert response[0][0]==400 and s.execution is None and s.commands.empty()


def test_full_loop_cli_rejects_invalid_sim_policy_before_network_or_output(tmp_path):
    import subprocess
    import sys
    for options in [ ['--execution-time-basis','simulation'], ['--execution-wall-watchdog','nan'],
                     ['--execution-budget','1801'], ['--budget','inf'], ['--modes',''], ['--modes','missing'] ]:
        out=tmp_path/str(len(list(tmp_path.iterdir())))
        result=subprocess.run([sys.executable,str(LAB/'e2e/full_loop.py'),*options,'--output',str(out)],capture_output=True,text=True,timeout=10)
        assert result.returncode==2 and 'error:' in result.stderr
        assert not out.exists()


def test_simulation_shared_only_nav95_succeeds_wall90_cap_remains():
    for basis in ['simulation','wall']:
        s,h,c=session(result_at=95)
        s.action.__func__.__globals__['ExecutionTiming']=ExecutionTiming
        instrument_session(s,c)
        p=ExecutionTiming(policy(103,basis,200),0,0,telemetry=s.timing_telemetry)
        cap=p.effective['navigate_wall_cap_s']
        assert p.effective['scope']=='shared_approach_and_all_legs'
        if basis=='simulation':
            assert cap is None
            assert s.action(None,'/navigate_to_pose',None,cap,execution_timing=p).status==4
            assert h.cancel_calls==0
        else:
            assert cap==90
            with unittest.TestCase().assertRaisesRegex(TimeoutError,'预算耗尽'):
                s.action(None,'/navigate_to_pose',None,cap,execution_timing=p)
            assert h.cancel_calls==1 and c.wall<93


def test_full_loop_uses_wall_envelope_and_refuses_queue_after_slow_startup(tmp_path, monkeypatch):
    import importlib.util
    import sys
    spec=importlib.util.spec_from_file_location('clock_full_loop_test',LAB/'e2e/full_loop.py')
    full=importlib.util.module_from_spec(spec);spec.loader.exec_module(full)
    scenario={'id':'s','problem_class':'turn_routing','frame':{'kind':'map'},
              'sim':{'supported':True,'world':'maze','spawn':{'x':0,'y':0,'yaw':0}}}
    best={'run_id':'r','state':'done','status':'feasible','algorithm':{'id':'a','source':'fixture'},
          'objective':{'value':1,'unit':'m'},'check':{'valid':True},'map_check':{'collision_free':True},
          'metrics':{'length':4.3},'routes':[]}
    plan={'budget_s':103,'path':[[0,0],[1,0]],'planned_length_m':4.3,'tasks':[]}
    monkeypatch.setattr(full,'to_sim_plan',lambda *a:copy.deepcopy(plan))
    for startup_delay, drop_terminal_policy, expected_status in [(0,False,'PASS'),(60,False,'FAIL'),(0,True,'FAIL')]:
        clock=NS(wall=0.);posts=[];current={}
        def sleep(seconds):clock.wall+=seconds
        monkeypatch.setattr(full,'time',NS(monotonic=lambda:clock.wall,sleep=sleep))
        class Client:
            def __init__(self,base):pass
            def call(self,method,path,body=None,timeout=30,raw=False):
                if raw:return 200,b'fixture',{}
                if path.endswith('/health'):return 200,{'status':'ok'},{}
                if path.endswith('/services'):return 200,{'services':[{'id':'solver','state':'ready'}]},{}
                if path.endswith('/scenarios/s'):return 200,scenario,{}
                if path.endswith('/recommend'):return 200,{'recommended':'a','ranking':[]},{}
                if path.endswith('/algorithms'):return 200,{'algorithms':[{'id':'a','problem_classes':['turn_routing'],'available':True}]},{}
                if path.endswith('/solve'):return 202,{'runs':['r']},{}
                if path.endswith('/runs/r'):return 200,best,{}
                if path.endswith('/sim/session'):return 202,{'phase':'starting'},{}
                if path.endswith('/sim/state'):
                    clock.wall+=startup_delay;return 200,{'phase':'ready','session_id':'session1'},{}
                if path.endswith('/sim/runs'):
                    posts.append(body)
                    frozen=freeze_plan_budget(plan,**{k:body[k] for k in ['budget_s','time_basis','wall_watchdog_s']})
                    current.update(sim_run_id='s',state='queued',**budget_metadata(frozen))
                    return 202,copy.deepcopy(current),{}
                if path.endswith('/sim/runs/s'):
                    result=copy.deepcopy(current)
                    if clock.wall>=20:
                        result.update(state='succeeded',finished_utc='fake-time',metrics={'tasks_total':1,'tasks_reached':1},actual={'ground_truth':[]})
                        if drop_terminal_policy:result.pop('budget_policy')
                    return 200,result,{}
                raise AssertionError(path)
        monkeypatch.setattr(full,'Client',Client)
        out=tmp_path/(str(startup_delay)+'-'+str(drop_terminal_policy))
        monkeypatch.setattr(sys,'argv',['full_loop.py','--scenario','s','--modes','follow_path','--keep-session',
                                       '--execution-time-basis','simulation','--execution-budget','1',
                                       '--execution-wall-watchdog','50','--budget','200','--output',str(out)])
        result=full.main();report=json.loads((out/'report.json').read_text())
        assert report['status']==expected_status
        assert result==(0 if expected_status=='PASS' else 1)
        if startup_delay:assert posts==[] and report['failed_step']=='execution envelope (follow_path)'
        elif drop_terminal_policy:assert report['failed_step']=='terminal execution policy (follow_path)'
        else:assert clock.wall>=20 and len(posts)==1
