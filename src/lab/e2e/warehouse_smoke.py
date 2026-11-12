"""One short headland route and a stationary map/laser audit in the full official warehouse."""
import copy,json,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from full_loop import Client
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'src/lab'))
from ceres_lab.scenarios.maps import load
out=ROOT/'src/lab/runtime/e2e'/('warehouse-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
out.mkdir(parents=True,exist_ok=False)
api=Client('http://127.0.0.1:4100');report={'status':'INCOMPLETE','steps':[]};sid=None

def wait(path,key,states,timeout=180):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  code,r,_=api.call('GET',path)
  if code==200 and r.get(key) in states:return r
  time.sleep(.3)
 raise TimeoutError(path)
def check(name,ok,**values):
 report['steps'].append(dict(name=name,ok=bool(ok),**values));print(name,ok,flush=True)
 if not ok:raise AssertionError(name)
try:
 _,s,_=api.call('GET','/lab/api/scenarios/farm-rows-warehouse');inst=copy.deepcopy(s['instance'])
 inst['customers']=[dict(id=0,node=8,demand=1,label='地头短程验证')];inst['capacity']=1
 c,j,_=api.call('POST','/lab/api/solve',dict(scenario_id=s['id'],instance=inst,algorithms=['urp.turn_mode_dp'],label='仓库完整世界 · 地头 4 m 短程验收（非全部作物行）'))
 check('submit',c==202);sol=wait('/lab/api/runs/'+j['runs'][0],'state',{'done','error','timeout'})
 check('solver_and_footprint',sol['state']=='done' and sol['check']['valid'] and sol['map_check']['collision_free'],solution=sol)
 c,st,_=api.call('POST','/lab/api/sim/session',dict(world='warehouse',spawn=s['sim']['spawn'],gui=False,rviz=False));sid=st.get('session_id')
 check('start',c==202);st=wait('/lab/api/sim/state','phase',{'ready','failed','stopped'})
 check('ready',st['phase']=='ready',state=st);sid=st['session_id'];report['session_id']=sid
 probe=subprocess.run(['bash','-lc','source /opt/ros/humble/setup.bash && exec bash setup/profile.sh simulation /usr/bin/python3 src/lab/e2e/sensor_snapshot.py --out "$1"','ceres-scan',str(out/'warehouse-scan.json')],cwd=ROOT,capture_output=True,text=True,timeout=35)
 (out/'warehouse-scan.log').write_text(probe.stdout+probe.stderr);check('stationary_scan',probe.returncode==0)
 scan=json.loads((out/'warehouse-scan.json').read_text());grid=load('warehouse');dist=[grid.clearance_at(*p) for p in scan['endpoints']]
 audit=dict(samples=len(dist),fraction_within_015m=sum(x<=.15 for x in dist)/len(dist),max_clearance_m=max(dist),mean_clearance_m=sum(dist)/len(dist),scope='One stationary pose near tested headland; not exhaustive map/geometry equivalence')
 (out/'warehouse-map-consistency.json').write_text(json.dumps(audit,indent=2));check('scan_map_alignment',audit['fraction_within_015m']>=.95,audit=audit)
 c,r,_=api.call('POST','/lab/api/sim/runs',dict(run_id=sol['run_id'],mode='follow_path'));check('execute',c==202)
 r=wait('/lab/api/sim/runs/'+r['sim_run_id'],'state',{'succeeded','failed','canceled'},120)
 check('headland_route',r['state']=='succeeded' and r['metrics']['tasks_reached']==1,simulation=r)
 report['status']='PASS'
except Exception as e:
 report.update(status='FAIL',error=f'{type(e).__name__}: {e}');print(report['error'],flush=True)
finally:
 api.call('DELETE','/lab/api/sim/session');st=wait('/lab/api/sim/state','phase',{'stopped'},150)
 path=ROOT/'src/lab/runtime/sim'/str(sid)/'session-report.json';cleanup=json.loads(path.read_text()) if path.exists() else {}
 report['shutdown']=dict(state=st,session_report=str(path),ok=cleanup.get('status')=='PASS' and not st.get('errors'))
 if not report['shutdown']['ok']:report['status']='FAIL'
 (out/'warehouse-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(report['status'],out/'warehouse-smoke.json',flush=True)
raise SystemExit(0 if report['status']=='PASS' else 1)
