"""Private execution records -> enums/scalars only. Never participates in PASS."""
from execution_freshness_diagnostics import project as freshness_projection
import importlib
import math
from pathlib import Path
import sys

ERRORS={
 'budget_exhausted':'执行预算耗尽','stream_stale':'执行中传感器/时钟数据超时',
 'action_server_unavailable':'动作服务不可用','goal_rejected':'拒绝目标',
 'cancel_unconfirmed':'未确认取消','cancel_not_settled':'动作取消未收敛',
 'ros_response_timeout':'ROS 响应超时','tasks_incomplete':'动作完成，但实际轨迹未按序到达全部任务',
 'tracking_deviation':'实际路径跟踪偏差超过','goal_tolerance':'实际终点未满足声明容差',
 'map_check_failed':'实际轨迹的整段地图/足迹检查未通过','path_incomplete':'原规划中存在未被实际轨迹覆盖的部分',
 'coverage_incomplete':'实际内缩作业区覆盖低于','execution_check_failed':'独立执行检查失败',
 'stop_requested':'会话停止请求','cancel_requested':'执行取消请求',
 'endpoint_error':'段控制器到达，但真值端点误差',
 'action_aborted':'状态 6，未到达','action_canceled':'状态 5，未到达'}
ERRORS['execution_timing_guard']='execution timing guard:'
TRIGGERS={'budget','stop','cancel','simulation_budget','wall_watchdog','clock_missing','clock_invalid','clock_backwards','clock_no_progress'}|{prefix+':'+stream for prefix in ('stream_missing','stream_stale','stream_timestamp_invalid','stream_stale_wall','stream_timing_missing','stream_stamp_missing','stream_stamp_invalid','stream_stamp_backwards','stream_clock_backwards','stream_stamp_future','stream_stale_sim','stream_no_progress') for stream in ('clock','scan','odom','ground_truth')}
def timing(raw):
    raw=mapping(raw);gaps=mapping(raw.get('max_stream_gap_wall_s'))
    result={'policy':enum(raw.get('policy'),{'execution_budget_v1','execution_budget_v2'}),'trigger':enum(raw.get('trigger'),TRIGGERS),'terminal_state':enum(raw.get('terminal_state'),{'succeeded','failed','canceled'}),'samples':number(raw.get('samples'),10000000,True),'max_no_progress_wall_s':number(raw.get('max_no_progress_wall_s'),10000),'max_stream_gap_wall_s':{k:number(gaps.get(k),10000) for k in ('clock','scan','odom','ground_truth')}}
    for key in ('elapsed_wall_s','trigger_elapsed_wall_s','total_elapsed_wall_s'):result[key]=number(raw.get(key),10000)
    for key in ('elapsed_sim_s','trigger_elapsed_sim_s','total_elapsed_sim_s'):
        value=raw.get(key);result[key]=value if type(value) in (int,float) and -86400<=value<=86400 and math.isfinite(value) else None
    result.update(freshness_projection(raw))
    return result

RECON={'not_attempted','missing_solution','source_unavailable','plan_rejected','geometry_mismatch','reconstructed'}
def mapping(x):return x if isinstance(x,dict) else {}
def number(x,high=1e6,integer=False):
    return x if type(x) in ((int,) if integer else (int,float)) and 0<=x<=high and math.isfinite(x) else None
def enum(x,values):return x if isinstance(x,str) and x in values else None
def boolean(x):return x if type(x) is bool else None
def sanitize(raw):
    raw=mapping(raw);actions=[]
    for a in raw.get('actions',[]) if isinstance(raw.get('actions'),list) else []:
        a=mapping(a);topic=enum(a.get('action'),{'follow_path','navigate_to_pose'})
        if topic is None:continue
        actions.append({'action':topic,'status':number(a.get('status'),6,True),
            'cancel_requested':boolean(a.get('cancel_requested')),
            'cancel_reason':enum(a.get('cancel_reason'),TRIGGERS),
            'cancel_accepted':boolean(a.get('cancel_accepted'))})
    codes=raw.get('error_codes');codes=sorted({x for x in codes if isinstance(x,str) and x in ERRORS}) if isinstance(codes,list) else []
    return {'execution_timing':timing(raw.get('execution_timing')),'error_codes':codes,'error_present':boolean(raw.get('error_present')),
      'actions':actions[-128:],'actions_total':number(raw.get('actions_total'),1e6,True),
      'actions_truncated':boolean(raw.get('actions_truncated')),
      'controller':enum(raw.get('controller'),{'FollowPath','FollowCoverage'}),
      'leg':number(raw.get('leg'),1e6,True),'legs_total':number(raw.get('legs_total'),1e6,True),
      'leg_result_count':number(raw.get('leg_result_count'),1e6,True),
      'budget_s':number(raw.get('budget_s'),1800),
      'budget_provenance':enum(raw.get('budget_provenance'),{'execution_record','reconstructed_to_sim_plan','unavailable'}) or 'unavailable',
      'reconstruction_status':enum(raw.get('reconstruction_status'),RECON) or 'not_attempted',
      'planned_length_m':number(raw.get('planned_length_m')),
      'reconstructed_planned_length_m':number(raw.get('reconstructed_planned_length_m'))}

