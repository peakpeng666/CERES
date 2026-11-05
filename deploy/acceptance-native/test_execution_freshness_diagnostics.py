import copy,json,unittest
from execution_freshness_diagnostics import observation,project
from execution_diagnostics import timing,collect
from execution_protocol import expected_policy,matches_policy,receipt,sanitize,protocol_ok
class FreshnessDiagnosticsTests(unittest.TestCase):
 def snapshot(self):
  return {'wall_monotonic_s':987654321,'sim_s':987654321,'elapsed_wall_s':20,'elapsed_sim_s':2,'observation_gap_wall_s':.25,'clock_progress_gap_wall_s':.1,'clock_advanced':True,'streams':{name:{'received_wall_s':987654321,'source_stamp_s':987654321,'receipt_age_wall_s':.5,'receipt_age_sim_s':.05,'source_age_sim_s':.15,'freshness_age_sim_s':.15,'progress_age_wall_s':.6,'source_kind':'header' if name!='clock' else 'clock_stamp','source_fault':None,'count':12,'private':'SECRET'} for name in ('clock','scan','odom','ground_truth')}}
 def test_atomic_trigger_relative_ages_and_modes_survive(self):
  raw=self.snapshot();raw['streams']['scan']['receipt_age_wall_s']=3.125;raw['streams']['scan']['freshness_age_sim_s']=.45
  result=timing({'policy':'execution_budget_v2','diagnostics':'execution_observation_v1','trigger':'stream_stale_wall:scan','trigger_observation':raw})
  self.assertEqual(result['trigger'],'stream_stale_wall:scan');self.assertEqual(result['trigger_observation']['streams']['scan']['receipt_age_wall_s'],3.125);self.assertEqual(result['trigger_observation']['streams']['scan']['freshness_age_sim_s'],.45)
  text=json.dumps(result);self.assertNotIn('987654321',text);self.assertNotIn('SECRET',text);self.assertNotIn('source_stamp_s',text);self.assertEqual(result,timing(result))
 def test_zero_stamp_ground_truth_does_not_invent_measurement_age(self):
  raw=self.snapshot();row=raw['streams']['ground_truth'];row.update(source_kind='clock_at_receive_zero_stamp',source_age_sim_s=None)
  result=observation(raw)['streams']['ground_truth'];self.assertIsNone(result['source_age_sim_s']);self.assertEqual(result['source_kind'],'clock_at_receive_zero_stamp');self.assertEqual(result['freshness_age_sim_s'],.15)
 def test_fallback_never_claims_measurement_age_from_inconsistent_input(self):
  raw=self.snapshot();raw['streams']['ground_truth'].update(source_kind='clock_at_receive_zero_stamp',source_age_sim_s=0.125)
  result=observation(raw);self.assertIsNone(result['streams']['ground_truth']['source_age_sim_s']);self.assertEqual(result,observation(result))
 def test_negative_relative_ages_preserved_for_fault_diagnosis(self):
  raw=self.snapshot();raw['streams']['scan'].update(source_age_sim_s=-4,receipt_age_wall_s=-.5,source_fault='backwards')
  result=observation(raw)['streams']['scan'];self.assertEqual(result['source_age_sim_s'],-4);self.assertEqual(result['receipt_age_wall_s'],-.5);self.assertEqual(result['source_fault'],'backwards')
 def test_history_bounded_and_fixed_schema(self):
  rows=[dict(self.snapshot(),elapsed_wall_s=i) for i in range(12)]
  result=project({'recent_observations':rows,'last_observation':rows[-1],'trigger_observation':rows[-1],'PRIVATE':'SECRET'})
  self.assertEqual(len(result['recent_observations']),8);self.assertEqual(result['recent_observations'][0]['elapsed_wall_s'],4);self.assertEqual(result,project(result));self.assertNotIn('SECRET',json.dumps(result))
 def test_missing_and_wrong_containers_stay_unavailable(self):
  for bad in (None,[],False,'SECRET',{'PRIVATE':'SECRET'}):
   self.assertIsNone(observation(bad))
  result=project({'recent_observations':'SECRET','trigger_observation':[],'max_stream_age_sim_s':['SECRET']})
  self.assertEqual(result['recent_observations'],[]);self.assertIsNone(result['trigger_observation']);self.assertTrue(all(x is None for x in result['max_stream_age_sim_s'].values()))
 def test_all_nonfinite_huge_bool_and_unrecognized_enums_dropped(self):
  for bad in (float('nan'),float('inf'),-float('inf'),10**1000,True,[],{},'SECRET'):
   raw=self.snapshot();raw.update(elapsed_wall_s=bad,elapsed_sim_s=bad,clock_advanced=bad);raw['streams']['scan'].update(receipt_age_wall_s=bad,source_age_sim_s=bad,count=bad,source_kind=bad,source_fault=bad)
   result=observation(raw);self.assertIsNone(result['elapsed_wall_s']);self.assertIsNone(result['streams']['scan']['source_kind']);self.assertIsNone(result['streams']['scan']['count']);json.dumps(result,allow_nan=False);self.assertEqual(result,observation(result));self.assertNotIn('SECRET',json.dumps(result))
 def test_all_source_trigger_enums_are_retained_not_arbitrary_text(self):
  prefixes=('stream_stale_wall','stream_timing_missing','stream_stamp_missing','stream_stamp_invalid','stream_stamp_backwards','stream_clock_backwards','stream_stamp_future','stream_stale_sim','stream_no_progress')
  for prefix in prefixes:
   value=prefix+':scan';self.assertEqual(timing({'trigger':value})['trigger'],value);self.assertEqual(collect({'nav2':[{'action':'/follow_path','cancel_reason':value}]},[])['actions'][0]['cancel_reason'],value)
  self.assertIsNone(timing({'trigger':'stream_stamp_PRIVATE:SECRET'})['trigger'])
 def test_old_cohort_readable_but_not_new_policy_pass(self):
  old=timing({'policy':'execution_budget_v1','trigger':'stream_stale:scan','max_stream_gap_wall_s':{'scan':3.2}})
  self.assertEqual(old['policy'],'execution_budget_v1');self.assertEqual(old['trigger'],'stream_stale:scan');self.assertIsNone(old['trigger_observation']);self.assertEqual(old,timing(old))
  row={'protocol':'simulation_execution_v1'};self.assertEqual(sanitize(row)['protocol'],'simulation_execution_v1');self.assertFalse(protocol_ok(row,'maze'))
 def test_receipt_configured_protocol_fields_are_required_for_pass(self):
  policy=expected_policy(103,'maze');run=dict(mode='follow_path',budget_s=103,time_basis='simulation',wall_watchdog_s=900,budget_policy=policy,run_id='r',session_id='s',sim_run_id='x')
  result=receipt('maze',103,run,dict(run));self.assertTrue(protocol_ok(result,'maze'));self.assertEqual(result,sanitize(result));self.assertFalse(result['real_time_feasibility_measured'])
  for field in ('execution_policy','source_freshness_sim_s','receipt_liveness_wall_s','source_progress_wall_s','future_skew_sim_s','zero_stamp_fallback'):
   changed=dict(result);changed.pop(field);self.assertFalse(protocol_ok(changed,'maze'))
 def test_v2_exact_policy_rejects_v1_or_weakened_freshness(self):
  policy=expected_policy(103,'maze');self.assertEqual(policy['policy'],'execution_budget_v2');self.assertNotIn('telemetry_stale_wall_s',policy['effective'])
  run=dict(mode='follow_path',budget_s=103,time_basis='simulation',wall_watchdog_s=900,budget_policy=policy)
  self.assertTrue(matches_policy(run,'maze',103))
  for field,bad in [('telemetry_stale_sim_s',4),('telemetry_receipt_wall_s',31),('telemetry_progress_wall_s',31),('telemetry_future_skew_sim_s',4),('telemetry_zero_stamp_fallback',['scan'])]:
   changed=copy.deepcopy(run);changed['budget_policy']['effective'][field]=bad;self.assertFalse(matches_policy(changed,'maze',103))
  run['budget_policy']['policy']='execution_budget_v1';self.assertFalse(matches_policy(run,'maze',103))
if __name__=='__main__':unittest.main()
