import copy,json,unittest
from execution_diagnostics import collect,sanitize,matching_solution
from public_summary import project
class ExecutionDiagnosticsTests(unittest.TestCase):
 def fixture(self):
  return dict(run_id='private-run',mode='follow_path',planned={'path':[[0,0],[1,0]],'length_m':1},progress={'leg':1,'legs_total':2},controller='FollowPath',leg_results=[]),[dict(run_id='private-run',scenario_snapshot={'private':'scenario'})]
 def plan(self,*args):return dict(path=[[0,0],[1,0]],planned_length_m=1,budget_s=90)
 def test_exact_budget_cancellation_preserves_only_typed_action_fields(self):
  sim,solutions=self.fixture();sim.update(error='TimeoutError: /follow_path 执行预算耗尽 PRIVATE',nav2=[dict(action='/follow_path',status=5,cancel_requested=True,cancel_reason='budget',cancel_accepted=True,feedback=['PRIVATE'],uuid='PRIVATE')])
  result=collect(sim,solutions,planner=self.plan)
  self.assertEqual(result['error_codes'],['budget_exhausted']);self.assertEqual(result['actions'][0],dict(action='follow_path',status=5,cancel_requested=True,cancel_reason='budget',cancel_accepted=True))
  self.assertEqual(result['budget_s'],90);self.assertEqual(result['budget_provenance'],'reconstructed_to_sim_plan');self.assertEqual(result['controller'],'FollowPath');self.assertEqual(result['leg'],1)
  self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('private-run',json.dumps(result));self.assertEqual(sanitize(result),result)
 def test_recorded_budget_wins_without_calling_reconstruction(self):
  sim,solutions=self.fixture();sim['budget_s']=123
  def forbidden(*a):raise AssertionError('must not reconstruct')
  result=collect(sim,solutions,planner=forbidden);self.assertEqual(result['budget_s'],123);self.assertEqual(result['budget_provenance'],'execution_record')
 def test_missing_ambiguous_or_nonmatching_solution(self):
  sim,solutions=self.fixture()
  for records in ([],solutions*2,[{'run_id':'other'}],None):
   result=collect(sim,records,planner=self.plan);self.assertIsNone(result['budget_s']);self.assertEqual(result['reconstruction_status'],'missing_solution')
  self.assertIsNone(matching_solution({},[{}]))
 def test_geometry_or_length_mismatch_not_observed_budget(self):
  sim,solutions=self.fixture()
  for key,value in [('path',[[0,0],[2,0]]),('planned_length_m',2),('path',[])]:
   plan=self.plan();plan[key]=value
   result=collect(sim,solutions,planner=lambda *a:plan);self.assertEqual(result['reconstruction_status'],'geometry_mismatch');self.assertIsNone(result['budget_s'])
 def test_rejected_reconstruction_and_missing_data(self):
  sim,solutions=self.fixture()
  def rejected(*a):raise ValueError('PRIVATE')
  result=collect(sim,solutions,planner=rejected);self.assertEqual(result['reconstruction_status'],'plan_rejected');self.assertNotIn('PRIVATE',json.dumps(result))
  self.assertIsNone(collect({},[])['leg_result_count']);self.assertIsNone(collect({},[])['controller'])
 def test_error_cases_and_unclassified_error_not_guessed(self):
  for text,code in [('执行中传感器/时钟数据超时','stream_stale'),('/follow_path 状态 6，未到达','action_aborted'),('动作取消未收敛','cancel_not_settled')]:
   self.assertIn(code,collect({'error':text},[])['error_codes'])
  result=collect({'error':'PRIVATE','metrics':{'duration_wall_s':104}},[]);self.assertTrue(result['error_present']);self.assertEqual(result['error_codes'],[])
 def test_action_limit_keeps_terminal_tail(self):
  actions=[dict(action='/follow_path',status=4)]*130+[dict(action='/follow_path',status=5,cancel_reason='budget')]
  result=collect({'nav2':actions},[]);self.assertEqual(len(result['actions']),128);self.assertEqual(result['actions_total'],131);self.assertTrue(result['actions_truncated']);self.assertEqual(result['actions'][-1]['status'],5)
 def test_malformed_finite_values_and_unknown_fields(self):
  for bad in ([],{},float('nan'),float('inf'),10**1000,True,'PRIVATE'):
   result=sanitize(dict(actions=[dict(action='follow_path',status=bad,cancel_reason=bad,cancel_requested=bad)],controller=bad,budget_s=bad,error_codes=[bad],leg=bad,PRIVATE='SECRET'))
   self.assertIsNone(result['budget_s']);self.assertIsNone(result['actions'][0]['status']);self.assertNotIn('PRIVATE',json.dumps(result));json.dumps(result,allow_nan=False);self.assertEqual(result,sanitize(result))
  for value in ([],None,'PRIVATE',True):self.assertEqual(sanitize(value),sanitize({}))
 def test_public_summary_does_not_turn_failure_into_pass(self):
  raw={'state':'FAIL','execution_diagnostics':collect({'error':'执行预算耗尽'},[])}
  result=project(raw,require_runner=False);self.assertEqual(result['state'],'FAIL');self.assertEqual(result['execution_diagnostics'],raw['execution_diagnostics'])
if __name__=='__main__':unittest.main()
