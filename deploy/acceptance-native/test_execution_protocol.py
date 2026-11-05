import copy,json,unittest
from unittest.mock import patch
from execution_protocol import expected_policy,matches_policy,receipt,sanitize,protocol_ok,maze_receipt
from execution_diagnostics import timing,collect
class ProtocolTests(unittest.TestCase):
 def record(self,stage='maze',budget=103):
  return dict(mode='follow_path',run_id='private',session_id='private',sim_run_id='private',budget_s=budget,time_basis='simulation',wall_watchdog_s=900 if stage=='maze' else 5400,budget_policy=expected_policy(budget,stage))
 def test_budget_is_selected_clock_not_observed_duration(self):
  for stage,budget in [('maze',103),('warehouse',649.6)]:
   row=self.record(stage,budget);row['metrics']={'duration_wall_s':700,'duration_sim_s':50}
   result=receipt(stage,budget,row,row);self.assertTrue(protocol_ok(result,stage));self.assertEqual(result['selected_clock_budget_s'],budget);self.assertFalse(result['real_time_feasibility_measured']);self.assertEqual(result,sanitize(result));self.assertNotIn('private',json.dumps(result))
 def test_no_override_even_if_numerically_equal(self):
  row=self.record();row['budget_policy']['requested']['budget_s']=103
  self.assertFalse(matches_policy(row,'maze',103))
 def test_each_frozen_field_is_checked(self):
  row=self.record()
  for key,bad in [('budget_s',104),('time_basis','wall'),('wall_watchdog_s',103),('mode','nav2_waypoints')]:
   other=copy.deepcopy(row);other[key]=bad;self.assertFalse(matches_policy(other,'maze',103))
  for key,bad in [('scope','private'),('clock_no_progress_wall_s',31),('telemetry_stale_wall_s',4),('navigate_wall_cap_s',90),('required_streams',[])]:
   other=copy.deepcopy(row);other['budget_policy']['effective'][key]=bad;self.assertFalse(matches_policy(other,'maze',103))
 def test_terminal_missing_changed_or_wrong_identity_fails(self):
  row=self.record()
  for bad in ({},None,dict(row,sim_run_id='other'),dict(row,budget_s=104)):
   self.assertFalse(protocol_ok(receipt('maze',103,row,bad),'maze'))
 def test_malformed_values_never_pass_or_escape(self):
  for value in ([],True,None,'PRIVATE',float('nan'),float('inf'),10**1000):
   self.assertFalse(matches_policy(value,'maze',103));self.assertFalse(protocol_ok(sanitize({'selected_clock_budget_s':value,'stage':value}),'maze'));json.dumps(sanitize({'selected_clock_budget_s':value}),allow_nan=False)
 def test_original_cli_request_and_submission_verified(self):
  row=self.record();report={'execution_budget_requested':{'budget_s':None,'time_basis':'simulation','wall_watchdog_s':900},'overall_wall_budget_s':1800,'steps':[{'name':'submit sim run (follow_path)','ok':True,'response':row}]}
  with patch('execution_diagnostics.reconstruct',return_value=({'budget_s':103},'reconstructed')):
   self.assertTrue(protocol_ok(maze_receipt(report,row,[],None),'maze'))
   report['execution_budget_requested']['budget_s']=103
   self.assertFalse(protocol_ok(maze_receipt(report,row,[],None),'maze'))
 def test_missing_or_duplicate_original_response_rejected(self):
  row=self.record();report={'steps':[]}
  with patch('execution_diagnostics.reconstruct',return_value=({'budget_s':103},'reconstructed')):
   self.assertFalse(protocol_ok(maze_receipt(report,row,[],None),'maze'))
 def test_timing_projection_no_absolute_clocks(self):
  raw=dict(policy='execution_budget_v1',trigger='stream_stale:scan',terminal_state='failed',samples=10,total_elapsed_wall_s=600,total_elapsed_sim_s=103,start_wall_monotonic_s=123456,start_sim_s=1000,PRIVATE='secret')
  projected=timing(raw);self.assertEqual(projected['trigger'],'stream_stale:scan');self.assertEqual(projected,timing(projected));self.assertNotIn('123456',json.dumps(projected));self.assertNotIn('secret',json.dumps(projected))
  self.assertEqual(collect({'execution_timing':raw,'nav2':[{'action':'/follow_path','cancel_reason':'simulation_budget'}]},[])['actions'][0]['cancel_reason'],'simulation_budget')
 def test_timing_nonfinite_and_unknown_values_null(self):
  raw=dict(trigger='PRIVATE',total_elapsed_sim_s=float('nan'),elapsed_wall_s=10**1000,max_stream_gap_wall_s=['secret'])
  clean=timing(raw);self.assertIsNone(clean['trigger']);self.assertIsNone(clean['total_elapsed_sim_s']);self.assertIsNone(clean['elapsed_wall_s']);json.dumps(clean,allow_nan=False)
 def test_missing_protocol_cannot_pass_complete_physics(self):
  from test_acceptance import AcceptanceTests
  from public_summary import project
  raw=AcceptanceTests().valid();raw.pop('execution_protocol');self.assertEqual(project(raw,require_runner=False)['state'],'FAIL')
 def test_cli_stage_watchdogs_and_job_envelopes(self):
  import ast,pathlib,types
  root=pathlib.Path(__file__).parent
  tree=ast.parse((root/'acceptance.py').read_text())
  cmd=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='cmd' for t in n.targets))
  words=[x.value for x in cmd.elts if isinstance(x,ast.Constant) and isinstance(x.value,str)]
  for flag,value in [('--modes','follow_path'),('--execution-time-basis','simulation'),('--execution-wall-watchdog','900'),('--budget','1800')]:self.assertEqual(words[words.index(flag)+1],value)
  self.assertNotIn('--execution-budget',words)
  runner=ast.parse((root/'runner/run.py').read_text())
  calls=[n for n in ast.walk(runner) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='child']
  acceptance=next(c for c in calls if isinstance(c.args[1],ast.Constant) and c.args[1].value=='acceptance.log')
  deadline=compile(ast.Expression(acceptance.args[2]),'<deadline>','eval')
  for stage,expected in [('maze',3600),('warehouse',9000)]:
   self.assertEqual(eval(deadline,{'time':types.SimpleNamespace(monotonic=lambda:100),'args':types.SimpleNamespace(stage=stage)}),100+expected)
  install=next(c for c in calls if isinstance(c.args[1],ast.Constant) and c.args[1].value=='install.log')
  self.assertEqual(eval(compile(ast.Expression(install.args[2]),'<deadline>','eval'),{'time':types.SimpleNamespace(monotonic=lambda:100)}),3700)
  import yaml
  workflow=yaml.safe_load((root/'.github/workflows/native-acceptance.yml.template').read_text())
  self.assertEqual(workflow['jobs']['maze']['timeout-minutes'],125);self.assertEqual(workflow['jobs']['warehouse']['timeout-minutes'],220)
if __name__=='__main__':unittest.main()
