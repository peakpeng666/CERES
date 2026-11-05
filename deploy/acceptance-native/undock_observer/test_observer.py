import json,pathlib,tempfile,unittest,sys,time,os
from undock_observer.observer import Accumulator,accept_state
from undock_observer.lifecycle import ObserverProcess
class Tests(unittest.TestCase):
 def test_empty(self):
  r=Accumulator().result();self.assertIsNone(r['clock_delta_s']);self.assertIsNone(r['dock_final']);self.assertIsNone(r['relative_displacement_m'])
 def test_phase_bounds_and_projection(self):
  a=Accumulator();a.sample('pose',(999,999));a.phase('undocking',10)
  for k,v in [('clock',5),('pose',(30,40,3.1)),('dock',True),('motor',(0,0)),('clock',8),('pose',(33,44,-3.1)),('dock',False),('motor',(.1,0))]:a.sample(k,v)
  a.phase('failed',60);a.sample('motor',(1,1));r=a.result()
  self.assertLess(r['relative_rotation_rad'],.1);self.assertEqual(r['clock_delta_s'],3);self.assertEqual(r['relative_displacement_m'],5);self.assertEqual(r['motor_nonzero_count'],1)
  self.assertEqual(r['counts']['pose'],2);self.assertEqual(r['wall_s'],50);self.assertFalse(r['dock_final']);self.assertNotIn('coordinates',json.dumps(r))
 def test_invalid_and_clock_regression(self):
  a=Accumulator();a.phase('undocking',0);a.sample('clock',2);a.sample('clock',1);a.sample('clock',float('nan'));a.sample('dock','secret');self.assertIsNone(a.result()['clock_delta_s']);self.assertEqual(a.result()['invalid_samples'],2)
 def test_session_identity(self):
  with tempfile.TemporaryDirectory() as t:
   root=pathlib.Path(t);sid='sim_20261022T091000_abcdef';(root/'sim'/sid).mkdir(parents=True)
   self.assertEqual(accept_state({'session_id':sid,'phase':'undocking'},root,None),(sid,'undocking'))
   with self.assertRaises(ValueError):accept_state({'session_id':'../private','phase':'undocking'},root,None)
   with self.assertRaises(ValueError):accept_state({'session_id':sid,'phase':'undocking'},root,'sim_20261022T091000_123456')
 def test_schema_rejects_unknown_raw_fields(self):
  from undock_observer.projection import project
  r=Accumulator().result();r['status']='deadline';self.assertEqual(project(r),r)
  r['raw']='PRIVATE';self.assertEqual(project(r),{'schema':1,'status':'invalid_schema'});self.assertEqual(project(project(r)),project(r))
  del r['raw'];r['clock_delta_s']=float('inf');self.assertEqual(project(r)['status'],'invalid_schema')
 def test_long_observed_interval_and_strict_upper_bound(self):
  from undock_observer.projection import project
  a=Accumulator();a.phase('undocking',100);a.phase('localizing',1100)
  r=a.result();r['status']='none';self.assertEqual(project(r)['wall_s'],1000)
  r['wall_s']=1201;self.assertEqual(project(r)['wall_s'],1201)
  r['wall_s']=1201.01;self.assertEqual(project(r),{'schema':1,'status':'invalid_schema'})
 def test_lifecycle_normal_owned_only(self):
  with tempfile.TemporaryDirectory() as t:
   process=ObserverProcess([sys.executable,'-c','pass'],os.environ.copy(),pathlib.Path(t)/'log');process.proc.wait(timeout=3);r=process.close();self.assertEqual(r,{'exit_code':0,'forced_kill_count':0,'remaining_owned_count':0})
 def test_lifecycle_forced_cleanup(self):
  with tempfile.TemporaryDirectory() as t:
   ready=pathlib.Path(t)/'ready';code='import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path('+repr(str(ready))+').touch();time.sleep(30)'
   process=ObserverProcess([sys.executable,'-c',code],os.environ.copy(),pathlib.Path(t)/'log')
   deadline=time.monotonic()+3
   while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
   r=process.close(grace=.02);self.assertEqual(r['forced_kill_count'],1);self.assertEqual(r['remaining_owned_count'],0)

