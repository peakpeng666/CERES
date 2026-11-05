"""Pure fixtures and owned local Python children only, never ROS or Ignition."""
import copy
import ast
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import textwrap
import threading
import unittest
from unittest.mock import patch, Mock

from startup_observer import Observer, ENABLE, DELAY, HARD_WALL_S
from startup_observer_child import Measurements, ECHO, INFO, MAX_BYTES, main as child_main
from startup_observation_schema import sanitize


class Clock:
    value = 100.0
    def __call__(self):return self.value


def row(phase='starting', sid='sim_fixture', count=26):
    return {'phase': phase, 'session_id': sid, 'world': 'warehouse', 'errors': [],
            'telemetry': {'counts': {'clock': count, 'cmd_vel': 0}, 'age_s': {'clock': 4.0}}}


def actual_source_client(clock):
    """Execute the exact frozen source excerpts, without importing ROS/services."""
    fixture=json.loads((Path(__file__).parent/'fixtures/startup_api_3acf.json').read_text())
    source={k:v for item in fixture['sources'] for k,v in item['functions'].items()}
    http=Mock(return_value=(None,'PRIVATE_TRANSPORT_DETAIL'))
    namespace={'json':json,'copy':copy,'time':types.SimpleNamespace(monotonic=clock),
               'bridge_known_issues':lambda:[], 'http_json':http}
    exec(source['stopped_state'],namespace)
    methods=[v for k,v in source.items() if k.startswith('SimClient.')]
    exec('class SimClient:\n'+ '\n\n'.join(textwrap.indent(m,'    ') for m in methods),namespace)
    exec(source['Session.state'],namespace)
    client=namespace['SimClient']()
    client.state=namespace['stopped_state']();client._fails=0;client._last_json='';client.bus=None;client.url='http://fixture'
    client._handle_execution=lambda ex:None
    client.services=types.SimpleNamespace(_lock=threading.RLock(),_busy={},sim_adopted=None,
        sim_meta={'session_id':'sim_fixture','world':'warehouse','spawn':{'x':-5.08,'y':-.75,'yaw':1.570796}},
        sim_alive=lambda:True,changed=lambda:None)
    def success():
        session=types.SimpleNamespace(lock=threading.RLock(),st={**client.current_state(),'errors':[]},
            execution=None,counts={'clock':26,'dock':1,'scan':4,'ground_truth':8},at={'clock':clock()-4},tf_age=None)
        http.return_value=(200,namespace['state'](session));client.poll_once()
    return client,http,success


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.temp = tempfile.TemporaryDirectory()
        self.obs = Observer({ENABLE: '1', 'URP_ACCEPTANCE_OWNER': 'PRIVATE'}, self.temp.name,
                            '/known/source', clock=self.clock)
        self.obs.bind('sim_fixture')

    def tearDown(self):
        self.obs.safe('close')
        self.temp.cleanup()

    def test_late_window_is_opt_in_and_preserves_default(self):
        self.assertEqual(self.obs.trigger_s, 5)
        obs = Observer({ENABLE: '1', DELAY: '30'}, self.temp.name, '/known/source', clock=self.clock)
        obs.bind('sim_fixture')
        with patch.object(obs, 'start') as start:
            self.clock.value += 29.9
            obs.observe(row())
            start.assert_not_called()
            self.clock.value += .1
            obs.observe(row())
            start.assert_called_once_with()
        self.assertEqual(obs.close()['observer_trigger_wall_s'], 30)

    def test_invalid_delay_does_not_mutate_control_response(self):
        for delay in ('31', 'PRIVATE', 30, None, {}, []):
            obs = Observer({ENABLE: '1', DELAY: delay}, self.temp.name, '/known/source', clock=self.clock,
                           popen=lambda *args, **kwargs: self.fail('must not start'))
            obs.bind('sim_fixture')
            value = row()
            self.assertIs(obs.wrap(lambda *args: value)('GET', '/sim/state'), value)
            result = obs.close()
            self.assertEqual(result['status'], 'unavailable')
            self.assertIsNone(result['observer_trigger_wall_s'])

    def test_late_window_never_starts_after_phase_departure(self):
        obs = Observer({ENABLE: '1', DELAY: '30'}, self.temp.name, '/known/source', clock=self.clock)
        obs.bind('sim_fixture')
        self.clock.value += 31
        with patch.object(obs, 'start') as start:
            obs.observe(row(phase='failed'))
            start.assert_not_called()
        self.assertEqual(obs.close()['sampling_end_reason'], 'startup_left')

    def test_disabled_has_no_mutation_or_side_effect(self):
        self.obs = Observer({}, self.temp.name, '/known/source', popen=lambda *a, **k:self.fail())
        value = row()
        seen = []
        api = self.obs.wrap(lambda *args:seen.append(args) or value)
        self.obs.bind('sim_fixture')
        self.assertIs(api('GET', '/sim/state', timeout=3), value)
        self.assertEqual(seen, [('GET', '/sim/state', None, 3)])
        self.assertEqual(self.obs.close()['status'], 'disabled')

    def test_wrapper_preserves_response_identity_content_and_call_count(self):
        value = row(); original = copy.deepcopy(value); calls = []
        api = self.obs.wrap(lambda *args:calls.append(args) or value)
        self.assertIs(api('GET', '/sim/state', timeout=2), value)
        self.assertEqual(value, original)
        self.assertEqual(calls, [('GET', '/sim/state', None, 2)])

    def test_original_exception_is_unchanged_no_retry(self):
        for exc in (TimeoutError('PRIVATE'), KeyboardInterrupt('PRIVATE')):
            calls = []
            def api(*args):calls.append(args);raise exc
            wrapped = self.obs.wrap(api)
            try:wrapped('POST', '/sim/session', {}, timeout=60)
            except BaseException as caught:self.assertIs(caught, exc)
            else:self.fail('exception swallowed')
            self.assertEqual(len(calls), 1)

    def test_observer_error_never_changes_original_result(self):
        value = row()
        with patch.object(self.obs, 'observe', side_effect=OSError()):
            self.assertIs(self.obs.wrap(lambda *args:value)('GET', '/sim/state'), value)
        self.assertEqual(self.obs.raw['status'], 'unavailable')

    def test_safe_preserves_real_cancellation_kinds(self):
        for exc in (KeyboardInterrupt(),SystemExit(2),RuntimeError('interrupted')):
            with patch.object(self.obs,'observe',side_effect=exc):
                with self.assertRaises(type(exc)) as caught:self.obs.safe('observe',row())
                self.assertIs(caught.exception,exc)

    def test_original_api_exception_wins_over_cutoff_cancellation(self):
        original=TimeoutError('original')
        def api(*args):raise original
        with patch.object(self.obs,'cutoff',side_effect=KeyboardInterrupt()):
            with self.assertRaises(TimeoutError) as caught:self.obs.wrap(api)('GET','/sim/state')
        self.assertIs(caught.exception,original)

    def test_delete_attempted_once_despite_observer_cancellation(self):
        calls=[];exc=RuntimeError('interrupted')
        with patch.object(self.obs,'before_cleanup',side_effect=exc):
            with self.assertRaises(RuntimeError) as caught:
                self.obs.wrap(lambda *args:calls.append(args) or row('stopped'))('DELETE','/sim/session',None,17)
        self.assertIs(caught.exception,exc)
        self.assertEqual(calls,[('DELETE','/sim/session',None,17)])

    def test_delete_api_error_primary_over_observer_cancellation(self):
        original=TimeoutError();calls=[]
        def api(*args):calls.append(args);raise original
        with patch.object(self.obs,'before_cleanup',side_effect=KeyboardInterrupt()):
            with self.assertRaises(TimeoutError) as caught:self.obs.wrap(api)('DELETE','/sim/session')
        self.assertIs(caught.exception,original);self.assertEqual(len(calls),1)

    def test_constructor_wrapper_preserves_control_cancellation(self):
        tree=ast.parse(Path(__file__).with_name('warehouse_acceptance.py').read_text())
        block=next(n for n in ast.walk(tree) if isinstance(n,ast.Try) and isinstance(n.body[0],ast.Assign)
                   and isinstance(n.body[0].value,ast.Call) and isinstance(n.body[0].value.func,ast.Name)
                   and n.body[0].value.func.id=='Observer')
        module=ast.Module(body=[block],type_ignores=[])
        from startup_observer import is_cancellation
        for exc in (KeyboardInterrupt(),SystemExit(2),RuntimeError('interrupted')):
            def constructor(*args):raise exc
            with self.assertRaises(type(exc)) as caught:
                exec(compile(module,'production-constructor-guard','exec'),{'Observer':constructor,'is_cancellation':is_cancellation,
                     'env':{},'private':self.temp.name,'source':'/fixture','raw':{}})
            self.assertIs(caught.exception,exc)

    def test_sigterm_during_observation_is_not_swallowed(self):
        previous=signal.getsignal(signal.SIGTERM)
        def stop(signum,frame):raise RuntimeError('interrupted')
        signal.signal(signal.SIGTERM,stop)
        try:
            self.obs.clock=lambda:os.kill(os.getpid(),signal.SIGTERM)
            with self.assertRaisesRegex(RuntimeError,'interrupted'):
                self.obs.wrap(lambda *args:row())('GET','/sim/state')
        finally:
            self.obs.clock=self.clock
            signal.signal(signal.SIGTERM,previous)

    def test_no_probe_before_validated_binding(self):
        self.obs.sid = None
        with patch.object(self.obs, 'start') as start:
            self.clock.value += 10
            self.obs.observe(row())
            start.assert_not_called()

    def test_invalid_or_changed_id_permanently_disables(self):
        for value in ('../sim_bad', 'sim_x/../../bad', 'sim_other', 'sim_'+'a'*125, None):
            obs = Observer({ENABLE:'1'}, self.temp.name, '/known/source', clock=self.clock)
            obs.bind('sim_fixture');obs.bind(value)
            self.assertTrue(obs.closed)
            obs.bind('sim_fixture')
            self.assertTrue(obs.closed)

    def test_foreign_response_never_captured_or_rebound(self):
        self.obs.observe(row(sid='sim_foreign'))
        self.assertEqual(self.obs.sid, 'sim_fixture')
        self.assertEqual(self.obs.raw['ros_api']['samples'], 0)
        self.assertEqual(self.obs.raw['ros_api']['availability'], 'identity_mismatch')

    def test_foreign_world_never_captured(self):
        value=row();value['world']='maze';self.obs.observe(value)
        self.assertTrue(self.obs.closed)
        self.assertEqual(self.obs.raw['ros_api']['samples'],0)

    def test_cleanup_response_and_settle_counts_excluded(self):
        self.obs.observe(row())
        before = copy.deepcopy(self.obs.raw['ros_api'])
        calls = []
        def api(*args):
            self.assertTrue(self.obs.closed)
            calls.append(args)
            return row('stopping', count=900)
        wrapped = self.obs.wrap(api)
        wrapped('DELETE', '/sim/session', timeout=180)
        wrapped('GET', '/sim/state')
        self.assertEqual(self.obs.raw['ros_api'], before)
        self.assertEqual(len(calls), 2)

    def test_leave_startup_before_delete_cuts_off(self):
        for phase in ('stopping','failed','stopped','ready','undocking','localizing'):
            obs = Observer({ENABLE:'1'}, self.temp.name, '/known/source', clock=self.clock)
            obs.bind('sim_fixture');obs.observe(row());obs.observe(row(phase,count=876));obs.observe(row(count=999))
            self.assertEqual(obs.raw['ros_api']['last']['counts']['clock'],26)
            self.assertTrue(obs.closed)

    def test_errors_in_cached_startup_are_observed_not_phase_departure(self):
        value=row();value['errors']=['PRIVATE'];self.obs.observe(value)
        self.assertFalse(self.obs.closed)
        self.assertEqual(self.obs.raw['ros_api']['starting_error_responses'],1)
        self.assertEqual(sanitize(self.obs.raw)['ros_api']['last_state']['error_count'],1)

    def test_real_api_transient_errors_without_telemetry_still_launch_at_five(self):
        client,http,success=actual_source_client(self.clock)
        for _ in range(3):client.poll_once()
        state=client.current_state()
        self.assertEqual(state['phase'],'starting');self.assertTrue(state['errors']);self.assertNotIn('telemetry',state)
        self.clock.value=102;self.obs.observe(state)
        self.assertFalse(self.obs.closed);self.assertIsNone(self.obs.proc)
        def start():self.obs.raw['status']='running'
        with patch.object(self.obs,'start',side_effect=start) as launched:
            self.clock.value=105;self.obs.observe(client.current_state());launched.assert_called_once_with()
            self.clock.value=106;success();self.obs.observe(client.current_state());launched.assert_called_once_with()
        out=sanitize(self.obs.raw)
        self.assertEqual(out['startup_observation_policy'],'starting_phase_v2')
        self.assertEqual(out['ros_api']['samples'],1)
        self.assertEqual(out['ros_api']['state_responses'],3)
        self.assertEqual(out['ros_api']['starting_error_responses'],2)
        self.assertEqual(out['ros_api']['first_state']['errors_present'],True)
        self.assertEqual(out['ros_api']['last_state']['errors_present'],False)
        self.assertEqual(out['ros_api']['last']['counts']['clock'],26)
        self.assertNotIn('PRIVATE',json.dumps(out))

    def test_real_api_starting_success_after_transient_error_can_launch(self):
        client,http,success=actual_source_client(self.clock)
        for _ in range(3):client.poll_once()
        self.obs.observe(client.current_state())
        self.clock.value=106;success()
        with patch.object(self.obs,'start') as launched:self.obs.observe(client.current_state());launched.assert_called_once_with()
        self.assertFalse(self.obs.closed)

    def test_real_api_busy_stopping_is_permanent_cutoff(self):
        client,http,success=actual_source_client(self.clock)
        client.poll_once();client.services._busy['sim']='stopping'
        self.obs.observe(client.current_state())
        client.services._busy.clear();self.clock.value=106;success()
        with patch.object(self.obs,'start') as launched:self.obs.observe(client.current_state());launched.assert_not_called()
        out=sanitize(self.obs.raw)
        self.assertEqual(out['sampling_end_reason'],'startup_left')
        self.assertEqual(out['ros_api']['last_state']['phase'],'stopping')
        self.assertFalse(out['ros_api']['last_state']['telemetry_present'])

    def test_invalid_phase_is_not_reported_as_phase_departure(self):
        self.obs.observe(row(phase={'PRIVATE':'value'}))
        out=sanitize(self.obs.raw)
        self.assertEqual(out['sampling_end_reason'],'invalid_state')
        self.assertEqual(out['ros_api']['last_state']['phase'],'unknown')
        self.assertNotIn('PRIVATE',json.dumps(out))

    def test_real_api_lifecycle_keeps_not_ready_failure_and_one_delete(self):
        from warehouse_lifecycle import drive
        client,http,success=actual_source_client(self.clock);calls=[];gets=0;stopped=False
        def api(method,path,data=None,timeout=5):
            nonlocal gets,stopped
            calls.append((method,path))
            if method=='POST':return client.current_state()
            if method=='DELETE':stopped=True;return {'session_id':'sim_fixture','world':'warehouse','phase':'stopped','errors':[]}
            if stopped:return {'session_id':'sim_fixture','world':'warehouse','phase':'stopped','errors':[]}
            gets+=1
            if gets<=6:client.poll_once()
            elif gets==7:success()
            else:client.state.update(phase='failed',errors=['PRIVATE_FAILURE'])
            return client.current_state()
        def sleep(seconds):self.clock.value+=seconds
        def start():self.obs.raw['status']='running'
        with patch.object(self.obs,'start',side_effect=start) as launched:
            out=drive(self.obs.wrap(api),'fixture',{'budget_s':30},lambda:None,lambda *args:None,
                      clock=self.clock,sleep=sleep,on_session=self.obs.bind)
        launched.assert_called_once_with()
        self.assertFalse(out['passed']);self.assertEqual(out['diagnostics']['failure_code'],'session_not_ready')
        self.assertEqual(calls.count(('POST','/sim/session')),1);self.assertEqual(calls.count(('DELETE','/sim/session')),1)
        self.assertEqual(len(calls),11)
        observed=sanitize(self.obs.raw)
        self.assertEqual(observed['ros_api']['last_state']['phase'],'failed')
        self.assertEqual(observed['ros_api']['last_state']['error_count'],1)
        self.assertNotIn('PRIVATE',json.dumps(sanitize(self.obs.raw)))

    def test_api_sample_count_and_fields_bounded(self):
        for i in range(1000):self.obs.observe(row(count=i))
        self.assertEqual(self.obs.raw['ros_api']['samples'],128)
        self.assertEqual(self.obs.raw['ros_api']['last']['counts']['clock'],999)

    def start_fake(self, code):
        calls=[]
        def spawn(command, **kwargs):
            calls.append((command,kwargs))
            return subprocess.Popen([sys.executable,'-c',code], **kwargs)
        self.obs.popen=spawn
        self.clock.value=105
        self.obs.observe(row())
        return calls

    def await_exit(self):
        end=time.monotonic()+3
        while not self.obs.exited() and time.monotonic()<end:time.sleep(.005)
        self.assertTrue(self.obs.exited())

    def test_owned_group_exact_partition_and_profile(self):
        calls=self.start_fake('import time;time.sleep(30)')
        command,kw=calls[0]
        self.assertEqual(kw['env']['IGN_PARTITION'],'urp_sim_fixture')
        self.assertNotIn('URP_ACCEPTANCE_OWNER',kw['env'])
        self.assertTrue(kw['start_new_session'])
        self.assertIn('/known/source/setup/profile.sh',command)
        self.assertNotIn('sim_fixture',str(command))
        self.assertEqual(os.getpgid(self.obs.proc.pid),self.obs.proc.pid)
        self.obs.close()
        self.assertIn(self.obs.proc.returncode,(-signal.SIGTERM,-signal.SIGKILL))
        self.assertTrue(self.obs.raw['observer_leader_reaped']);self.assertIsNone(self.obs.raw['observer_cleanup_complete'])

    def test_unreaped_leader_is_kept_until_group_cleanup(self):
        self.start_fake('pass');self.await_exit()
        self.assertIsNone(self.obs.proc.returncode)
        self.obs.close()
        self.assertEqual(self.obs.proc.returncode,0)

    def test_expiry_kills_owned_group(self):
        self.start_fake('import time;time.sleep(30)')
        self.obs.expire();self.await_exit();self.obs.close()
        self.assertTrue(self.obs.raw['observer_watchdog_expired'])
        self.assertEqual(self.obs.raw['status'],'bounded_stop')

    def test_valid_finished_window_requires_starting_confirmation(self):
        payload=sanitize({'status':'complete','native_stats':{'availability':'no_messages'},'ros_clock':{'availability':'no_messages'}})
        self.start_fake('print('+repr(json.dumps(payload))+')');self.await_exit()
        self.obs.observe(row(count=100))
        self.assertTrue(self.obs.confirmed)
        self.assertEqual(self.obs.raw['status'],'complete')
        self.obs.observe(row(count=200));self.obs.observe(row('failed'))
        self.assertEqual(self.obs.raw['status'],'complete')
        self.assertEqual(self.obs.raw['ros_api']['observer_window_first']['counts']['clock'],26)
        self.assertEqual(self.obs.raw['ros_api']['observer_window_last']['counts']['clock'],100)
        self.assertEqual(self.obs.raw['ros_api']['last']['counts']['clock'],200)

    def test_unconfirmed_window_discarded_on_failed_response(self):
        self.start_fake('print("{}")');self.await_exit()
        self.obs.observe(row('failed'))
        self.assertFalse(self.obs.confirmed)
        self.assertEqual(sanitize(self.obs.raw)['native_stats']['availability'],'unavailable')

    def test_bad_child_output_is_unavailable_and_cleaned(self):
        self.start_fake('print("PRIVATE RAW PATH")');self.await_exit()
        self.obs.safe('observe',row())
        self.assertEqual(self.obs.raw['status'],'unavailable')
        self.assertTrue(self.obs.raw['observer_leader_reaped']);self.assertIsNone(self.obs.raw['observer_cleanup_complete'])
        self.assertNotIn('PRIVATE',json.dumps(sanitize(self.obs.raw)))

    def test_probe_spawn_failure_no_retry(self):
        self.clock.value=105
        with patch.object(self.obs,'popen',side_effect=OSError()) as spawn:
            self.obs.safe('observe',row());self.obs.safe('observe',row())
            self.assertEqual(spawn.call_count,1)

    def test_owned_group_descendant_killed_after_leader_exits(self):
        code='import subprocess,sys,os; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);print(p.pid,flush=True);os._exit(0)'
        self.start_fake(code);self.await_exit()
        self.obs.drain();pid=int(self.obs.buffer)
        self.obs.close()
        end=time.monotonic()+1
        while time.monotonic()<end:
            try:state=Path('/proc/'+str(pid)+'/stat').read_text().split(') ',1)[1].split()[0]
            except (FileNotFoundError,ProcessLookupError):break
            if state=='Z':break
            time.sleep(.005)
        else:self.fail('owned descendant still running')

    def test_timer_failure_still_kills_started_child(self):
        with patch('startup_observer.threading.Timer.start',side_effect=RuntimeError()):
            self.obs.popen=lambda cmd,**kw:subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],**kw)
            self.clock.value=105;self.obs.safe('observe',row())
        self.await_exit();self.obs.close()
        self.assertTrue(self.obs.raw['observer_leader_reaped']);self.assertIsNone(self.obs.raw['observer_cleanup_complete'])

    def test_actual_finalizer_keeps_application_cleanup_after_observer_cancel(self):
        # Compile the production finalizer AST unchanged into a mock-only test
        # function. This executes no application/ROS setup or native commands.
        tree=ast.parse(Path(__file__).with_name('warehouse_acceptance.py').read_text())
        execute=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='execute')
        body=[ast.Global(names=['forced','diagnostic'])]+next(n for n in execute.body if isinstance(n,ast.Try)).finalbody
        module=ast.Module(body=[ast.FunctionDef(name='finalize',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=body,decorator_list=[])],type_ignores=[])
        ast.fix_missing_locations(module)
        for initial in ('PASS','FAIL'):
            calls=[];exc=RuntimeError('interrupted')
            def cancel(*args):raise exc
            class Gateway:
                returncode=None
                def poll(self):return None
                def wait(self,timeout):calls.append(('wait',timeout));self.returncode=0
            raw={'state':initial,'cleanup_pass':True};diagnostic={'harness':{},'lifecycle':{'failure_code':'api_timeout'}}
            namespace={'lifecycle_only':False,'process_observer':None,'observer':types.SimpleNamespace(safe=cancel,raw={}), 'raw':raw,'base':'http://127.0.0.1:1',
                       'gateway':Gateway(),'api':lambda *args,**kw:calls.append(('api',args)),
                       'diagnostic':diagnostic,'describe_failure':lambda exc:{'exception_kind':'interrupted'},
                       'owned':lambda token:[], 'token':'fixture','forced':False,'subprocess':subprocess,
                       'signal':signal,'time':time,'collect':lambda *args:calls.append(('collect',)) or {},
                       'runtime':Path(self.temp.name),'sid':'sim_fixture','project':lambda raw,**kw:dict(raw),
                       'a':types.SimpleNamespace(summary=Path(self.temp.name)/'summary.json'),'json':json}
            namespace['identity_capture']=types.SimpleNamespace(finish=lambda:{},defer_cancellation=lambda exc:None,apply_pending=lambda result:None,raise_pending=lambda:None)
            exec(compile(module,'production-finalizer','exec'),namespace)
            with self.assertRaises(RuntimeError) as caught:namespace['finalize']()
            self.assertIs(caught.exception,exc)
            self.assertTrue(any(c[0]=='api' and c[1][1:] == ('POST','/shutdown',{}) for c in calls))
            self.assertIn(('wait',240),calls);self.assertIn(('wait',5),calls);self.assertIn(('collect',),calls)
            self.assertEqual(raw['state'],'ERROR' if initial=='PASS' else 'FAIL')
            self.assertEqual(diagnostic['lifecycle']['failure_code'],'api_timeout')


