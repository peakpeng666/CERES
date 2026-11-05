"""Lifecycle over the original gateway API. Injectable clock/API for local tests."""
import math
from pathlib import Path
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from execution_protocol import matches_policy, receipt
from warehouse_diagnostics import failure as describe_failure, section, STATES

TERMINAL={'succeeded','failed','canceled'}
OVERALL_WALL_S=7200
WALL_WATCHDOG_S=5400
FINALIZE_WALL_S=90
SUBMIT_WALL_S=30
EXECUTION_POLL_WALL_S=WALL_WATCHDOG_S+FINALIZE_WALL_S

class Rejected(RuntimeError):pass

def drive(api, run_id, plan, guard, save, clock=time.monotonic, sleep=time.sleep, overall=OVERALL_WALL_S, on_session=None):
    """Always attempt normal session stop even if the start response is uncertain."""
    began=clock();deadline=began+overall; attempted=False; submitted={};result={};stop={};sid=None; failure=None
    diagnostic={'phase':'preflight','session_id_observed':False}
    def mark(phase):diagnostic['phase']=phase
    def remember(row,cleanup=False):
        nonlocal sid
        if not isinstance(row,dict):raise Rejected('session_identifier_invalid')
        value=row.get('session_id')
        diagnostic['cleanup_session_phase' if cleanup else 'last_session_phase']=row.get('phase') if isinstance(row.get('phase'),str) and row.get('phase') in STATES else 'unknown'
        if value is None:return
        if not isinstance(value,str) or len(value)>128 or not re.fullmatch(r'sim_[A-Za-z0-9_]+',value):raise Rejected('session_identifier_invalid')
        if sid is not None and sid!=value:raise Rejected('session_identity_changed')
        sid=value;diagnostic['session_id_observed']=True
        if on_session is not None:on_session(sid)
    def require_execution_envelope():
        if deadline-clock()<SUBMIT_WALL_S+EXECUTION_POLL_WALL_S:raise Rejected('execution_wall_envelope_insufficient')
    def poll(suffix, predicate, limit):
        end=min(deadline,clock()+limit)
        while clock()<end:
            row=api('GET',suffix,timeout=min(5,end-clock()))
            if suffix=='/sim/state':remember(row)
            if clock()>=end:break
            if predicate(row):return row
            sleep(min(1,end-clock()))
        raise Rejected('lifecycle_timeout')
    try:
        guard()
        require_execution_envelope()
        attempted=True
        mark('session_start')
        state=api('POST','/sim/session',{'world':'warehouse','spawn':{'x':-5.08,'y':-.75,'yaw':1.570796},
                                         'gui':False,'rviz':False,'localization':'ground_truth'},timeout=60)
        remember(state)
        mark('session_ready')
        state=poll('/sim/state',lambda r:r.get('phase') in ('ready','failed','stopped'),1200)
        if state.get('phase')!='ready' or state.get('session_id')!=sid or not isinstance(sid,str) or not re.fullmatch(r'sim_[A-Za-z0-9_]+',sid):raise Rejected('session_not_ready')
        guard()
        require_execution_envelope()
        # Keep the planner-selected budget unchanged. Simulation seconds are
        # never converted to wall seconds, and measured RTF never sets policy.
        mark('execution_submit')
        submitted=api('POST','/sim/runs',{'run_id':run_id,'mode':'follow_path',
                      'time_basis':'simulation','wall_watchdog_s':WALL_WATCHDOG_S},timeout=SUBMIT_WALL_S)
        save('submitted-follow_path.json',submitted)
        sim_id=submitted.get('sim_run_id')
        if not isinstance(sim_id,str) or not re.fullmatch(r'sim_run_[A-Za-z0-9_]+',sim_id):raise Rejected('sim_identifier_invalid')
        if submitted.get('run_id')!=run_id or submitted.get('session_id')!=sid:raise Rejected('submitted_identity_mismatch')
        if not matches_policy(submitted,'warehouse',plan['budget_s']):raise Rejected('submitted_execution_policy_mismatch')
        if submitted.get('planned',{}).get('path')!=plan['path'] or [t.get('task') for t in submitted.get('tasks',[])]!=[t['task'] for t in plan['tasks']]:raise Rejected('submitted_plan_mismatch')
        mark('execution_wait')
        result=poll('/sim/runs/'+sim_id,lambda r:r.get('state') in TERMINAL and bool(r.get('finished_utc')),EXECUTION_POLL_WALL_S)
        save('sim-follow_path.json',result)
        mark('execution_check')
        if result.get('run_id')!=run_id or result.get('session_id')!=sid or result.get('sim_run_id')!=sim_id:raise Rejected('terminal_identity_mismatch')
        if not matches_policy(result,'warehouse',plan['budget_s']):raise Rejected('terminal_execution_policy_mismatch')
        guard()
        if result.get('state')!='succeeded':raise Rejected('simulation_failed')
    except BaseException as exc:
        failure=exc
        diagnostic.update(failure_phase=diagnostic['phase'],**describe_failure(exc))
    finally:
        if attempted:
            try:
                if sid is None:
                    diagnostic['identity_probe_attempted']=True
                    try:remember(api('GET','/sim/state',timeout=5))
                    except BaseException as probe:
                        detail=describe_failure(probe)
                        diagnostic['identity_probe_exception_kind']=detail['exception_kind']
                        diagnostic['identity_probe_http_status']=detail['http_status']
                mark('session_stop')
                stop=api('DELETE','/sim/session',timeout=180)
                remember(stop,cleanup=True)
                # Cleanup receives its own bounded time even after physics deadline.
                mark('cleanup_wait')
                end=clock()+240
                while clock()<end:
                    stop=api('GET','/sim/state')
                    remember(stop,cleanup=True)
                    if stop.get('phase') in ('stopped','failed'):break
                    sleep(1)
                if stop.get('session_id') not in (None,sid):raise Rejected('cleanup_identity_mismatch')
                if stop.get('phase')!='stopped' or stop.get('errors'):raise Rejected('session_cleanup_failed')
            except BaseException as exc:
                detail=describe_failure(exc)
                diagnostic.update(cleanup_failure_code=detail['failure_code'],cleanup_exception_kind=detail['exception_kind'],cleanup_http_status=detail['http_status'])
                if failure is None:diagnostic.update(failure_phase=diagnostic['phase'],**detail)
                failure=failure or exc
        diagnostic['elapsed_wall_s']=max(0,clock()-began)
        diagnostic=section(diagnostic)
        protocol=receipt('warehouse',plan['budget_s'],submitted,result)
        save('lifecycle.json',{'session_id':sid,'stop':stop,'failure_kind':type(failure).__name__ if failure else None,
                               'execution_protocol':protocol,'diagnostics':diagnostic})
    return {'result':result,'session_id':sid,'stop':stop,'passed':failure is None,'execution_protocol':protocol,'diagnostics':diagnostic}


