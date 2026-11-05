import json
import tempfile
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch
from test_lifecycle import API,Clock,PLAN
from warehouse_lifecycle import drive
from warehouse_diagnostics import project,failure

class LifecycleDiagnosticTests(unittest.TestCase):
 def run_api(self,api,save=None):
  clock=Clock();seen=[];saved={}
  result=drive(api,'replay_id',PLAN,lambda:None,save or (lambda k,v:saved.update({k:v})),clock.clock,clock.sleep,on_session=seen.append)
  return result,seen,saved
 def api(self,mode):
  original=API()
  def call(method,path,data=None,timeout=5):
   if path=='/sim/session' and method=='POST':return {'session_id':'sim_test','phase':'starting'}
   if path=='/sim/state' and not original.stopping:
    if mode=='gone':return {'phase':'failed','session_id':None,'errors':['private raw reason']}
    if mode=='timeout':raise TimeoutError('private URL/token')
    if mode=='changed':return {'phase':'ready','session_id':'sim_other'}
    if mode=='missing_ready_id':return {'phase':'ready','session_id':None}
    if mode=='http':raise urllib.error.HTTPError('https://private',503,'private message',{},None)
   return original(method,path,data,timeout)
  return call
 def test_failed_terminal_keeps_start_id_and_failure(self):
  result,seen,saved=self.run_api(self.api('gone'))
  self.assertFalse(result['passed']);self.assertEqual(result['session_id'],'sim_test');self.assertEqual(seen,['sim_test'])
  self.assertEqual(result['diagnostics']['failure_code'],'session_not_ready');self.assertEqual(result['diagnostics']['last_session_phase'],'failed')
  self.assertNotIn('private',json.dumps(result['diagnostics']))
 def test_poll_timeout_keeps_id_and_typed_phase(self):
  result,seen,_=self.run_api(self.api('timeout'))
  self.assertFalse(result['passed']);self.assertEqual(result['session_id'],'sim_test')
  self.assertEqual(result['diagnostics']['exception_kind'],'timeout');self.assertEqual(result['diagnostics']['failure_phase'],'session_ready')
 def test_changed_identity_rejected_without_rebinding(self):
  result,seen,_=self.run_api(self.api('changed'))
  self.assertFalse(result['passed']);self.assertEqual(result['session_id'],'sim_test');self.assertEqual(seen,['sim_test'])
  self.assertEqual(result['diagnostics']['failure_code'],'session_identity_changed')
 def test_ready_missing_id_still_rejected(self):
  result,_,_=self.run_api(self.api('missing_ready_id'));self.assertFalse(result['passed']);self.assertEqual(result['diagnostics']['failure_code'],'session_not_ready')
 def test_http_status_only(self):
  result,_,_=self.run_api(self.api('http'));d=result['diagnostics'];self.assertEqual(d['exception_kind'],'http');self.assertEqual(d['http_status'],503);self.assertNotIn('private',json.dumps(d))
 def test_identity_callback_survives_final_save_failure(self):
  clock=Clock();seen=[]
  def save(k,v):raise OSError('private destination')
  with self.assertRaises(OSError):drive(self.api('gone'),'replay_id',PLAN,lambda:None,save,clock.clock,clock.sleep,on_session=seen.append)
  self.assertEqual(seen,['sim_test'])
 def test_primary_and_cleanup_failures_separate(self):
  api=self.api('timeout')
  def broken(method,path,data=None,timeout=5):
   if method=='DELETE':raise urllib.error.HTTPError('private',502,'private',{},None)
   return api(method,path,data,timeout)
  result,_,_=self.run_api(broken);d=result['diagnostics']
  self.assertEqual(d['exception_kind'],'timeout');self.assertEqual(d['cleanup_exception_kind'],'http');self.assertEqual(d['cleanup_http_status'],502)
 def test_uncertain_start_probes_before_delete_without_success(self):
  original=API('start_uncertain');result,seen,_=self.run_api(original)
  self.assertFalse(result['passed']);self.assertEqual(result['session_id'],'sim_test');self.assertEqual(seen,['sim_test'])
  self.assertEqual(result['diagnostics']['failure_phase'],'session_start');self.assertTrue(result['diagnostics']['identity_probe_attempted'])
  paths=[(m,p) for m,p,_ in original.calls];self.assertLess(paths.index(('GET','/sim/state')),paths.index(('DELETE','/sim/session')))
 def test_uncertain_start_probe_failure_does_not_skip_stop(self):
  original=API('start_uncertain')
  def broken(method,path,data=None,timeout=5):
   if method=='GET' and path=='/sim/state' and not original.stopping:raise TimeoutError('private')
   return original(method,path,data,timeout)
  result,_,_=self.run_api(broken);self.assertFalse(result['passed']);self.assertTrue(original.stopping);self.assertEqual(result['diagnostics']['identity_probe_exception_kind'],'timeout')
 def test_retained_identity_enables_existing_report_collection_without_pass(self):
  from startup_diagnostics import collect
  original=self.api('gone');sid='sim_20261022T000000_abcdef'
  def actual_id(*args,**kwargs):
   row=original(*args,**kwargs)
   if row.get('session_id')=='sim_test':row=dict(row,session_id=sid)
   return row
  result,_,_=self.run_api(actual_id)
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);folder=root/'sim'/sid;folder.mkdir(parents=True)
   (folder/'session-report.json').write_text(json.dumps({'session_id':sid,'status':'FAIL','counts':{},'errors':[]}))
   self.assertFalse(collect(root,None)['report_available'])
   self.assertTrue(collect(root,result['session_id'])['report_available'])
   self.assertFalse(result['passed'])
 def test_transient_cleanup_identity_is_not_erased_by_later_null(self):
  for identity in ('sim_other','sim_../outside',[], 'sim_'+'a'*129):
   with self.subTest(identity_type=type(identity).__name__):
    original=API();observed=[False]
    def unstable(method,path,data=None,timeout=5):
     if method=='GET' and path=='/sim/state' and original.stopping and not observed[0]:
      observed[0]=True;return {'phase':'stopping','session_id':identity}
     return original(method,path,data,timeout)
    result,_,_=self.run_api(unstable)
    self.assertFalse(result['passed']);self.assertEqual(result['session_id'],'sim_test')
    self.assertIn(result['diagnostics']['cleanup_failure_code'],('session_identity_changed','session_identifier_invalid'))
    self.assertTrue(original.stopping)
 def test_cleanup_phase_does_not_overwrite_startup_failure_phase(self):
  result,_,_=self.run_api(self.api('gone'))
  self.assertEqual(result['diagnostics']['last_session_phase'],'failed');self.assertEqual(result['diagnostics']['cleanup_session_phase'],'stopped')
 def test_delete_response_identity_is_also_checked(self):
  original=API()
  def foreign_delete(method,path,data=None,timeout=5):
   row=original(method,path,data,timeout)
   return {'phase':'stopping','session_id':'sim_other'} if method=='DELETE' else row
  result,_,_=self.run_api(foreign_delete);self.assertFalse(result['passed']);self.assertTrue(original.stopping)
  self.assertEqual(result['diagnostics']['cleanup_failure_code'],'session_identity_changed')
 def test_probe_interruption_does_not_skip_delete(self):
  original=API('start_uncertain')
  def interrupted(method,path,data=None,timeout=5):
   if method=='GET' and path=='/sim/state' and not original.stopping:raise KeyboardInterrupt()
   return original(method,path,data,timeout)
  result,_,_=self.run_api(interrupted);self.assertFalse(result['passed']);self.assertTrue(original.stopping)
  self.assertEqual(result['diagnostics']['identity_probe_exception_kind'],'interrupted')
 def test_success_predicates_still_pass(self):
  result,_,_=self.run_api(API());self.assertTrue(result['passed']);self.assertEqual(result['diagnostics']['failure_code'],'none')

class ProjectionTests(unittest.TestCase):
 def test_malicious_and_malformed_values(self):
  for raw in (None,[],42,{'harness':[],'lifecycle':{'phase':{},'http_status':True,'elapsed_wall_s':float('nan'),'failure_code':'private path','session_id':'sim_secret'},'session_report':[]}):
   value=project(raw);json.dumps(value,allow_nan=False);self.assertEqual(value,project(value));self.assertNotIn('sim_secret',json.dumps(value));self.assertNotIn('private path',json.dumps(value))
 def test_finite_bounds_and_strict_bool(self):
  row=project({'lifecycle':{'elapsed_wall_s':10001,'session_id_observed':1,'http_status':999}})['lifecycle']
  self.assertIsNone(row['elapsed_wall_s']);self.assertIsNone(row['session_id_observed']);self.assertIsNone(row['http_status'])
 def test_exception_text_is_not_a_code(self):
  self.assertEqual(failure(RuntimeError('private URI'))['failure_code'],'unknown')
if __name__=='__main__':unittest.main()