class MeasurementTests(unittest.TestCase):
    def setUp(self):self.clock=Clock();self.m=Measurements(95,self.clock)

    def stat(self, iterations=24, stamp=72000000):
        return json.dumps({'simTime':{'nsec':stamp},'realTime':{'sec':'1'},'iterations':str(iterations)}).encode()+b'\n'

    def info(self):self.m.feed('info',b'Publishers [Address, Message Type]:\n  tcp://PRIVATE:123, ignition.msgs.WorldStatistics\n')

    def test_valid_bytes_incremental_and_defaults(self):
        value=self.stat()
        for chunk in (value[:7],value[7:]):self.assertTrue(self.m.feed('echo',chunk))
        self.info();self.clock.value+=1;out=self.m.finish(0,0)
        self.assertEqual(out['native_stats']['samples'],1)
        self.assertEqual(out['native_stats']['first']['sim_time_s'],.072)
        self.assertFalse(out['native_stats']['first']['paused'])
        self.assertIsNone(out['native_stats']['sim_time_advanced'])
        self.assertEqual(out['native_stats']['last_receipt_age_at_window_end_s'],1)
        self.assertNotIn('PRIVATE',json.dumps(out))

    def test_missing_messages_not_fabricated_stall(self):
        self.info();out=self.m.finish(0,0)
        self.assertEqual(out['native_stats']['availability'],'no_messages')
        self.assertEqual(out['native_stats']['samples'],0)
        self.assertIsNone(out['native_stats']['sim_time_advanced'])
        self.assertIsNone(out['ros_clock']['first'])

    def test_wrong_type_disables_native_values(self):
        self.m.feed('echo',self.stat());self.m.feed('info',b'Publishers [Address, Message Type]:\n  x, arbitrary.Type\n')
        out=self.m.finish(0,0)
        self.assertEqual(out['native_stats']['availability'],'type_mismatch')
        self.assertIsNone(out['native_stats']['first'])

    def test_ros_clock_is_direct_and_receipt_timing_separate(self):
        self.m.ros(0,72000000);self.clock.value+=1;self.m.ros(0,75000000)
        out=self.m.finish(1,1)['ros_clock']
        self.assertEqual(out['messages'],2)
        self.assertTrue(out['sim_time_advanced'])
        self.assertEqual(out['last']['receipt_wall_s'],6)
        self.assertEqual(out['last']['sim_time_s'],.075)

    def test_sampling_end_age_excludes_later_cleanup(self):
        self.m.ros(0,72000000);self.clock.value=120
        out=self.m.finish(1,1,sampling_ended=101)
        self.assertEqual(out['ros_clock']['last_receipt_age_at_window_end_s'],1)

    def test_native_samples_bound(self):
        for _ in range(128):self.assertTrue(self.m.feed('echo',self.stat()))
        self.assertFalse(self.m.feed('echo',self.stat()))
        self.assertEqual(self.m.raw['native_stats']['samples'],128)

    def test_byte_and_line_bounds(self):
        self.assertFalse(self.m.feed('echo',b'x'*(MAX_BYTES+1)))
        self.assertLessEqual(self.m.raw['output_bytes'],MAX_BYTES)
        self.assertFalse(Measurements(95,self.clock).feed('echo',b'x'*16385))

    def test_invalid_ros_and_json_not_measurements(self):
        for pair in ((True,0),(0,1000000000),(-1,0),(86400,1)):
            self.m.ros(*pair)
        self.m.feed('echo',b'\xff\n{"simTime": NaN}\n')
        self.assertEqual(self.m.raw['ros_clock']['messages'],0)
        self.assertEqual(self.m.raw['ros_clock']['invalid_messages'],4)
        self.assertEqual(self.m.raw['native_stats']['invalid_samples'],2)

    def test_commands_only_fixed_topic_read_operations(self):
        self.assertEqual(ECHO,('/usr/bin/ign','topic','-e','-t','/world/warehouse/stats','--json-output','-d','8','--force-version','11'))
        self.assertEqual(INFO,('/usr/bin/ign','topic','-i','-t','/world/warehouse/stats','--force-version','11'))
        for command in (ECHO,INFO):
            self.assertNotIn('-p',command);self.assertNotIn('service',command)

    def test_child_main_with_fake_ros_and_pipe_fixtures_only(self):
        import io
        callback=[];commands=[];tick=[0]
        node=types.SimpleNamespace(create_subscription=lambda typ,topic,cb,qos:callback.append(cb),destroy_node=lambda:None)
        def create_node(name,**kwargs):
            self.assertEqual(kwargs,{'enable_rosout':False,'start_parameter_services':False})
            return node
        def spin(node,timeout_sec):
            tick[0]+=1;callback[0](types.SimpleNamespace(clock=types.SimpleNamespace(sec=0,nanosec=tick[0]*3000000)))
        fake_ros=types.SimpleNamespace(init=lambda **kw:None,create_node=create_node,spin_once=spin,shutdown=lambda:None)
        class Process:
            def __init__(self, command, **kwargs):
                commands.append(command);read,write=os.pipe()
                value=(b'Publishers [Address, Message Type]:\n  private, ignition.msgs.WorldStatistics\n' if command==INFO else self_stat)
                os.write(write,value);os.close(write);self.stdout=os.fdopen(read,'rb');self.returncode=0
            def poll(self):return 0
            def wait(self,timeout):return 0
        self_stat=self.stat()+self.stat(25,75000000)
        modules={'rclpy':fake_ros,'rclpy.qos':types.SimpleNamespace(qos_profile_sensor_data=object()),
                 'rclpy.signals':types.SimpleNamespace(SignalHandlerOptions=types.SimpleNamespace(NO=0)),
                 'rosgraph_msgs':types.SimpleNamespace(),'rosgraph_msgs.msg':types.SimpleNamespace(Clock=object())}
        output=io.StringIO()
        with patch.dict(sys.modules,modules),patch('startup_observer_child.subprocess.Popen',Process),patch('startup_observer_child.WINDOW_S',.03),patch('startup_observer_child.signal.signal'),patch('sys.stdout',output):
            self.assertEqual(child_main(['--origin',str(time.monotonic()-5)]),0)
        result=json.loads(output.getvalue())
        self.assertEqual(commands,[ECHO,INFO])
        self.assertEqual(result['native_stats']['samples'],2)
        self.assertTrue(result['native_stats']['sim_time_advanced'])
        self.assertTrue(result['ros_clock']['sim_time_advanced'])
        self.assertNotIn('private',output.getvalue())


if __name__=='__main__':unittest.main()
