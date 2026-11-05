import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from contextlib import ExitStack
import acceptance
class ObserverIntegrationTests(unittest.TestCase):
 def exercise(self,ready=True,forced=0,exit_code=0):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);source=root/'source';deployment=source/'setup/2026-10-15/deployment';deployment.mkdir(parents=True)
   for rel in ('tf-repro/buffer_candidate.cpp','gui-plugin-lifetime.cpp','tf2-overlay-manifest.json','gui-overlay-manifest.json'):
    p=deployment/rel;p.parent.mkdir(exist_ok=True);p.write_text('fixture')
   args=SimpleNamespace(source=source,private_dir=root/'private',summary=root/'summary',stage='maze',harness_sha='a'*40)
   observer=Mock();observer.wait_ready.return_value=ready;observer.close.return_value={'exit_code':exit_code,'forced_kill_count':forced,'remaining_owned_count':0}
   gateway=Mock();gateway.poll.return_value=None;gateway.returncode=0;loop=Mock();loop.wait.return_value=0;loop.returncode=0
   def api(base,method,path,*a,**k):return {'status':'ok'} if path=='/health' else {'recommended':'fixture'} if path=='/recommend' else {}
   def derive(*a):a[-1].update(state='PASS',cleanup_pass=True)
   def project(raw,**kwargs):return dict(raw,state=raw['state'] if raw.get('cleanup_pass') else 'FAIL')
   with ExitStack() as stack:
    patches={'verify_source':Mock(),'api':api,'wait_for_solver':Mock(),'ObserverProcess':Mock(return_value=observer),'owned':lambda token:[],'session_id':lambda report:'sim_20261022T091000_abcdef','derive':derive,'collect':lambda *args:{},'project':project}
    for name,value in patches.items():stack.enter_context(patch.object(acceptance,name,value))
    popen=stack.enter_context(patch.object(acceptance.subprocess,'Popen',side_effect=[gateway,loop]))
    code=acceptance.execute(args)
   observer.close.assert_called_once();observer.wait_ready.assert_called_once()
   self.assertEqual(popen.call_count,2 if ready else 1)
   return code,json.loads(args.summary.read_text())
 def test_normal_observer_can_already_have_exited(self):
  code,result=self.exercise();self.assertEqual(code,0);self.assertTrue(result['cleanup_pass'])
 def test_forced_observer_cleanup_cannot_pass(self):
  code,result=self.exercise(forced=1,exit_code=-9);self.assertEqual(code,1);self.assertFalse(result['cleanup_pass'])
 def test_observer_shutdown_error_cannot_pass(self):
  code,result=self.exercise(exit_code=1);self.assertEqual(code,1);self.assertFalse(result['cleanup_pass'])
 def test_readiness_failure_stops_before_full_loop(self):
  code,result=self.exercise(ready=False);self.assertEqual(code,1);self.assertFalse(result['cleanup_pass'])
if __name__=='__main__':unittest.main()
