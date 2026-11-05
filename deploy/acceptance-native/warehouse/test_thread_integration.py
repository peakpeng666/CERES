"""Pure fault-injection coverage for the optional thread integration."""
import json
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import process_observer as p
from test_process_observer import Reader


class ThreadIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now=100.;self.reader=Reader();self.gateway=SimpleNamespace(pid=10,returncode=None)
        self.obs=self.make()
        self.obs.bind('sim_fixture')
    def make(self):
        return p.ProcessObserver(self.gateway,reader=self.reader,clock=lambda:self.now,parent_pid=1,ticks=100,page=4096)
    def state(self):return dict(session_id='sim_fixture',world='warehouse',phase='starting')
    def observe(self,offset):
        self.now=100+offset;self.obs.safe('observe',self.state())
    def failures(self):return [ValueError('ordinary'),RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(3)]
    def exact(self,error,call):
        try:call()
        except BaseException as got:self.assertIs(got,error)
        else:self.fail('expected exact exception object')
    def test_constructor_failure_contract(self):
        for error in self.failures():
            with self.subTest(error=type(error)),patch.object(p.thread_diag,'ThreadObserver',side_effect=error):
                obs=self.make()
                if p.is_cancellation(error):self.exact(error,lambda:obs.bind('sim_fixture'))
                else:
                    obs.bind('sim_fixture');self.assertFalse(obs.closed)
                    self.assertEqual(obs.result()['thread_observation']['status'],'unavailable')
    def test_observe_fault_preserves_api_result_or_exact_cancellation(self):
        for error in self.failures():
            with self.subTest(error=type(error)):
                obs=self.make();obs.bind('sim_fixture');obs.thread=Mock();obs.thread.observe.side_effect=error
                result=self.state();api=Mock(return_value=result);self.now=130.
                if p.is_cancellation(error):self.exact(error,lambda:obs.wrap(api)('GET','/sim/state'))
                else:
                    self.assertIs(obs.wrap(api)('GET','/sim/state'),result)
                    self.assertFalse(obs.closed);self.assertEqual(obs.raw['snapshots'],1)
                api.assert_called_once();self.now=100.
    def test_result_and_projection_failures(self):
        self.observe(30)
        for name in ('result','projection'):
            for error in self.failures():
                with self.subTest(name=name,error=type(error)):
                    if name=='result':
                        self.obs.thread=Mock();self.obs.thread.result.side_effect=error
                        action=self.obs.result;context=patch.object(p.thread_diag,'unused',None,create=True)
                    else:
                        self.obs.thread=None;action=self.obs.result
                        context=patch.object(p.thread_diag,'sanitize',side_effect=error)
                    with context:
                        if p.is_cancellation(error):self.exact(error,action)
                        else:
                            out=action();self.assertEqual(out['snapshots'],1)
                            self.assertEqual(len(out['rows']),2)
                            self.assertEqual(out['thread_observation']['status'],'unavailable')
    def test_cleanup_stop_fault_always_attempts_original_once(self):
        for error in self.failures():
            for original in (None,OSError('original_api')):
                with self.subTest(error=type(error),original=original):
                    obs=self.make();obs.bind('sim_fixture');obs.thread=Mock();obs.thread.stop.side_effect=error
                    result={'cleanup':'unchanged'};api=Mock(return_value=result,side_effect=original)
                    call=lambda:obs.wrap(api)('DELETE','/sim/session')
                    if original is not None:self.exact(original,call)
                    elif p.is_cancellation(error):self.exact(error,call)
                    else:self.assertIs(call(),result)
                    api.assert_called_once()
    def test_api_error_precedence_and_recoverable_continuity_break(self):
        for error in self.failures():
            obs=self.make();obs.bind('sim_fixture');obs.thread=Mock();obs.thread.break_intervals.side_effect=error
            original=OSError('poll_failure');api=Mock(side_effect=original)
            self.exact(original,lambda:obs.wrap(api)('GET','/sim/state'))
            self.assertFalse(obs.closed);self.assertEqual(obs.previous,{})
            self.assertEqual(obs.raw['api_read_errors'],1)
    def test_candidates_only_matched_current_identities(self):
        thread=Mock();self.obs.thread=thread
        self.observe(30);self.assertEqual(thread.observe.call_args.args[0],[])
        self.reader.rows[12]['ticks']+=200;self.observe(31)
        candidates=thread.observe.call_args.args[0]
        dominant=max(candidates,key=lambda item:item['delta_ticks'])
        self.assertEqual(dominant['row']['pid'],12);self.assertEqual(dominant['delta_ticks'],200)
        self.reader.exes[12]=('/private/ruby',(9,9));self.observe(32)
        self.assertNotIn(12,[item['row']['pid'] for item in thread.observe.call_args.args[0]])
    def test_validator_rechecks_intermediate_ancestry_after_final_exe(self):
        count=0
        def hook(pid):
            nonlocal count
            if pid==12:
                count+=1
                if count==2:self.reader.rows[11]['ppid']=99
        self.reader.hook=hook
        with self.assertRaises(ValueError):self.obs.validate_thread_target(12,100,*self.reader.exes[12])
    def test_validator_rejects_reuse_exec_exit_unowned_and_closed(self):
        for change in ('reuse','exec','exit','unowned','closed'):
            self.setUp();exe,exe_id=self.reader.exes[12]
            if change=='reuse':self.reader.rows[12]['start']+=1
            elif change=='exec':self.reader.exes[12]=('/private/SECRET',(9,9))
            elif change=='exit':del self.reader.rows[12]
            elif change=='unowned':self.reader.rows[12]['ppid']=99
            else:self.obs.stop('cleanup_started')
            with self.assertRaises((ValueError,KeyError,ProcessLookupError)):
                self.obs.validate_thread_target(12,100,exe,exe_id)
    def test_validator_freezes_whole_owned_chain_across_batch(self):
        self.obs.validate_thread_target(12,100,*self.reader.exes[12])
        self.reader.rows[12]['ppid']=10  # Still owned, but changed during observed lifetime.
        with self.assertRaises(ValueError):self.obs.validate_thread_target(12,100,*self.reader.exes[12])
        self.setUp();self.obs.validate_thread_target(12,100,*self.reader.exes[12])
        self.reader.rows[11]['pgid']=77
        with self.assertRaises(ValueError):self.obs.validate_thread_target(12,100,*self.reader.exes[12])
    def test_combined_projection_cap_privacy_and_idempotence(self):
        process=dict(lifetime_id=128,category='wheel_status_publisher',cpu_delta_s=1000000.,covered_wall_s=8.,intervals=8,samples=9,peak_rss_kib=134217728,max_threads=100000,last_state='R',secret='/PRIVATE_PROCESS')
        thread=dict(thread_lifetime_id=128,category='named_llvmpipe_worker',cpu_delta_s=1000000.,covered_wall_s=8.,intervals=8,samples=9,name='SECRET_THREAD',pid=123,tid=456)
        out=p.sanitize(dict(rows=[process]*128,thread_observation=dict(rows=[thread]*128,secret='PRIVATE_THREAD')))
        self.assertEqual(len(out['rows']),32);self.assertEqual(len(out['thread_observation']['rows']),16)
        self.assertEqual(out,p.sanitize(out));encoded=json.dumps(out)
        self.assertLess(len(encoded.encode()),16384)
        for forbidden in ('PRIVATE','SECRET','"pid"','"tid"','"name"'):self.assertNotIn(forbidden,encoded)
    def test_phase_and_cleanup_prevent_further_thread_sampling(self):
        for reason in ('phase','cleanup'):
            self.setUp();thread=Mock();self.obs.thread=thread;self.observe(30)
            if reason=='phase':self.obs.safe('observe',dict(self.state(),phase='ready'))
            else:self.obs.wrap(lambda *args:None)('DELETE','/sim/session')
            count=thread.observe.call_count;self.observe(31)
            self.assertEqual(thread.observe.call_count,count);thread.stop.assert_called_once()


if __name__=='__main__':unittest.main()
