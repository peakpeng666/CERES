"""Simulation telemetry contract: controlled clocks, no native ROS acceptance."""
import ast
import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from urp_lab.execution_timing import (ExecutionTiming, REQUIRED_STREAMS, record_telemetry,
                                     resolve_budget_policy)
from test_session_action_cancellation import session


def fixture(*, basis='simulation', budget=103, watchdog=900, wall=0, sim=0):
    policy = resolve_budget_policy(budget, time_basis=basis,
                                   wall_watchdog_s=watchdog if basis == 'simulation' else None)
    telemetry = {}
    at = {}
    def receive(key, wall, sim, source=None):
        at[key] = wall
        record_telemetry(telemetry,key,wall,sim,sim if source is None else source)
    for key in REQUIRED_STREAMS:
        receive(key,wall,sim)
    return ExecutionTiming(policy,wall,sim,telemetry=telemetry), at, telemetry, receive


def advance_other_streams(receive, wall, sim, exclude='scan'):
    for key in REQUIRED_STREAMS:
        if key != exclude:
            receive(key,wall,sim)


def test_wall_policy_is_exact_v1_and_simulation_is_explicit_v2():
    p = resolve_budget_policy(103)
    assert p == {'policy':'execution_budget_v1', 'planner_budget_s':103.0,
                 'planner_formula':'path_length_over_0.10mps_plus_60s_clamped_90_1800_v1',
                 'requested':{'budget_s':None,'time_basis':None,'wall_watchdog_s':None},
                 'effective':{'budget_s':103.0,'time_basis':'wall','wall_watchdog_s':103.0,
                              'clock_no_progress_wall_s':30.0,'telemetry_stale_wall_s':3.0,
                              'scope':'shared_approach_and_all_legs','navigate_wall_cap_s':90.0,
                              'required_streams':list(REQUIRED_STREAMS)},'budget_provenance':'planner'}
    p = resolve_budget_policy(103,time_basis='simulation',wall_watchdog_s=900)
    assert p['policy']=='execution_budget_v2'
    assert 'telemetry_stale_wall_s' not in p['effective']
    assert p['effective']['telemetry_stale_sim_s']==3
    assert p['effective']['telemetry_progress_wall_s']==30
    assert p['effective']['telemetry_receipt_wall_s']==30


@pytest.mark.parametrize('order', [REQUIRED_STREAMS, tuple(reversed(REQUIRED_STREAMS))])
def test_common_physics_pause_does_not_age_measurements_in_sim_time(order):
    p, at, telemetry, receive = fixture(sim=10)
    for wall in [1,3,3.011,10,29.9]:
        assert p.observe(wall,10,at) is None
    # Resume one callback at a time. Clock-first must not instantly age sensors
    # by the wall duration of the pause, unlike subtracting receipt wall times.
    for key in order:
        current=10.01 if 'clock' in order[:order.index(key)+1] else 10
        receive(key,29.95,current,10.01)
        assert p.observe(29.95,current,at) is None
    assert p.data['max_stream_gap_wall_s']['scan']>3
    assert p.data['max_stream_age_sim_s']['scan']<.02


def test_run17_like_cluster_is_not_an_isolated_scan_failure_in_new_protocol():
    gaps={'clock':2.98374,'ground_truth':2.95637,'odom':2.93479,'scan':3.01063}
    p, at, telemetry, receive = fixture(wall=96,sim=17.5)
    for key,gap in gaps.items():receive(key,99.516-gap,17.5)
    assert p.observe(99.516,17.616,at) is None
    legacy,_,_,_=fixture(basis='wall',wall=96,sim=17.5)
    assert legacy.observe(99.516,17.616,at)=='stream_stale:scan'
    # This is a synthetic regression case, NOT reconstruction of missing run17 headers.