class RosLifecycleTests(unittest.TestCase):
 def exercise(self,destroy_error=False,shutdown_error=False,subscription_error=False,import_error=False,expect_deadline=False):
  from unittest.mock import Mock,patch
  from types import SimpleNamespace
  import io
  from undock_observer import observer
  from undock_observer.projection import project
  with tempfile.TemporaryDirectory() as t:
   root=pathlib.Path(t);runtime=root/'runtime';sid='sim_20261022T091000_abcdef';(runtime/'sim'/sid).mkdir(parents=True);output=root/'observer.json'
   node=Mock();ros=Mock();ros.create_node.return_value=node
   if destroy_error:node.destroy_node.side_effect=RuntimeError('PRIVATE_DESTROY')
   if shutdown_error:ros.shutdown.side_effect=RuntimeError('PRIVATE_SHUTDOWN')
   if subscription_error:node.create_subscription.side_effect=RuntimeError('PRIVATE_SUBSCRIPTION')
   dependencies=(ros,SimpleNamespace(NO=0),object(),object(),object(),object(),object(),object())
   opener=Mock();opener.open.side_effect=lambda *a,**k:io.BytesIO(json.dumps({'session_id':sid,'phase':('undocking' if expect_deadline or opener.open.call_count==1 else 'failed')}).encode())
   ticks={'n':0}
   def clock():ticks['n']+=1;return ticks['n']
   env={'ROS_DOMAIN_ID':'146','ROS_LOCALHOST_ONLY':'1','RMW_IMPLEMENTATION':'rmw_cyclonedds_cpp','URP_ACCEPTANCE_OWNER':'a'*48,'URP_LAB_RUNTIME':str(runtime)}
   with patch.dict(os.environ,env),patch.object(observer,'ros_dependencies',side_effect=ImportError('PRIVATE_IMPORT') if import_error else None,return_value=dependencies),patch.object(observer.urllib.request,'build_opener',return_value=opener),patch.object(observer.signal,'signal'):
    if expect_deadline:
     with patch.object(observer.time,'monotonic',side_effect=clock):code=observer.main(['--port','12345','--output',str(output)])
     self.assertGreaterEqual(ticks['n'],1200);self.assertLess(ticks['n'],1210)
    else:code=observer.main(['--port','12345','--output',str(output)])
   result=json.loads(output.read_text());self.assertEqual(project(result),result);self.assertNotIn('PRIVATE',json.dumps(result))
   if not import_error:
    node.destroy_node.assert_called_once();ros.shutdown.assert_called_once()
   else:ros.shutdown.assert_not_called()
   self.assertEqual(result['cleanup_destroy_failed'],destroy_error);self.assertEqual(result['cleanup_shutdown_failed'],shutdown_error)
   self.assertEqual(code,int(destroy_error or shutdown_error or subscription_error or import_error or expect_deadline))
   return result
 def test_observer_deadline_is_1200_with_cleanup(self):self.assertEqual(self.exercise(expect_deadline=True)['status'],'deadline')
 def test_ros_success_and_shutdown(self):self.assertEqual(self.exercise()['status'],'none')
 def test_destroy_exception_does_not_skip_shutdown(self):self.assertEqual(self.exercise(destroy_error=True)['status'],'cleanup_error')
 def test_shutdown_exception_still_writes_bounded_result(self):self.assertEqual(self.exercise(shutdown_error=True)['status'],'cleanup_error')
 def test_both_cleanup_exceptions_are_recorded(self):self.exercise(destroy_error=True,shutdown_error=True)
 def test_subscription_failure_still_cleans_up(self):self.assertEqual(self.exercise(subscription_error=True)['status'],'observer_error')
 def test_missing_ros_import_has_bounded_error(self):self.assertEqual(self.exercise(import_error=True)['status'],'observer_error')

if __name__=='__main__':unittest.main()
