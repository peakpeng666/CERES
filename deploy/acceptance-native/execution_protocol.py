"""Explicit simulation-clock protocol, separate from unchanged physical predicates.
Budgets remain planner-selected seconds. This does not measure real-time feasibility.
"""
import math
PROTOCOL='simulation_execution_v2'
ENVELOPES={'maze':(900,1800),'warehouse':(5400,7200)}
OUTER={'maze':('full_loop',3600,7500),'warehouse':('warehouse_lifecycle',9000,13200)}
STREAMS=['clock','scan','odom','ground_truth']
def seconds(value,maximum=1800):
    return float(value) if type(value) in (int,float) and 0<value<=maximum and math.isfinite(value) else None

def expected_policy(planner_budget_s,stage):
    budget=seconds(planner_budget_s)
    if not isinstance(stage,str) or stage not in ENVELOPES or budget is None:raise ValueError('invalid_execution_protocol')
    watchdog,_=ENVELOPES[stage]
    return {'policy':'execution_budget_v2','planner_budget_s':budget,
      'planner_formula':'path_length_over_0.10mps_plus_60s_clamped_90_1800_v1',
      'requested':{'budget_s':None,'time_basis':'simulation','wall_watchdog_s':watchdog},
      'effective':{'budget_s':budget,'time_basis':'simulation','wall_watchdog_s':watchdog,
        'clock_no_progress_wall_s':30.0,'telemetry_stale_sim_s':3.0,
        'telemetry_receipt_wall_s':30.0,'telemetry_progress_wall_s':30.0,
        'telemetry_future_skew_sim_s':3.0,'telemetry_zero_stamp_fallback':['ground_truth'],
        'scope':'shared_approach_and_all_legs','navigate_wall_cap_s':None,'required_streams':STREAMS.copy()},
      'budget_provenance':'planner'}

def strict_equal(value,expected):
    if type(expected) in (int,float):return type(value) in (int,float) and value==expected
    if isinstance(expected,dict):return isinstance(value,dict) and set(value)==set(expected) and all(strict_equal(value[k],v) for k,v in expected.items())
    if isinstance(expected,list):return isinstance(value,list) and len(value)==len(expected) and all(strict_equal(a,b) for a,b in zip(value,expected))
    return type(value) is type(expected) and value==expected

def matches_policy(run,stage,planner_budget_s):
    if not isinstance(run,dict):return False
    try:policy=expected_policy(planner_budget_s,stage)
    except ValueError:return False
    return run.get('mode')=='follow_path' and strict_equal(run.get('budget_policy'),policy) and all(strict_equal(run.get(k),policy['effective'][k]) for k in ('budget_s','time_basis','wall_watchdog_s'))

def receipt(stage,planner_budget_s,submitted,terminal):
    if not isinstance(stage,str) or stage not in ENVELOPES:return sanitize({})
    watchdog,overall=ENVELOPES[stage]
    linked=isinstance(submitted,dict) and isinstance(terminal,dict) and all(isinstance(submitted.get(k),str) and submitted[k] and submitted[k]==terminal.get(k) for k in ('run_id','session_id','sim_run_id'))
    return sanitize({'protocol':PROTOCOL,'execution_policy':'execution_budget_v2','source_freshness_sim_s':3.0,'receipt_liveness_wall_s':30.0,'source_progress_wall_s':30.0,'future_skew_sim_s':3.0,'zero_stamp_fallback':'ground_truth','stage':stage,'time_basis':'simulation','mode':'follow_path',
      'selected_clock_budget_s':planner_budget_s,'wall_watchdog_s':watchdog,'overall_wall_budget_s':overall,
      'outer_scope':OUTER[stage][0],'runner_stage_wall_budget_s':OUTER[stage][1],'job_wall_budget_s':OUTER[stage][2],'installation_wall_budget_s':3600,
      'budget_origin':'planner','requested_policy_verified':matches_policy(submitted,stage,planner_budget_s),
      'terminal_policy_verified':bool(linked and matches_policy(terminal,stage,planner_budget_s)),
      'real_time_feasibility_measured':False})

