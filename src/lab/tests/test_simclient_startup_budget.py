"""Exercise actual submit/dispatch methods with fake time, network and worker threads."""
import ast
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from urp_lab.execution_timing import budget_metadata, freeze_plan_budget

SOURCE=Path(__file__).parents[1]/'urp_lab/simclient.py'

class StartupDispatch(unittest.TestCase):
    def client(self,alive=True,ready_at=1100,cancel_at=None,failed_at=None):
        tree=ast.parse(SOURCE.read_text());clock=NS(wall=0.)
        workers=[];posts=[];rows={}
        class Worker:
            def __init__(self,target,args,**kwargs):self.target=target;self.args=args;workers.append(self)
            def start(self):pass # Capture asynchronous work without real wall time.
        class ServiceError(Exception):pass
        methods=[n for n in next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='SimClient').body if isinstance(n,ast.FunctionDef) and n.name in ('submit','_dispatch','_finish_local')]
        # Only replace the planner import with a deterministic prevalidated plan.
        for method in methods:
            method.body=[n for n in method.body if not isinstance(n,ast.ImportFrom)]
        defs=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('READY_TIMEOUT_S','SIM_FINAL') for t in n.targets)]
        defs+=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='new_sim_run']
        defs+=[ast.ClassDef(name='SimClient',bases=[],keywords=[],body=methods,decorator_list=[])]
        ns={'budget_metadata':budget_metadata, 'freeze_plan_budget':freeze_plan_budget, 'threading':NS(Event=threading.Event,Thread=Worker),'ServiceError':ServiceError,
            'NotSimulatable':ValueError,'to_sim_plan':lambda *args:{'path':[[0,0],[1,1]],'tasks':[], 'budget_s':103},
            'new_id':lambda _: 'run1','utc_now':lambda:'fake-time',
            'http_json':lambda *args,**kwargs:(posts.append((args,kwargs)) or (202,{}))}
        ns['time']=NS(monotonic=lambda:clock.wall)
        exec(compile(ast.fix_missing_locations(ast.Module(body=defs,type_ignores=[])),str(SOURCE),'exec'),ns)
        c=ns['SimClient']();c._lock=threading.Lock();c._waiting={};c._persisted={};c.bus=None;c.url='http://fake'
        c.state={'phase':'starting','session_id':'session1','world':'warehouse'}
        c.store=NS(active_sim_runs=lambda:[],put_sim_run=lambda r:rows.__setitem__(r['sim_run_id'],dict(r)),get_sim_run=lambda rid:rows.get(rid))
        svc=NS(live=alive,sim_meta={'session_id':'session1','world':'warehouse'},sim_adopted=None,_busy={},sim_exit=None,starts=[])
        svc.sim_alive=lambda:svc.live
        def start_sim(body):svc.starts.append(body);svc.live=True
        svc.start_sim=start_sim;c.services=svc
        def sleep(seconds):
            clock.wall+=seconds
            if ready_at is not None and clock.wall>=ready_at:c.state['phase']='ready'
            if failed_at is not None and clock.wall>=failed_at:c.state.update(phase='failed',errors=['native failure'])
            if cancel_at is not None and clock.wall>=cancel_at:
                for flag in c._waiting.values():flag.set()
        ns['time'].sleep=sleep
        return c,clock,workers,posts,rows,ns['READY_TIMEOUT_S']
    def submit(self,c,autostart=False):
        return c.submit({'run_id':'solution1','frame':{'kind':'map'},'state':'done','routes':[[0,1]]},
                        {'sim':{'supported':True,'world':'warehouse'}},'follow_path',autostart=autostart)
    def test_queued_existing_startup_can_dispatch_after_old_900_limit(self):
        c,t,workers,posts,rows,cap=self.client();run=self.submit(c)
        self.assertEqual(t.wall,0);self.assertEqual(run['state'],'queued');self.assertEqual(cap,1200.)
        self.assertEqual(len(workers),1);self.assertEqual(posts,[])
        workers[0].target(*workers[0].args)
        self.assertEqual(t.wall,1100);self.assertEqual(len(posts),1);self.assertEqual(c.services.starts,[])
        self.assertEqual(posts[0][0][0],'POST');self.assertEqual(posts[0][1]['timeout'],15)
        self.assertEqual(c._waiting,{})
    def test_autostart_returns_queued_then_dispatches_late_ready(self):
        c,t,workers,posts,rows,cap=self.client(alive=False);run=self.submit(c,autostart=True)
        self.assertEqual(len(c.services.starts),1);self.assertEqual(run['state'],'queued');self.assertEqual(t.wall,0)
        workers[0].target(*workers[0].args)
        self.assertEqual(len(posts),1);self.assertEqual(t.wall,1100)
    def test_never_ready_has_finite_1200_timeout_without_post(self):
        c,t,workers,posts,rows,cap=self.client(ready_at=None);self.submit(c)
        workers[0].target(*workers[0].args)
        self.assertEqual(rows['run1']['state'],'failed');self.assertIn('1200',rows['run1']['error'])
        self.assertLessEqual(t.wall,1200.25);self.assertEqual(posts,[]);self.assertEqual(c._waiting,{})
    def test_cancel_while_queued_prevents_dispatch(self):
        c,t,workers,posts,rows,cap=self.client(cancel_at=5);self.submit(c)
        workers[0].target(*workers[0].args)
        self.assertEqual(rows['run1']['state'],'canceled');self.assertEqual(posts,[]);self.assertEqual(t.wall,5)
    def test_startup_failure_prevents_dispatch(self):
        c,t,workers,posts,rows,cap=self.client(failed_at=5);self.submit(c)
        workers[0].target(*workers[0].args)
        self.assertEqual(rows['run1']['state'],'failed');self.assertIn('native failure',rows['run1']['error'])
        self.assertEqual(posts,[]);self.assertEqual(t.wall,5)
    def test_no_implicit_autostart(self):
        c,t,workers,posts,rows,cap=self.client(alive=False)
        with self.assertRaises(Exception):self.submit(c)
        self.assertEqual(workers,[]);self.assertEqual(c.services.starts,[])

if __name__=='__main__':unittest.main()