@pytest.mark.parametrize('rtf,expected', [(1,'stream_stale_sim:scan'),(.165,'stream_stale_sim:scan'),(.01,'stream_stale_wall:scan')])
def test_scan_dropout_while_clock_advances_has_two_finite_bounds(rtf,expected):
    p, at, telemetry, receive = fixture()
    reason=None
    for step in range(1,122):
        wall=step*.25;sim=wall*rtf
        advance_other_streams(receive,wall,sim)
        reason=p.observe(wall,sim,at)
        if reason:break
    assert reason==expected
    assert wall<=30.25
    assert p.data['last_observation']['streams']['clock']['receipt_age_wall_s']==0
    assert p.data['last_observation']['streams']['scan']['receipt_age_wall_s']==wall


def test_sim_and_wall_freshness_boundaries_are_strict_greater_than():
    p, at, telemetry, receive = fixture()
    advance_other_streams(receive,3,3)
    assert p.observe(3,3,at) is None
    advance_other_streams(receive,3.001,3.001)
    assert p.observe(3.001,3.001,at)=='stream_stale_sim:scan'
    p, at, telemetry, receive = fixture()
    advance_other_streams(receive,30,1)
    assert p.observe(30,1,at) is None
    advance_other_streams(receive,30.001,1.001)
    assert p.observe(30.001,1.001,at)=='stream_stale_wall:scan'
    p,at,_,_=fixture(basis='wall')
    assert p.observe(3,100,at) is None
    assert p.observe(3.001,100,at)=='stream_stale:clock'


@pytest.mark.parametrize('rtf,expected',[(1,'stream_stale_sim:scan'),(.01,'stream_no_progress:scan')])
def test_duplicate_old_headers_do_not_launder_freshness_or_source_progress(rtf,expected):
    p, at, telemetry, receive = fixture()
    for step in range(1,122):
        wall=step*.25;sim=wall*rtf
        advance_other_streams(receive,wall,sim)
        receive('scan',wall,sim,0)
        reason=p.observe(wall,sim,at)
        if reason:break
    assert reason==expected
    assert telemetry['scan']['received_wall_s']==wall
    assert telemetry['scan']['freshness_sim_s']==0
    assert telemetry['scan']['progress_wall_s']==0
    assert wall<=30.25


def test_future_stamp_has_explicit_skew_bound_and_no_duplicate_credit():
    p,at,telemetry,receive=fixture(sim=10)
    receive('scan',0,10,13)  # exact allowed skew; credit is clipped to /clock at receipt
    assert p.observe(0,10,at) is None
    advance_other_streams(receive,3,13)
    receive('scan',3,13,13)
    assert telemetry['scan']['freshness_sim_s']==10
    assert p.observe(3,13,at) is None
    advance_other_streams(receive,3.01,13.01)
    receive('scan',3.01,13.01,13)
    assert p.observe(3.01,13.01,at)=='stream_stale_sim:scan'
    p,at,telemetry,receive=fixture(sim=10)
    receive('scan',.1,10,13.001)
    assert p.observe(.1,10,at)=='stream_stamp_future:scan'


def test_clock_first_consumer_backlog_is_conservatively_stale_not_called_a_physics_pause():
    p,at,telemetry,receive=fixture(sim=10)
    receive('clock',4,14)
    assert p.observe(4,14,at)=='stream_stale_sim:scan'
    assert p.data['last_observation']['observation_gap_wall_s']==4
    assert p.data['last_observation']['clock_progress_gap_wall_s']==0
    assert p.data['last_observation']['streams']['scan']['source_age_sim_s']==4


def test_sensor_first_small_skew_is_supported_but_large_skew_is_not_silently_fresh():
    p,at,telemetry,receive=fixture(sim=10)
    receive('scan',1,10,10.1)
    assert p.observe(1,10,at) is None
    receive('clock',1.1,10.1)
    assert p.observe(1.1,10.1,at) is None
    receive('scan',2,10.1,1000)
    assert p.observe(2,10.1,at)=='stream_stamp_future:scan'


