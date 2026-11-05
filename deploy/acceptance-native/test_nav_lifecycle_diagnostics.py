import json,unittest
from startup_diagnostics import nav_lifecycle,sanitize_nav_lifecycle,project,sanitize
class NavLifecycleTests(unittest.TestCase):
 def test_node_bringup_and_shutdown_are_separate(self):
  lines=['Configuring controller_server','Activating controller_server','Server controller_server connected with bond.','Activating planner_server','Failed to change state for node: planner_server. Exception: PRIVATE','Failed to bring up all requested nodes. Aborting bringup.','Running Nav2 LifecycleManager rcl preshutdown (PRIVATE)','Deactivating controller_server','Failed to change state for node: controller_server PRIVATE']
  result=nav_lifecycle({'nav':'\n'.join(lines)})
  self.assertIn({'component':'nav','node':'planner_server','event':'transition_failed','context':'before_shutdown_marker','count':1},result['events'])
  self.assertIn({'component':'nav','node':'controller_server','event':'transition_failed','context':'at_or_after_shutdown_marker','count':1},result['events'])
  self.assertNotIn('PRIVATE',json.dumps(result));self.assertEqual(result,sanitize_nav_lifecycle(result))
  self.assertEqual(result['failure_time_alignment'],'unavailable')
 def test_later_node_bond_failure_and_service_wait(self):
  result=nav_lifecycle({'nav':'Activating bt_navigator\nServer bt_navigator was unable to be reached after 4.00s by bond. This server may be misconfigured.\nWaiting for service /velocity_smoother/get_state...\n'})
  self.assertEqual([r['event'] for r in result['events']],['activate_requested','bond_failed','state_service_wait'])
  self.assertTrue(all(r['context']=='no_shutdown_marker' for r in result['events']))
 def test_costmap_tf_and_localization_are_typed(self):
  result=nav_lifecycle({'localization':'Configuring amcl\nManaged nodes are active','nav':'[local_costmap.local_costmap]: Timed out waiting for transform from PRIVATE to PRIVATE to become available, tf error: SECRET'})
  self.assertIn('tf_wait',[r['event'] for r in result['events']]);self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('SECRET',json.dumps(result))
 def test_unknown_nodes_and_forged_fields_dropped(self):
  self.assertEqual(nav_lifecycle({'nav':'Activating private_node\nConfiguring controller_server_extra'})['events'],[])
  self.assertEqual(sanitize_nav_lifecycle({'events':[{'component':'nav','node':'PRIVATE','event':'activate_requested','context':'no_shutdown_marker','count':1}]})['events'],[])
 def test_counts_bounded_and_nested_projection_idempotent(self):
  p=project({}, {'nav':'Activating planner_server\n'*11000})
  self.assertEqual(p['nav_lifecycle']['events'][0]['count'],10000);self.assertEqual(p,sanitize(p))
  self.assertEqual(p['nav_lifecycle']['coverage'],'bounded_log_tail');self.assertEqual(p['nav_lifecycle']['log_window'],'tail_64k')
 def test_ansi_whole_message_and_private_exception_are_not_forwarded(self):
  result=nav_lifecycle({'nav':'[manager]: \x1b[1;34mActivating bt_navigator\x1b[0m\n[bt_navigator]: Exception when loading BT: SECRET /private/x https://private.invalid\n[unknown]: Error loading XML file: SECRET'})
  self.assertEqual([r['event'] for r in result['events']],['activate_requested','bt_load_failed'])
  self.assertNotIn('SECRET',json.dumps(result));self.assertNotIn('private',json.dumps(result))
 def test_truncated_private_tail_does_not_reconstruct_missing_prefix(self):
  import pathlib,tempfile
  from startup_diagnostics import collect
  with tempfile.TemporaryDirectory() as tmp:
   sid='sim_20261022T080000_abcdef';root=pathlib.Path(tmp)/'sim'/sid; (root/'logs').mkdir(parents=True)
   (root/'session-report.json').write_text('{}')
   (root/'logs/nav.log').write_text('Shutting down managed nodes...\n'+('X'*70000)+'Activating controller_server\nActivating planner_server\n')
   rows=collect(tmp,sid)['nav_lifecycle']['events']
   self.assertEqual(len(rows),1);self.assertEqual(rows[0]['node'],'planner_server');self.assertEqual(rows[0]['context'],'no_shutdown_marker')
 def test_projection_bounds_and_bad_types(self):
  row=dict(component='nav',node='manager',event='managed_active',context='no_shutdown_marker',count=1)
  self.assertEqual(len(sanitize_nav_lifecycle({'events':[row]*100})['events']),60)
  for value in (True,-1,float('nan'),float('inf'),10**1000,'SECRET'):
   self.assertEqual(sanitize_nav_lifecycle({'events':[dict(row,count=value)]})['events'],[])
  for value in (None,'SECRET',False,{'PRIVATE':True}):
   self.assertEqual(sanitize_nav_lifecycle({'events':value})['events'],[])
if __name__=='__main__':unittest.main()
