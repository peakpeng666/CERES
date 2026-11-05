import ast
import copy
import json
import signal
import subprocess
import time
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import process_observer as p


def row(pid,ppid,start=100,ticks=10,pgid=None):
    return dict(pid=pid,ppid=ppid,pgid=pid if pgid is None else pgid,start=start,ticks=ticks,rss=10,threads=2,state='S')


class Reader:
    def __init__(self):
        self.rows={10:row(10,1),11:row(11,10),12:row(12,11),99:row(99,1)}
        self.exes={11:('/usr/bin/python3.10',(1,2)),12:('/opt/ros/humble/lib/ros_gz_bridge/parameter_bridge',(1,3))}
        self.hook=None;self.deny=set()
    def stat(self,pid):
        if pid in self.deny:raise PermissionError()
        if pid not in self.rows:raise ProcessLookupError()
        return dict(self.rows[pid])
    def pids(self):return sorted(self.rows)
    def exe(self,pid):
        if self.hook:self.hook(pid)
        if pid in self.deny:raise PermissionError()
        return self.exes[pid]


class Tests(unittest.TestCase):
    def setUp(self):
        self.reader=Reader();self.now=100.;self.gateway=SimpleNamespace(pid=10,returncode=None)
        self.obs=p.ProcessObserver(self.gateway,reader=self.reader,clock=lambda:self.now,parent_pid=1,ticks=100,page=4096)
        self.obs.bind('sim_fixture')
    def observe(self,offset,phase='starting',sid='sim_fixture'):
        self.now=100+offset
        self.obs.safe('observe',dict(session_id=sid,world='warehouse',phase=phase))
    def advance(self):
        self.reader.rows[11]['ticks']+=100
        self.reader.rows[12]['ticks']+=200
    def test_intervals_privacy_and_public_size(self):
        self.observe(30);self.advance();self.observe(31);self.observe(39)
        out=self.obs.result();self.assertEqual(out['status'],'complete')
        self.assertEqual(out['rows'][0]['cpu_delta_s'],2)
        self.assertEqual(out['rows'][0]['covered_wall_s'],1)
        text=json.dumps(out)
        for secret in ('/opt','/usr','ppid','start_ticks','environ','cmdline','utilization'):
            self.assertNotIn(secret,text)
        self.assertLess(len(text),16000)
    def test_pid_reuse_does_not_subtract(self):
        self.observe(30);self.reader.rows[12]['start']=200;self.reader.rows[12]['ticks']=500
        self.observe(31)
        self.assertEqual(len(self.obs.result()['rows']),3)
        self.assertTrue(all(r['cpu_delta_s']==0 for r in self.obs.result()['rows']))
    def test_exec_breaks_interval_and_label(self):
        self.observe(30);self.advance();self.reader.exes[12]=('/private/SECRET',(1,9));self.observe(31)
        vals=self.obs.result()['rows'];changed=next(r for r in vals if r['lifetime_id']==2)
        self.assertEqual(changed['cpu_delta_s'],0);self.assertEqual(changed['category'],'unavailable')
        self.assertNotIn('SECRET',json.dumps(vals));self.assertEqual(self.obs.raw['exec_changes'],1)
    def test_exit_records_lost_not_negative_delta(self):
        self.observe(30);del self.reader.rows[12];self.observe(31)
        self.assertEqual(self.obs.raw['lost_identities'],1)
        self.assertTrue(all(r['cpu_delta_s']==0 for r in self.obs.result()['rows']))
    def test_reparent_outside_excluded(self):
        self.observe(30);self.reader.rows[12]['ppid']=99;self.advance();self.observe(31)
        self.assertEqual(self.obs.raw['lost_identities'],1)
        self.assertEqual(next(r for r in self.obs.result()['rows'] if r['lifetime_id']==2)['intervals'],0)
    def test_reparent_inside_breaks_interval(self):
        self.observe(30);self.reader.rows[12]['ppid']=10;self.advance();self.observe(31)
        self.assertEqual(self.obs.raw['ancestry_omissions'],1)
    def test_reparent_during_exe_rejected(self):
        self.reader.hook=lambda pid:self.reader.rows[pid].update(ppid=99)
        self.observe(30);self.assertEqual(self.obs.raw['ancestry_omissions'],2)
        self.assertEqual(self.obs.result()['rows'],[])
    def test_exec_race_rejected(self):
        def hook(pid):
            old=self.reader.exes[pid];self.reader.exes[pid]=(old[0],(old[1][0],old[1][1]+1))
        self.reader.hook=hook;self.observe(30)
        self.assertEqual(self.obs.result()['rows'],[])
    def test_root_reuse_and_owner_validation(self):
        self.reader.rows[10]['start']=101;self.observe(30)
        self.assertEqual(self.obs.raw['end_reason'],'identity_mismatch')
        bad=p.ProcessObserver(self.gateway,reader=self.reader,parent_pid=999,ticks=100,page=4096)
        self.assertTrue(bad.closed)
    def test_permission_missing(self):
        self.reader.deny.add(12);self.observe(30);self.observe(31);self.observe(39)
        self.assertEqual(self.obs.raw['status'],'partial');self.assertEqual(len(self.obs.result()['rows']),1)
    def test_monotonic_backwards_nan(self):
        self.observe(30);self.observe(29)
        self.assertEqual(self.obs.raw['end_reason'],'clock_invalid')
        self.setUp();self.now=float('nan');self.obs.safe('observe',dict(session_id='sim_fixture',world='warehouse',phase='starting'))
        self.assertEqual(self.obs.raw['end_reason'],'clock_invalid')
    def test_cleanup_and_phase_cutoff_before_sampling(self):
        self.observe(30)
        calls=[]
        api=self.obs.wrap(lambda *a:calls.append(a) or {})
        api('DELETE','/sim/session');self.advance();self.observe(31)
        self.assertEqual(self.obs.raw['snapshots'],1);self.assertEqual(len(calls),1)
        self.setUp();self.observe(30,'stopping');self.assertEqual(self.obs.raw['snapshots'],0)
    def test_bounded_window_and_rate(self):
        self.observe(29);self.observe(30);self.observe(30.1);self.observe(38.1)
        self.assertEqual(self.obs.raw['snapshots'],1);self.assertTrue(self.obs.closed)
    def test_caps(self):
        with patch.object(p,'MAX_OWNED',1),patch.object(p,'MAX_LIFETIMES',1):
            self.observe(30);self.assertEqual(self.obs.raw['owned_overflows'],1)
            del self.reader.rows[11];self.reader.rows[12]['ppid']=10
            self.observe(31);self.assertEqual(self.obs.raw['lifetime_omissions'],1)
        self.setUp();self.reader.pids=lambda:(_ for _ in ()).throw(OverflowError())
        self.observe(30);self.assertEqual(self.obs.raw['end_reason'],'scan_limit')
    def test_negative_counter_no_delta(self):
        self.observe(30);self.reader.rows[12]['ticks']=1;self.observe(31)
        self.assertEqual(self.obs.raw['counter_regressions'],1)
    def test_sanitize_adversarial(self):
        for raw in (None,[],{'status':[],'end_reason':{},'rows':[{'category':[]}]}):
            p.sanitize(raw)
        self.observe(30);raw=copy.deepcopy(self.obs.raw)
        raw['SECRET']='private';raw['rows'][0]['category']='private/path';raw['rows'][1]['cpu_delta_s']=float('nan')
        self.assertEqual(p.sanitize(raw)['rows'],[])
        self.assertNotIn('SECRET',json.dumps(p.sanitize(raw)))
    def test_category_honesty(self):
        self.assertEqual(p.category('/bin/ruby3.0'),'ruby_runtime')
        self.assertEqual(p.category('/bin/python3.10'),'python_runtime')
        self.assertEqual(p.category('/private/ign'),'other_owned')
    def test_proc_parser_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'123';path.mkdir();f=path/'stat';reader=p.ProcReader(d)
            for text in ('123 (bad) S 1','123 (bad) '+('0 '*22),'x'*4097):
                f.write_text(text)
                with self.assertRaises(ValueError):reader.stat(123)
    def test_projection_cap_and_idempotence(self):
        self.observe(30)
        raw=copy.deepcopy(self.obs.raw)
        template=raw['rows'][0]
        raw['rows']=[dict(template,lifetime_id=i+1,cpu_delta_s=float(i)) for i in range(128)]
        out=p.sanitize(raw)
        self.assertEqual(len(out['rows']),32)
        self.assertEqual(out['public_rows_omitted'],96)
        self.assertEqual(p.sanitize(out),out)
        self.assertLess(len(json.dumps(out)),16000)
    def test_snapshot_crossing_window_discarded(self):
        self.reader.hook=lambda pid:setattr(self,'now',139.)
        self.observe(37.9)
        self.assertEqual(self.obs.raw['snapshots'],0)
        self.assertEqual(self.obs.raw['rows'],[])
        self.assertTrue(self.obs.closed)
    def test_snapshot_clock_regression_discarded(self):
        self.reader.hook=lambda pid:setattr(self,'now',129.)
        self.observe(30)
        self.assertEqual(self.obs.raw['end_reason'],'clock_invalid')
        self.assertEqual(self.obs.raw['rows'],[])
    def test_api_exception_preserved(self):
        failure=RuntimeError('fixture')
        def api(*args):raise failure
        with self.assertRaises(RuntimeError) as caught:self.obs.wrap(api)('GET','/sim/state')
        self.assertIs(caught.exception,failure)
        self.assertFalse(self.obs.closed)
        self.assertEqual(self.obs.raw['api_read_errors'],1)
    def test_root_reaped_before_sample(self):
        self.gateway.returncode=0;self.observe(30)
        self.assertEqual(self.obs.raw['end_reason'],'identity_mismatch')
        self.assertEqual(self.obs.raw['snapshots'],0)
    def test_stat_comm_parentheses_and_negative_counter(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'123';path.mkdir();f=path/'stat';reader=p.ProcReader(d)
            fields=['S']+['0']*21
            fields[1]='1';fields[2]='123';fields[11]='5';fields[12]='2';fields[17]='1';fields[19]='100';fields[21]='3'
            f.write_text('123 (a ) name) '+' '.join(fields))
            self.assertEqual(reader.stat(123)['ticks'],7)
            fields[12]='-1';f.write_text('123 (a) '+' '.join(fields))
            with self.assertRaises(ValueError):reader.stat(123)

    def test_ancestor_reparent_on_second_exe_read_is_rejected(self):
        calls={}
        def hook(pid):
            calls[pid]=calls.get(pid,0)+1
            if pid==12 and calls[pid]==2:self.reader.rows[11]['ppid']=99
        self.reader.hook=hook;self.observe(30)
        self.assertEqual(self.obs.raw['ancestry_omissions'],1)
        self.assertEqual(len(self.obs.result()['rows']),1)
        self.assertEqual(self.obs.result()['rows'][0]['category'],'python_runtime')

    def test_depth_cap_is_an_explicit_omission(self):
        for pid in range(20,86):
            self.reader.rows[pid]=row(pid,10 if pid==20 else pid-1)
            self.reader.exes[pid]=('/bin/python3',(1,pid))
        self.observe(30);self.observe(39)
        self.assertGreater(self.obs.raw['ancestry_omissions'],0)
        self.assertEqual(self.obs.raw['status'],'partial')
    def test_adversarial_omission_projection_is_idempotent(self):
        self.observe(30);raw=copy.deepcopy(self.obs.raw);template=raw['rows'][0]
        raw['rows']=[dict(template,lifetime_id=i+1) for i in range(128)]
        raw['public_rows_omitted']=128
        out=p.sanitize(raw)
        self.assertEqual(p.sanitize(out),out)
    def test_finalizer_diagnostic_failure_preserves_application_cleanup(self):
        path=Path(__file__).with_name('warehouse_acceptance.py')
        execute=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='execute')
        original=next(n for n in execute.body if isinstance(n,ast.Try)).finalbody
        # Exercise exactly the diagnostic prelude, before the existing observer cleanup.
        # New lifecycle-only cancellation precedence wraps the shared finalizer.
        if len(original)==1 and isinstance(original[0],ast.Try):original=original[0].body
        end=next(i for i,n in enumerate(original) if isinstance(n,ast.If) and ast.unparse(n.test)=='observer is not None')
        module=ast.Module(body=original[:end],type_ignores=[]);ast.fix_missing_locations(module)
        for failure in (ValueError('fixture'),KeyboardInterrupt()):
            def fail():raise failure
            ns={'lifecycle_only':False,'process_observer':SimpleNamespace(stop=lambda reason:None,result=fail),
                'raw':{'state':'PASS'},'is_cancellation':p.is_cancellation}
            exec(compile(module,'finalizer-diagnostic','exec'),ns)
            self.assertEqual(ns['raw']['warehouse_process_observation']['status'],'unavailable')
            self.assertEqual(ns['raw']['state'],'ERROR' if isinstance(failure,KeyboardInterrupt) else 'PASS')
            self.assertIs(ns['observer_cancellation'],failure if isinstance(failure,KeyboardInterrupt) else None)
    def test_constructor_error_is_nonfatal_in_actual_integration(self):
        tree=ast.parse(Path(__file__).with_name('warehouse_acceptance.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.Try) and any(isinstance(x,ast.Assign) and ast.unparse(x).startswith('process_observer = ProcessObserver') for x in n.body))
        module=ast.Module(body=[node],type_ignores=[]);ast.fix_missing_locations(module)
        def fail(gateway):raise OSError('fixture')
        ns={'ProcessObserver':fail,'gateway':self.gateway,'raw':{},'process_observer':None,'is_cancellation':p.is_cancellation}
        exec(compile(module,'constructor-diagnostic','exec'),ns)
        self.assertIsNone(ns['process_observer'])
        self.assertEqual(ns['raw']['warehouse_process_observation']['status'],'unavailable')

    def test_api_observation_error_preserves_exact_api_result(self):
        result=dict(session_id='sim_fixture',world='warehouse',phase='starting')
        self.now=130
        with patch.object(self.obs,'snapshot',side_effect=ValueError('fixture')):
            self.assertIs(self.obs.wrap(lambda *args:result)('GET','/sim/state'),result)
        self.assertEqual(self.obs.raw['end_reason'],'read_error')
        self.setUp();self.now=130
        with patch.object(self.obs,'snapshot',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):self.obs.wrap(lambda *args:result)('GET','/sim/state')
    def test_optional_summary_projection_error_preserves_acceptance(self):
        import warehouse_public_summary as public
        baseline=public.project({},require_runner=False)
        with patch.object(public,'sanitize_process_observation',side_effect=ValueError('fixture')):
            result=public.project({},require_runner=False)
        self.assertEqual(result['warehouse_process_observation']['status'],'unavailable')
        baseline.pop('warehouse_process_observation');result.pop('warehouse_process_observation')
        self.assertEqual(result,baseline)
        with patch.object(public,'sanitize_process_observation',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):public.project({},require_runner=False)

    def test_safe_preserves_project_cancellation_exact_object(self):
        for failure in (RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7)):
            for operation in ('observe','bind'):
                self.setUp()
                def fail():raise failure
                self.obs.clock=fail
                if operation=='bind':self.obs.sid=None
                arg='sim_fixture' if operation=='bind' else dict(session_id='sim_fixture',world='warehouse',phase='starting')
                with self.assertRaises(type(failure)) as caught:self.obs.safe(operation,arg)
                self.assertIs(caught.exception,failure)
                self.assertTrue(self.obs.closed)
        self.setUp();self.obs.clock=lambda:(_ for _ in ()).throw(RuntimeError('ordinary'))
        self.assertIsNone(self.obs.safe('observe',dict(session_id='sim_fixture',world='warehouse',phase='starting')))
    def test_constructor_cancellation_exact_object(self):
        tree=ast.parse(Path(__file__).with_name('warehouse_acceptance.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.Try) and any(isinstance(x,ast.Assign) and ast.unparse(x).startswith('process_observer = ProcessObserver') for x in n.body))
        module=ast.Module(body=[node],type_ignores=[]);ast.fix_missing_locations(module)
        for failure in (RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7),ValueError('ordinary')):
            def fail(gateway):raise failure
            ns={'ProcessObserver':fail,'gateway':self.gateway,'raw':{},'process_observer':None,'is_cancellation':p.is_cancellation}
            if p.is_cancellation(failure):
                with self.assertRaises(type(failure)) as caught:exec(compile(module,'constructor','exec'),ns)
                self.assertIs(caught.exception,failure)
            else:
                exec(compile(module,'constructor','exec'),ns)
                self.assertEqual(ns['raw']['warehouse_process_observation']['status'],'unavailable')
    def test_projection_cancellation_exact_object(self):
        import warehouse_public_summary as public
        for failure in (RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7),ValueError('ordinary')):
            with patch.object(public,'sanitize_process_observation',side_effect=failure):
                if p.is_cancellation(failure):
                    with self.assertRaises(type(failure)) as caught:public.project({},require_runner=False)
                    self.assertIs(caught.exception,failure)
                else:self.assertEqual(public.project({},require_runner=False)['warehouse_process_observation']['status'],'unavailable')
    def test_delete_attempted_once_despite_diagnostic_cancellation(self):
        for failure in (RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7),ValueError('ordinary')):
            self.setUp();calls=[];result={}
            def stop(reason):raise failure
            with patch.object(self.obs,'stop',side_effect=stop):
                wrapped=self.obs.wrap(lambda *args:calls.append(args) or result)
                if p.is_cancellation(failure):
                    with self.assertRaises(type(failure)) as caught:wrapped('DELETE','/sim/session')
                    self.assertIs(caught.exception,failure)
                else:self.assertIs(wrapped('DELETE','/sim/session'),result)
            self.assertEqual(len(calls),1)
    def test_original_api_failure_beats_diagnostic_stop_failure(self):
        for original in (ValueError('api'),RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7)):
            for diagnostic in (ValueError('diagnostic'),RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(8)):
                self.setUp();calls=[]
                def api(*args):calls.append(args);raise original
                with patch.object(self.obs,'stop',side_effect=diagnostic):
                    with self.assertRaises(type(original)) as caught:self.obs.wrap(api)('DELETE','/sim/session')
                self.assertIs(caught.exception,original);self.assertEqual(len(calls),1)
    def test_recoverable_poll_error_can_resume_without_bridging_interval(self):
        self.observe(30);failure=OSError('temporary')
        def fail(*args):raise failure
        with self.assertRaises(OSError) as caught:self.obs.wrap(fail)('GET','/sim/state')
        self.assertIs(caught.exception,failure);self.assertFalse(self.obs.closed)
        self.advance();self.observe(32)
        self.assertTrue(all(r['intervals']==0 for r in self.obs.result()['rows']))
        self.advance();self.observe(33);self.observe(39)
        self.assertTrue(all(r['intervals']==1 for r in self.obs.result()['rows']))
        self.assertEqual(self.obs.raw['api_read_errors'],1);self.assertEqual(self.obs.raw['status'],'partial')
    def test_get_api_cancellation_stops_and_propagates_exact_object(self):
        for failure in (RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7)):
            self.setUp()
            def fail(*args):raise failure
            with self.assertRaises(type(failure)) as caught:self.obs.wrap(fail)('GET','/sim/state')
            self.assertIs(caught.exception,failure);self.assertTrue(self.obs.closed)
    def test_full_finalizer_runs_cleanup_then_rethrows_exact_cancellation(self):
        tree=ast.parse(Path(__file__).with_name('warehouse_acceptance.py').read_text())
        execute=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='execute')
        body=[ast.Global(names=['forced','diagnostic'])]+next(n for n in execute.body if isinstance(n,ast.Try)).finalbody
        module=ast.Module(body=[ast.FunctionDef(name='finalize',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=body,decorator_list=[])],type_ignores=[]);ast.fix_missing_locations(module)
        for failure in (RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7),ValueError('ordinary')):
            for failure_site in ('stop','result'):
                calls=[]
                def fail(*args):raise failure
                class Gateway:
                    returncode=None
                    def poll(self):return None
                    def wait(self,timeout):calls.append(('wait',timeout));self.returncode=0
                with tempfile.TemporaryDirectory() as d:
                    diag=SimpleNamespace(stop=fail if failure_site=='stop' else lambda reason:None,result=fail if failure_site=='result' else lambda:{})
                    ns={'lifecycle_only':False,'process_observer':diag,'observer':None,'is_cancellation':p.is_cancellation,
                        'raw':{'state':'PASS','cleanup_pass':True},'base':'http://127.0.0.1:1','gateway':Gateway(),
                        'api':lambda *args,**kw:calls.append(('api',args)),
                        'diagnostic':{'harness':{},'lifecycle':{'failure_code':'none'}},'describe_failure':lambda exc:{'exception_kind':'interrupted'},
                        'owned':lambda token:[],'token':'fixture','forced':False,'subprocess':subprocess,'signal':signal,'time':time,
                        'collect':lambda *args:{},'runtime':Path(d),'sid':'sim_fixture','project':lambda raw,**kw:dict(raw),
                        'a':SimpleNamespace(summary=Path(d)/'summary.json'),'json':json}
                    ns['identity_capture']=SimpleNamespace(finish=lambda:{},defer_cancellation=lambda exc:None,apply_pending=lambda result:None,raise_pending=lambda:None)
                    exec(compile(module,'production-finalizer','exec'),ns)
                    if p.is_cancellation(failure):
                        with self.assertRaises(type(failure)) as caught:ns['finalize']()
                        self.assertIs(caught.exception,failure)
                    else:
                        ns['finalize']();self.assertEqual(ns['raw']['state'],'PASS')
                    self.assertEqual(len([c for c in calls if c[0]=='api']),1)
                    self.assertIn(('wait',240),calls);self.assertIn(('wait',5),calls)

    def test_exact_launch_catalogue_and_unknown_negative_cases(self):
        from resource_uri_diagnostics import LAUNCH_GRAPH_PROCESSES
        for name in LAUNCH_GRAPH_PROCESSES:
            self.assertEqual(p.category('/opt/ros/humble/lib/example/'+name),name)
            self.assertEqual(p.category('/private/'+name+'-extra'),'other_owned')
        for name in ('motion_control','sensors_node','wheel_status_publisher'):
            self.assertIn(p.category('/opt/ros/humble/lib/example/'+name),p.CATEGORIES)
        for name in ('ruby $(which ign) gazebo','ign gazebo','gz sim','unlisted_secret','python3.py','motion_control.py'):
            self.assertEqual(p.category('/private/'+name),'other_owned')
        self.assertEqual(p.category('/usr/bin/python3.10'),'python_runtime')
        self.assertEqual(p.category('/usr/bin/ruby3.0'),'ruby_runtime')

    def test_integration_actual_popen_is_passed(self):
        text=Path(__file__).with_name('warehouse_acceptance.py').read_text()
        self.assertIn('process_observer=ProcessObserver(gateway)',text)
        self.assertLess(text.index('gateway=subprocess.Popen'),text.index('process_observer=ProcessObserver(gateway)'))
        self.assertIn("process_observer.safe('bind',value)",text)
        self.assertIn("process_observer.stop('cleanup_started')",text)
        self.assertIn("raw['warehouse_process_observation']=process_observer.result()",text)

if __name__=='__main__':unittest.main()