@pytest.mark.parametrize('source,expected', [(None,'missing'),(float('nan'),'invalid'),(float('inf'),'invalid'),(-1,'invalid'),(9,'backwards')])
def test_missing_nonfinite_negative_and_backward_source_stamps_fail(source,expected):
    p,at,telemetry,receive=fixture(sim=10)
    at['scan']=1
    record_telemetry(telemetry,'scan',1,11,source)
    advance_other_streams(receive,1,11)
    assert p.observe(1,11,at)=='stream_stamp_'+expected+':scan'
    json.dumps(p.data,allow_nan=False)
    # A later callback cannot erase an observed bad-domain/regression event.
    receive('scan',2,12,12)
    assert p.observe(2,12,at)=='stream_stamp_'+expected+':scan'


def test_ground_truth_zero_stamp_fallback_is_narrow_and_labelled():
    p,at,telemetry,receive=fixture()
    for wall in [1,2,3,4]:
        for key in REQUIRED_STREAMS:receive(key,wall,wall,0 if key=='ground_truth' else wall)
        assert p.observe(wall,wall,at) is None
    row=telemetry['ground_truth']
    assert row['source_kind']=='clock_at_receive_zero_stamp'
    assert row['source_stamp_s']==0 and row['freshness_sim_s']==4
    receive('scan',5,5,0)
    assert p.observe(5,5,at)=='stream_stamp_backwards:scan'
    p,at,telemetry,receive=fixture(sim=10)
    receive('ground_truth',1,11,0)
    assert telemetry['ground_truth']['source_kind']=='header'
    assert p.observe(1,11,at)=='stream_stamp_backwards:ground_truth'


@pytest.mark.parametrize('deliver_duplicates',[False,True])
def test_all_frozen_or_duplicate_clock_callbacks_fail_at_thirty_wall(deliver_duplicates):
    p,at,telemetry,receive=fixture(sim=10)
    for wall in range(1,30):
        if deliver_duplicates:
            for key in REQUIRED_STREAMS:receive(key,wall,10)
        assert p.observe(wall,10,at) is None
    if deliver_duplicates:
        for key in REQUIRED_STREAMS:receive(key,30,10)
    assert p.observe(30,10,at)=='clock_no_progress'


def test_resume_clock_without_scan_does_not_reset_scan_deadline():
    p,at,telemetry,receive=fixture(sim=10)
    assert p.observe(20,10,at) is None
    advance_other_streams(receive,21,11)
    assert p.observe(21,11,at) is None
    advance_other_streams(receive,23,13)
    assert p.observe(23,13,at) is None
    advance_other_streams(receive,23.1,13.1)
    assert p.observe(23.1,13.1,at)=='stream_stale_sim:scan'


@pytest.mark.parametrize('bad,expected',[(None,'clock_invalid'),(float('nan'),'clock_invalid'),(float('inf'),'clock_invalid'),(9,'clock_backwards')])
def test_clock_faults_take_priority_without_laundering(bad,expected):
    p,at,telemetry,receive=fixture(sim=10)
    assert p.observe(1,bad,at)==expected
    json.dumps(p.data,allow_nan=False)
    del at['clock']
    assert p.observe(2,10,at)=='clock_missing'


def test_missing_timing_metadata_is_explicit_not_receive_time_fallback():
    p,at,telemetry,receive=fixture()
    del telemetry['scan']
    assert p.observe(1,1,at)=='stream_timing_missing:scan'


def test_watchdog_is_not_extended_by_fresh_telemetry_or_slow_clock():
    p,at,telemetry,receive=fixture(watchdog=5)
    for step in range(1,21):
        wall=step*.25;sim=wall*.001
        for key in REQUIRED_STREAMS:receive(key,wall,sim)
        reason=p.observe(wall,sim,at)
        assert reason==('wall_watchdog' if wall==5 else None)