def physical_evidence(sim, session, lifecycle, plan):
    metrics=sim.get('metrics') or {};actual=(sim.get('actual') or {}).get('ground_truth')
    valid=isinstance(actual,list) and len(actual)>=2 and all(isinstance(p,list) and len(p)>=3 and all(type(v) in (int,float) and math.isfinite(v) for v in p[:3]) for p in actual)
    moving=bool(valid and any(math.hypot(p[1]-actual[0][1],p[2]-actual[0][2])>.05 for p in actual[1:]))
    cleanup=bool(session.get('status')=='PASS' and session.get('remaining_owned_processes')==[]
                 and session.get('abnormal_exits')==[] and not session.get('errors')
                 and lifecycle['stop'].get('phase')=='stopped' and not lifecycle['stop'].get('errors'))
    checked=metrics.get('actual_map_check') or {}
    compat=session.get('compatibility') or {}
    row={k:metrics.get(k) for k in ('tasks_total','tasks_reached','duration_sim_s','duration_wall_s','actual_length_m','final_error_m','cross_track_max_m')}
    row.update(execution_protocol=lifecycle.get('execution_protocol'),
               tasks_all_reached=(len(sim.get('tasks') or [])==5 and all(t.get('state')=='reached' for t in sim['tasks'])),
               execution_mode_verified=(sim.get('mode')=='follow_path' and isinstance(sim.get('localization'),dict) and sim['localization'].get('mode')=='ground_truth' and sim.get('world')=='warehouse'),
               trajectory_samples=len(actual) if isinstance(actual,list) else None,trajectory_motion_verified=moving,
               actual_map_collision_free=checked.get('checked') is True and checked.get('collision_free') is True,
               cleanup_pass=cleanup,assets_complete=(session.get('assets') or {}).get('complete'),
               runtime_compatibility_verified=(session.get('bridge_runtime') or {}).get('mode')=='source_shutdown_join' and all(bool(compat.get(k)) for k in ('tf','gui')),
               state='PASS' if lifecycle['passed'] and sim.get('state')=='succeeded' else 'FAIL')
    if metrics.get('duration_wall_s') and metrics.get('duration_sim_s') is not None:row['real_time_factor']=metrics['duration_sim_s']/metrics['duration_wall_s']
    if sim.get('planned',{}).get('path')!=plan['path'] or [t.get('task') for t in sim.get('tasks',[])]!=[t['task'] for t in plan['tasks']]:row['state']='FAIL'
    return row