def sanitize(raw):
    raw=raw if isinstance(raw,dict) else {}
    def enum(key,allowed):
        value=raw.get(key);return value if isinstance(value,str) and value in allowed else None
    return {'protocol':enum('protocol',{PROTOCOL,'simulation_execution_v1'}),
      'execution_policy':enum('execution_policy',{'execution_budget_v1','execution_budget_v2'}),
      'source_freshness_sim_s':seconds(raw.get('source_freshness_sim_s'),3),
      'receipt_liveness_wall_s':seconds(raw.get('receipt_liveness_wall_s'),30),
      'source_progress_wall_s':seconds(raw.get('source_progress_wall_s'),30),
      'future_skew_sim_s':seconds(raw.get('future_skew_sim_s'),3),
      'zero_stamp_fallback':enum('zero_stamp_fallback',{'ground_truth'}),
      'stage':enum('stage',set(ENVELOPES)),
      'time_basis':enum('time_basis',{'simulation'}),'mode':enum('mode',{'follow_path'}),
      'selected_clock_budget_s':seconds(raw.get('selected_clock_budget_s')),
      'wall_watchdog_s':seconds(raw.get('wall_watchdog_s'),7200),
      'overall_wall_budget_s':seconds(raw.get('overall_wall_budget_s'),7200),
      'budget_origin':enum('budget_origin',{'planner'}),
      'outer_scope':enum('outer_scope',{'full_loop','warehouse_lifecycle'}),
      'runner_stage_wall_budget_s':seconds(raw.get('runner_stage_wall_budget_s'),9000),
      'job_wall_budget_s':seconds(raw.get('job_wall_budget_s'),13200),
      'installation_wall_budget_s':seconds(raw.get('installation_wall_budget_s'),3600),
      'requested_policy_verified':raw.get('requested_policy_verified') is True,
      'terminal_policy_verified':raw.get('terminal_policy_verified') is True,
      'real_time_feasibility_measured':False}

def protocol_ok(raw,stage):
    if not isinstance(stage,str) or stage not in ENVELOPES:return False
    value=sanitize(raw);watchdog,overall=ENVELOPES[stage]
    return (value['protocol']==PROTOCOL and value['execution_policy']=='execution_budget_v2' and value['source_freshness_sim_s']==3 and value['receipt_liveness_wall_s']==30 and value['source_progress_wall_s']==30 and value['future_skew_sim_s']==3 and value['zero_stamp_fallback']=='ground_truth' and value['stage']==stage and value['time_basis']=='simulation' and value['mode']=='follow_path' and value['budget_origin']=='planner' and value['selected_clock_budget_s'] is not None and value['wall_watchdog_s']==watchdog and value['overall_wall_budget_s']==overall and value['outer_scope']==OUTER[stage][0] and value['runner_stage_wall_budget_s']==OUTER[stage][1] and value['job_wall_budget_s']==OUTER[stage][2] and value['installation_wall_budget_s']==3600 and value['requested_policy_verified'] and value['terminal_policy_verified'])

def maze_receipt(report,sim,solutions,source):
    report=report if isinstance(report,dict) else {}
    sim=sim if isinstance(sim,dict) else {}
    from execution_diagnostics import reconstruct,matching_solution
    plan,_=reconstruct(source,sim,matching_solution(sim,solutions))
    budget=plan.get('budget_s') if isinstance(plan,dict) else None
    steps=report.get('steps') if isinstance(report,dict) else None
    rows=[x for x in steps if isinstance(x,dict) and x.get('name')=='submit sim run (follow_path)' and x.get('ok') is True] if isinstance(steps,list) else []
    submitted=rows[0].get('response') if len(rows)==1 else None
    result=receipt('maze',budget,submitted,sim)
    result['requested_policy_verified']=bool(result['requested_policy_verified'] and strict_equal(report.get('execution_budget_requested'),{'budget_s':None,'time_basis':'simulation','wall_watchdog_s':900}) and strict_equal(report.get('overall_wall_budget_s'),1800))
    return result
