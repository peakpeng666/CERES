"""Pure mode routing and bounded-cleanup tests; never launches a native process."""
import ast
import json
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warehouse_acceptance as a
import test_runtime_identity_integration as runtime_tests
from runner.run import simulation_entry

class Clock:
    def __init__(self):self.t=0
    def now(self):return self.t
    def sleep(self,n):self.t+=n

class LifecycleOnlyIntegrationTests(unittest.TestCase):
    def errors(self):return (RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(9))
    def test_explicit_runner_routes_and_unchanged_budgets(self):
        self.assertEqual(simulation_entry('warehouse-lifecycle-only'),('warehouse/warehouse_lifecycle_only_entry.py',3600))
        self.assertEqual(simulation_entry('warehouse'),('warehouse/warehouse_acceptance.py',9000))
        self.assertEqual(simulation_entry('maze'),('acceptance.py',3600))
        with self.assertRaises(ValueError):simulation_entry('unknown')

    def test_all_new_mode_http_calls_have_total_remaining_cap(self):
        clock=Clock();transport=Mock(return_value={})
        request=a.bounded_lifecycle_transport(transport,3,clock.now)
        request('fixed','GET','/health',timeout=5)
        self.assertEqual(transport.call_args.kwargs['timeout'],3)
        clock.t=2.5;request('fixed','POST','/shutdown',{},timeout=10)
        self.assertEqual(transport.call_args.kwargs['timeout'],.5)
        for invalid in (0,-1,float('nan'),float('inf'),True):
            with self.assertRaises(TimeoutError):request('fixed','GET','/sim/state',timeout=invalid)
        clock.t=3
        with self.assertRaises(TimeoutError):request('fixed','GET','/health')
        self.assertEqual(transport.call_count,2)

    def test_deadline_validation_and_runner_handoff(self):
        for value in (None,True,float('nan'),float('inf'),100,99,3701):
            with self.assertRaises(ValueError):a.validate_lifecycle_deadline(value,lambda:100)
        self.assertEqual(a.validate_lifecycle_deadline(3700,lambda:100),3700)
        import runner.run as runner
        tree=ast.parse(Path(runner.__file__).read_text())
        calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='child' and len(node.args)>2 and isinstance(node.args[2],ast.Name) and node.args[2].id=='diagnostic_deadline']
        self.assertEqual(len(calls),1)
        command=eval(compile(ast.Expression(calls[0].args[0]),'<command>','eval'),{'root':Path('/r'),'entry':Path('/e'),'private':Path('/p'),'args':SimpleNamespace(harness_sha='a'*40,stage='warehouse-lifecycle-only'),'diagnostic_deadline':1234.5})
        self.assertEqual(command[-2:],['--diagnostic-deadline','1234.5'])

    def test_entry_is_explicit_not_environment_driven(self):
        text=Path(a.__file__).with_name('warehouse_lifecycle_only_entry.py').read_text()
        self.assertIn('main(lifecycle_only=True)',text)
        source=Path(a.__file__).read_text()
        self.assertIn("if a.stage!=('warehouse-lifecycle-only' if lifecycle_only else 'warehouse')",source)
        self.assertIn("lifecycle=drive(api_call,record['run_id'],plan,guard,save,on_session=observed_session)",source)
        self.assertNotIn('os.environ.get',text)

    def test_gateway_cancellation_retained_after_owned_cleanup(self):
        for error in self.errors():
            with self.subTest(error=type(error)):
                calls=[];clock=Clock();capture=a.IdentityCapture()
                gateway=Mock();gateway.poll.return_value=None;gateway.returncode=0
                api=Mock(side_effect=error)
                owned=Mock(side_effect=[[(1,2)],[],[],[]])
                signals=Mock(side_effect=lambda *args:calls.append(args[1]))
                result=a.cleanup_lifecycle_gateway('fixed',gateway,'owned',api,owned,signals,capture,clock.now,clock.sleep)
                self.assertIs(capture.pending,error)
                self.assertEqual(calls,[signal.SIGTERM])
                self.assertTrue(result['forced'])
                self.assertEqual(result['remaining_owned_process_count'],0)
                self.assertEqual(gateway.wait.call_args_list[0].kwargs,{'timeout':240})
                self.assertEqual(gateway.wait.call_args_list[-1].kwargs,{'timeout':5})

    def test_bounded_term_then_kill_and_unknown_not_zero(self):
        clock=Clock();capture=a.IdentityCapture();gateway=Mock();gateway.poll.return_value=0;gateway.returncode=0
        owned=Mock(return_value=[(1,2)]);signals=Mock()
        result=a.cleanup_lifecycle_gateway('fixed',gateway,'owned',Mock(),owned,signals,capture,clock.now,clock.sleep)
        self.assertEqual(clock.t,16)
        self.assertEqual([x.args[1] for x in signals.call_args_list],[signal.SIGTERM,signal.SIGKILL])
        self.assertEqual(result['remaining_owned_process_count'],1)
        result=a.cleanup_lifecycle_gateway('fixed',gateway,'owned',Mock(),Mock(side_effect=OSError()),signals,capture,clock.now,clock.sleep)
        self.assertIsNone(result['remaining_owned_process_count']);self.assertTrue(result['forced'])

    def test_actual_finalizer_preserves_original_cancel_even_when_summary_write_fails(self):
        for error in self.errors():
            with self.subTest(error=type(error)),tempfile.TemporaryDirectory() as tmp:
                calls=[];capture=a.IdentityCapture();capture.original_error=error
                gateway=Mock();gateway.poll.return_value=None;gateway.returncode=0
                ns=dict(lifecycle_only=True,process_observer=None,observer=None,is_cancellation=a.is_cancellation,
                        identity_capture=capture,raw={'stage':'warehouse-lifecycle-only','state':'ERROR','cleanup_pass':False},
                        base='fixed',gateway=gateway,api=Mock(),diagnostic={'harness':{}},
                        describe_failure=lambda e:{'exception_kind':'unknown'},owned=lambda t:[],signal_owned=Mock(),
                        token='owned',forced=False,subprocess=subprocess,signal=signal,
                        collect=lambda *args:{},runtime=Path(tmp),sid='sim_fixed',project=lambda raw,**kw:raw,
                        cleanup_lifecycle_gateway=a.cleanup_lifecycle_gateway,
                        a=SimpleNamespace(summary=Path(tmp)),json=json)
                finalize=runtime_tests.RuntimeIdentityIntegrationTests().finalizer(ns)
                try:finalize()
                except BaseException as got:self.assertIs(got,error)
                else:self.fail('cancel lost')
                ns['api'].assert_called_once();self.assertEqual(gateway.wait.call_count,2)

    def test_actual_finalizer_new_mode_success_and_cleanup_cancellation(self):
        for error in (None,*self.errors()):
            with self.subTest(error=type(error)),tempfile.TemporaryDirectory() as tmp:
                capture=a.IdentityCapture();gateway=Mock();gateway.poll.return_value=None;gateway.returncode=0
                api=Mock(side_effect=error) if error else Mock()
                raw={'stage':'warehouse-lifecycle-only','state':'PASS','cleanup_pass':True,'task_not_submitted':True}
                ns=dict(lifecycle_only=True,process_observer=None,observer=None,is_cancellation=a.is_cancellation,
                        identity_capture=capture,raw=raw,base='fixed',gateway=gateway,api=api,diagnostic={'harness':{}},
                        describe_failure=lambda e:{'exception_kind':'unknown'},owned=lambda t:[],signal_owned=Mock(),
                        token='owned',forced=False,subprocess=subprocess,signal=signal,
                        collect=lambda *args:{},runtime=Path(tmp),sid='sim_fixed',project=lambda raw,**kw:dict(raw),
                        cleanup_lifecycle_gateway=a.cleanup_lifecycle_gateway,
                        a=SimpleNamespace(summary=Path(tmp)/'summary.json'),json=json)
                finalize=runtime_tests.RuntimeIdentityIntegrationTests().finalizer(ns)
                if error is not None:
                    try:finalize()
                    except BaseException as got:self.assertIs(got,error)
                    else:self.fail('cancel lost')
                else:finalize()
                result=json.loads(ns['a'].summary.read_text())
                self.assertTrue(result['task_not_submitted'])
                self.assertEqual(result['remaining_owned_process_count'],0)
                self.assertEqual(result['cleanup_pass'],error is None)
                self.assertEqual(gateway.wait.call_count,2)

    def test_actual_finalizer_first_observer_cancellation_wins(self):
        for site in ('process','observer','both'):
            with self.subTest(site=site),tempfile.TemporaryDirectory() as tmp:
                first=KeyboardInterrupt('first');second=SystemExit(9);capture=a.IdentityCapture()
                gateway=Mock();gateway.poll.return_value=None;gateway.returncode=0
                process=Mock() if site in ('process','both') else None
                observer=Mock() if site in ('observer','both') else None
                if process is not None:process.stop.side_effect=first
                if observer is not None:
                    observer.safe.side_effect=first if site=='observer' else second;observer.raw={}
                ns=dict(lifecycle_only=True,process_observer=process,observer=observer,is_cancellation=a.is_cancellation,
                        identity_capture=capture,raw={'stage':'warehouse-lifecycle-only','state':'PASS','cleanup_pass':True},
                        base='fixed',gateway=gateway,api=Mock(side_effect=second),diagnostic={'harness':{}},
                        describe_failure=lambda e:{'exception_kind':'unknown'},owned=lambda t:[],signal_owned=Mock(),
                        token='owned',forced=False,subprocess=subprocess,signal=signal,
                        collect=lambda *args:{},runtime=Path(tmp),sid='sim_fixed',project=lambda raw,**kw:dict(raw),
                        cleanup_lifecycle_gateway=a.cleanup_lifecycle_gateway,
                        a=SimpleNamespace(summary=Path(tmp)/'summary.json'),json=json)
                finalize=runtime_tests.RuntimeIdentityIntegrationTests().finalizer(ns)
                try:finalize()
                except BaseException as got:self.assertIs(got,first)
                else:self.fail('cancel lost')
                self.assertEqual(gateway.wait.call_count,2)

    def test_new_main_propagates_exact_cancellation_old_main_unchanged(self):
        for error in self.errors():
            for new in (False,True):
                argv=['entry','--source','/fixture','--private-dir','/fixture/private','--summary','/fixture/summary','--harness-sha','a'*40]
                if new:argv+=['--diagnostic-deadline','3600']
                with self.subTest(error=type(error),new=new),patch('sys.argv',argv),patch.object(a,'execute',side_effect=error),patch.object(a.signal,'signal'):
                    if new:
                        try:a.main(lifecycle_only=True)
                        except BaseException as got:self.assertIs(got,error)
                        else:self.fail('cancel lost')
                    else:self.assertEqual(a.main(),1)

if __name__=='__main__':unittest.main()