def test_trigger_snapshot_is_complete_and_bounded_even_when_scan_fails_first():
    p,at,telemetry,receive=fixture()
    for wall in range(1,20):
        for key in REQUIRED_STREAMS:receive(key,wall,wall)
        assert p.observe(wall,wall,at) is None
    receive('clock',24,24)
    with pytest.raises(TimeoutError,match='stream_stale_sim:scan'):p.check(24,24,at)
    snapshot=copy.deepcopy(p.data['trigger_observation'])
    assert set(snapshot['streams'])==set(REQUIRED_STREAMS)
    for key in REQUIRED_STREAMS[1:]:
        assert snapshot['streams'][key]['receipt_age_wall_s']==5
        assert p.data['max_stream_gap_wall_s'][key]==5
    assert len(p.data['recent_observations'])<=8
    assert p.data['max_observation_gap_wall_s']==5
    for key in REQUIRED_STREAMS:receive(key,25,25)
    p.finish(25,25,'failed')
    assert p.data['trigger_observation']==snapshot
    json.dumps(p.data,allow_nan=False)


def test_real_session_observe_records_sources_without_changing_cached_messages():
    source=Path(__file__).parents[1]/'ros/urp_sim_session.py'
    cls=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='Session')
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('observe','sim_time')]
    ns={'time':NS(monotonic=lambda:100),'stamp':lambda s:s.sec+s.nanosec/1e9,
        'REQUIRED_STREAMS':REQUIRED_STREAMS,'record_telemetry':record_telemetry}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Session',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])),str(source),'exec'),ns)
    s=ns['Session']();s.counts={};s.latest={};s.at={};s.timing_telemetry={};s.execution=None
    s.observe('clock',NS(clock=NS(sec=10,nanosec=0)))
    scan=NS(header=NS(stamp=NS(sec=9,nanosec=0)))
    s.observe('scan',scan)
    assert s.latest['scan'] is scan and s.at['scan']==100
    assert s.timing_telemetry['scan']['source_stamp_s']==9
    assert s.timing_telemetry['scan']['received_sim_s']==10
    s.observe('ground_truth',NS(header=NS(stamp=NS(sec=0,nanosec=0))))
    assert s.timing_telemetry['ground_truth']['source_kind']=='clock_at_receive_zero_stamp'


def test_pending_action_survives_physics_pause_but_default_wall_still_cancels():
    for basis in ['simulation','wall']:
        s,h,c=session(result_at=8)
        s.sim_time=lambda:min(c.wall,1)+max(0,c.wall-6)
        p,at,telemetry,receive=fixture(basis=basis)
        s.at=at
        s.action.__func__.__globals__['ExecutionTiming']=ExecutionTiming
        def spin(*args,**kwargs):
            c.wall+=.25
            if not 1<c.wall<6:
                for key in REQUIRED_STREAMS:receive(key,c.wall,s.sim_time())
        s.spin=spin
        if basis=='simulation':
            assert s.action(None,'/follow_path',None,None,execution_timing=p).status==4
            assert h.cancel_calls==0
        else:
            with pytest.raises(TimeoutError,match='stream_stale:clock'):
                s.action(None,'/follow_path',None,90,execution_timing=p)
            assert h.cancel_calls==1 and s.goal is None


def test_terminal_result_cannot_hide_old_source_header_health_failure():
    s,h,c=session(result_at=4)
    p,at,telemetry,receive=fixture()
    s.at=at;s.action.__func__.__globals__['ExecutionTiming']=ExecutionTiming
    def spin(*args,**kwargs):
        c.wall=4
        advance_other_streams(receive,4,4)
        receive('scan',4,4,0)
    s.spin=spin
    with pytest.raises(TimeoutError,match='stream_stale_sim:scan'):
        s.action(None,'/follow_path',None,None,execution_timing=p)
    assert h.cancel_calls==0 and s.goal is None
    assert p.data['trigger']=='stream_stale_sim:scan'
