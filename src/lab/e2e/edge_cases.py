"""Real API/ROS negative-path acceptance. Expected failures are labeled and retained."""
import copy,json,os,signal,time,urllib.request,urllib.error
from datetime import datetime,timezone
from pathlib import Path
from full_loop import Client

api=Client('http://127.0.0.1:4100')
out=Path(__file__).resolve().parents[1]/'runtime/e2e'/('edge-cases-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
out.mkdir(parents=True,exist_ok=False)
report={'cases':[],'status':'INCOMPLETE'}
def check(name,ok,**data):
 report['cases'].append({'name':name,'ok':bool(ok),**data});print(name,ok,flush=True)
 (out/'edge-cases.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 if not ok:raise AssertionError(name)
def wait(path,field,states,seconds=120):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  code,d,_=api.call('GET',path)
  if code==200 and d.get(field) in states:return d
  time.sleep(.2)
 raise TimeoutError(path)
def solve(sid,aid,params=None,inst=None):
 c,j,_=api.call('POST','/lab/api/solve',{'scenario_id':sid,'instance':inst,'algorithms':[{'id':aid,'params':params or {}}],'label':'验收：预期边界行为 / edge_cases.py'})
 assert c==202,(c,j)
 return j['runs'][0]
def sim(rid,mode):
 c,r,_=api.call('POST','/lab/api/sim/runs',{'run_id':rid,'mode':mode});assert c==202,(c,r);return r['sim_run_id']
def done(rid):return wait('/lab/api/runs/'+rid,'state',{'done','error','timeout','canceled'})
def sim_done(rid):
 r=wait('/lab/api/sim/runs/'+rid,'state',{'succeeded','failed','canceled'},180)
 # Session final state can precede metrics by the bounded stop observation.
 end=time.monotonic()+10
 while not r.get('finished_utc') and time.monotonic()<end:
  time.sleep(.25);_,r,_=api.call('GET','/lab/api/sim/runs/'+rid)
 return r

try:
 r=done(solve('bench-dead_end_infeasible','urp.turn_mode_dp'))
 check('infeasible',r['state']=='done' and r['status']=='infeasible' and not r['routes'] and r['objective']['value'] is None,run=r)
 _,sc,_=api.call('GET','/lab/api/scenarios/farm-rows-warehouse');inst=copy.deepcopy(sc['instance']);nodes=sorted(int(k) for k in inst['coordinates'] if int(k)!=0)[:12]
 inst['customers']=[{'id':i,'node':n,'demand':1,'label':f'T{i}'} for i,n in enumerate(nodes)];inst['capacity']=12
 r=done(solve(sc['id'],'urp.turn_mode_dp',{'time_limit_s':.05},inst))
 check('actual_exact_solver_timeout',r['state']=='timeout' and not r['routes'],run=r)
 rid=solve('bench-cvrp-uniform-n100','pyvrp.ils',{'time_limit_s':30});wait('/lab/api/runs/'+rid,'state',{'running'})
 api.call('POST',f'/lab/api/runs/{rid}/cancel',{});r=done(rid)
 check('cancel_solver',r['state']=='canceled' and not r['routes'],run=r)
 extra_node=next(int(k) for k in inst['coordinates'] if int(k) not in nodes and int(k)!=0)
 inst['customers'].append({'id':12,'node':extra_node,'demand':1})
 r=done(solve(sc['id'],'urp.turn_mode_dp',{},inst))
 check('worker_exception_is_error',r['state']=='error' and r['process']['returncode']!=0,run=r)
 good=done(solve('maze-inspection','urp.turn_mode_dp'))
 c,st,_=api.call('POST','/lab/api/sim/session',{'world':'maze','spawn':{'x':0,'y':0,'yaw':0},'gui':False,'rviz':False})
 check('start_fresh_session',c==202,response=st)
 st=wait('/lab/api/sim/state','phase',{'ready','failed'},150);check('session_ready',st['phase']=='ready',state=st);report['session_id']=st['session_id']
 sid=sim(good['run_id'],'follow_path');wait('/lab/api/sim/runs/'+sid,'state',{'executing'},30)
 time.sleep(1.5);api.call('POST',f'/lab/api/sim/runs/{sid}/cancel',{});r=sim_done(sid)
 wait('/lab/api/sim/state','phase',{'ready'},15)
 check('cancel_moving_robot',r['state']=='canceled' and any(x['status']==5 for x in r['nav2']) and r['metrics'].get('duration_wall_s') is not None,run=r)
 _,base,_=api.call('GET','/lab/api/scenarios/maze-inspection');bad=copy.deepcopy(base['instance']);bad['coordinates']['2']=[1000,1000]
 r=done(solve('maze-inspection','urp.turn_mode_dp',{},bad));check('invalid_geometry_detected',r['map_check']['collision_free'] is False,run_id=r['run_id'])
 c,rejected,_=api.call('POST','/lab/api/sim/runs',{'run_id':r['run_id'],'mode':'follow_path'});check('unsafe_follow_path_rejected',c==422,response=rejected)
 failed=sim_done(sim(r['run_id'],'nav2_waypoints'))
 check('nav2_unreachable_goal',failed['state']=='failed' and any(x['status']==6 for x in failed['nav2']),run=failed)
 recovered=sim_done(sim(good['run_id'],'follow_path'))
 check('navigation_failure_recovery',recovered['state']=='succeeded' and recovered['metrics']['tasks_reached']==2,run=recovered)
 # A deliberately failed owned child validates supervision and cleanup. Never
 # signal a process from another ROS session or one lacking our ownership tag.
 sid=report['session_id'];target=None
 for p in Path('/proc').glob('[0-9]*'):
  try:
   if (b'URP_SIM_SESSION='+sid.encode()) in (p/'environ').read_bytes().split(b'\0') and os.readlink(p/'exe').endswith('/planner_server'):target=int(p.name);break
  except OSError:pass
 check('owned_planner_identified',target is not None,pid=target)
 os.kill(target,signal.SIGTERM)
 st=wait('/lab/api/sim/state','phase',{'stopped','failed'},100)
 check('service_exception_reported',bool(st['errors']),state=st)
 session=Path('src/lab/runtime/sim')/sid/'session-report.json';d=json.loads(session.read_text())
 check('failure_cleanup_no_orphans',d['status']=='FAIL' and not d['remaining_owned_processes'],session_report=str(session))
 # The next start proves the previous failure released the resources.
 c,_,_=api.call('POST','/lab/api/sim/session',{'world':'maze','gui':False,'rviz':False});check('restart_after_service_failure',c==202)
 st=wait('/lab/api/sim/state','phase',{'ready','failed'},150);check('restarted_session_ready',st['phase']=='ready');report['recovery_session_id']=st['session_id']
 r=sim_done(sim(good['run_id'],'follow_path'));check('execute_after_restart',r['state']=='succeeded',run=r)
 api.call('DELETE','/lab/api/sim/session');st=wait('/lab/api/sim/state','phase',{'stopped'},150)
 d=json.loads((Path('src/lab/runtime/sim')/report['recovery_session_id']/'session-report.json').read_text())
 check('normal_shutdown_after_recovery',d['status']=='PASS' and not st['errors'] and not d['remaining_owned_processes'])
 report['status']='PASS'
except Exception as e:
 report['status']='FAIL';report['error']=f'{type(e).__name__}: {e}';raise
finally:
 (out/'edge-cases.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 print('Report:',out/'edge-cases.json',flush=True)