def matching_solution(sim,solutions):
    rid=mapping(sim).get('run_id')
    if not isinstance(rid,str) or not rid or not isinstance(solutions,list):return None
    matches=[s for s in solutions if isinstance(s,dict) and s.get('run_id')==rid]
    return matches[0] if len(matches)==1 else None

def reconstruct(source,sim,solution,planner=None):
    if not isinstance(solution,dict) or not isinstance(solution.get('scenario_snapshot'),dict):return None,'missing_solution'
    mode=enum(sim.get('mode'),{'follow_path','nav2_waypoints'})
    if not mode:return None,'plan_rejected'
    if planner is None:
        if source is None:return None,'source_unavailable'
        # The caller verifies the pinned checkout before collection. Never import
        # an installed alternate implementation as the reconstruction authority.
        lab=Path(source).resolve()/'src/lab';old=list(sys.path)
        try:
            sys.path.insert(0,str(lab));module=importlib.import_module('urp_lab.simplan')
            if Path(module.__file__).resolve()!=lab/'urp_lab/simplan.py':return None,'source_unavailable'
            planner=module.to_sim_plan
        except Exception:return None,'source_unavailable'
        finally:sys.path[:]=old
    try:plan=planner(solution,solution['scenario_snapshot'],mode)
    except Exception:return None,'plan_rejected'
    if not isinstance(plan,dict):return None,'plan_rejected'
    recorded=mapping(sim.get('planned'));path=recorded.get('path')
    if not isinstance(path,list) or not 2<=len(path)<=200000 or any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not -1e6<=v<=1e6 or not math.isfinite(v) for v in p) for p in path):return None,'geometry_mismatch'
    length=number(recorded.get('length_m'));new_length=number(plan.get('planned_length_m'))
    if plan.get('path')!=path or length is None or new_length is None or not math.isclose(length,new_length,rel_tol=1e-12,abs_tol=1e-9):return None,'geometry_mismatch'
    budget=number(plan.get('budget_s'),1800)
    if budget is None or budget<=0:return None,'plan_rejected'
    return plan,'reconstructed'

def collect(sim,solutions,source=None,planner=None):
    sim=mapping(sim);error=sim.get('error');error=error if isinstance(error,str) else ''
    nav=sim.get('nav2');nav=nav if isinstance(nav,list) else []
    actions=[]
    for action in nav[-128:]:
        action=mapping(action);topic={'/follow_path':'follow_path','/navigate_to_pose':'navigate_to_pose'}.get(action.get('action')) if isinstance(action.get('action'),str) else None
        if topic:actions.append(dict(action,action=topic))
    progress=mapping(sim.get('progress'));budget=number(sim.get('budget_s'),1800)
    if budget==0:budget=None
    result=dict(execution_timing=sim.get('execution_timing'),error_codes=[code for code,marker in ERRORS.items() if marker in error],error_present=bool(error),actions=actions,
        actions_total=len(nav),actions_truncated=len(nav)>128,controller=sim.get('controller'),
        leg=progress.get('leg'),legs_total=progress.get('legs_total'),
        leg_result_count=len(sim['leg_results']) if isinstance(sim.get('leg_results'),list) else None,
        budget_s=budget,budget_provenance='execution_record' if budget is not None else 'unavailable',
        reconstruction_status='not_attempted',planned_length_m=mapping(sim.get('planned')).get('length_m'))
    if budget is None:
        plan,status=reconstruct(source,sim,matching_solution(sim,solutions),planner)
        result['reconstruction_status']=status
        if plan is not None:result.update(budget_s=plan['budget_s'],budget_provenance='reconstructed_to_sim_plan',reconstructed_planned_length_m=plan['planned_length_m'])
    return sanitize(result)
