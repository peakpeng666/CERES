import json
import os
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from shutdown_diagnostics import capture,sanitize,snapshot,outcome,read_file,event
from acceptance import derive
from public_summary import project
import test_acceptance as fixtures

class ShutdownDiagnosticsTests(unittest.TestCase):
 def fixture(self,root,status='FAIL'):
  p=root/'sim'/'sim_test';p.mkdir(parents=True,exist_ok=True)
  data={'session_id':'sim_test','status':status,'errors':[],'remaining_owned_processes':[],
   'lifecycle_shutdown':{'navigation':True,'localization':True},'gazebo_shutdown':{'accepted':True,'returncode':0,'stdout':'PRIVATE'},
   'cleanup':[{'name':'sim','group':123,'command':['PRIVATE'],'signals':['SIGINT_LEADER','SIGTERM'],
     'reaped':[{'pid':123,'returncode':-15}],'complete':True,'seconds':12.8}],
   'abnormal_exits':[{'group':'sim','returncode':-15,'pid':123,'log':'PRIVATE'}],
   'robot_node_shutdown':{'signaled':[{'pid':123}],'still_running':[]},'bridge_shutdown':{'signaled':[],'still_running':[]}}
  (p/'session-report.json').write_text(json.dumps(data));return p,data
 def test_actual_failure_shape_diagnosed_without_private_fields(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);self.fixture(r);out=capture(r,'sim_test',{'shutdown':{'phase':'failed','verified':False,'errors':['会话清理未通过：PRIVATE']}})
  self.assertEqual(out['session_status'],'FAIL');self.assertEqual(out['cleanup'][0]['returncodes'],[-15]);self.assertEqual(out['abnormal'][0]['returncodes'],[-15])
  self.assertEqual(out['full_loop_error_codes'],['session_cleanup_failed']);self.assertTrue(out['gazebo']['accepted'])
  self.assertNotIn('PRIVATE',json.dumps(out));self.assertNotIn('sim_test',json.dumps(out));self.assertNotIn('pid',json.dumps(out))
 def test_report_finalization_snapshots_do_not_rescue_failed_gate(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);a=capture(r,'sim_test',{});self.fixture(r,'PASS');b=capture(r,'sim_test',{})
  raw=fixtures.AcceptanceTests().valid();raw['cleanup_pass']=False;raw['shutdown_diagnostics']={'at_full_loop_return':a,'after_gateway_shutdown':b}
  self.assertEqual(project(raw,require_runner=False)['state'],'FAIL');self.assertEqual(a['session_read'],'missing');self.assertEqual(b['session_status'],'PASS')
 def test_minus15_still_rejects_cleanup(self):
  report,sim,solution,session,raw=fixtures.AcceptanceTests().evidence()
  report['shutdown']['verified']=False;session['status']='FAIL';session['abnormal_exits']=[{'group':'sim','returncode':-15}]
  derive(report,sim,solution,session,raw);self.assertFalse(raw['cleanup_pass'])
 def test_missing_fields_are_unavailable_not_measured_success(self):
  o=snapshot({});self.assertIsNone(o['remaining_owned_count']);self.assertIsNone(o['gazebo']['accepted']);self.assertEqual(o['session_status'],'unavailable')
 def test_malformed_containers_and_idempotence(self):
  for v in (None,[],True,7,{'at_full_loop_return':{'cleanup':[None,[],{'component':{},'signals':[[]]}],'lifecycle':[]}}):
   out=sanitize(v);self.assertEqual(out,sanitize(out));json.dumps(out,allow_nan=False)
 def test_numbers_are_finite_strict_and_bounded(self):
  for n in (True,float('inf'),float('nan'),10**1000,-1):
   o=snapshot({'cleanup_count':n,'cleanup':[{'seconds':n}],'remaining_owned_count':n})
   self.assertIsNone(o['cleanup_count']);self.assertIsNone(o['cleanup'][0]['seconds']);json.dumps(o,allow_nan=False)
 def test_unknown_fields_and_arbitrary_strings_cannot_escape(self):
  o=sanitize({'secret':'PRIVATE','at_full_loop_return':{'session_status':'PRIVATE','gateway_events':['PRIVATE'],
    'cleanup':[{'component':'PRIVATE','signals':['PRIVATE'],'returncodes':['PRIVATE']}],'abnormal':[{'component':'PRIVATE','lifecycle':'PRIVATE'}]}})
  self.assertNotIn('PRIVATE',json.dumps(o));self.assertEqual(o,sanitize(o))
 def test_lists_are_bounded(self):
  o=snapshot({'cleanup':[{'signals':['SIGINT']*100,'returncodes':[-15]*100}]*100,'abnormal':[{}]*100,'gateway_events':['unknown']*100})
  self.assertEqual(len(o['cleanup']),16);self.assertEqual(len(o['abnormal']),32);self.assertEqual(len(o['cleanup'][0]['returncodes']),32)
 def test_invalid_json_and_large_report(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);p,_=self.fixture(r);(p/'session-report.json').write_text('{')
   self.assertEqual(capture(r,'sim_test',{})['session_read'],'invalid')
   (p/'session-report.json').write_text(' '*100)
   with patch('shutdown_diagnostics.MAX_BYTES',10):self.assertEqual(capture(r,'sim_test',{})['session_read'],'too_large')
 def test_invalid_session_id_never_reads(self):
  for sid in ('../PRIVATE','sim_../PRIVATE',[],None,'sim_'+'x'*125):
   with patch('shutdown_diagnostics.read_file',side_effect=AssertionError):self.assertEqual(capture(Path('/unused'),sid,{})['session_read'],'unavailable')
 def test_mismatched_identity_is_visible(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);p,data=self.fixture(r);data['session_id']='sim_other';(p/'session-report.json').write_text(json.dumps(data));self.assertFalse(capture(r,'sim_test',{})['session_identity_matches'])
 def test_lifecycle_timeout_only_uses_exact_source_marker(self):
  self.assertEqual(outcome('ROS 响应超时'),'timeout');self.assertEqual(outcome('PRIVATE/timeout'),'error')
 def test_unreadable_is_distinct_from_missing(self):
  with patch('shutdown_diagnostics.os.open',side_effect=PermissionError('PRIVATE')):
   o=capture(Path('/unused'),'sim_test',{})
  self.assertEqual(o['session_read'],'unreadable');self.assertNotIn('PRIVATE',json.dumps(o))
 def test_nonregular_and_symlink_reports_are_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);real=r/'real';real.write_text('{}');fifo=r/'fifo';os.mkfifo(fifo)
   link=r/'link';link.symlink_to(real);hard=r/'hard';os.link(real,hard)
   for p in (fifo,link,r,hard):
    started=time.monotonic();self.assertEqual(read_file(p)[0],'invalid');self.assertLess(time.monotonic()-started,1)
 def test_ancestor_symlink_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);(r/'real').mkdir();(r/'real'/'report').write_text('{}');(r/'alias').symlink_to(r/'real',target_is_directory=True)
   self.assertEqual(read_file(r/'alias'/'report')[0],'invalid')
 def test_changed_file_during_read_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'report';p.write_text('{}');original=os.read;changed=[False]
   def changing(fd,n):
    b=original(fd,n)
    if not changed[0]:changed[0]=True;p.write_text('{"changed":true}')
    return b
   with patch('shutdown_diagnostics.os.read',side_effect=changing):self.assertEqual(read_file(p)[0],'invalid')
 def test_replaced_ancestor_during_read_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);parent=root/'parent';parent.mkdir();p=parent/'report';p.write_text('{}');original=os.read;changed=[False]
   def moving(fd,n):
    b=original(fd,n)
    if not changed[0]:
     changed[0]=True;parent.rename(root/'moved');parent.mkdir();p.write_text('{}')
    return b
   with patch('shutdown_diagnostics.os.read',side_effect=moving):self.assertEqual(read_file(p)[0],'invalid')
 def test_gateway_exit_label_is_neutral_after_escalation(self):
  self.assertEqual(event('会话在 3.0 s 内退出，返回码 0'),'exit_observed')
  self.assertEqual(event('SIGTERM 后 10 s 内退出，返回码 -15'),'exit_observed')
 def test_malformed_gazebo_container_is_not_observed_absence(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);p,data=self.fixture(r)
   for value in (None,[],True,'PRIVATE'):
    data['gazebo_shutdown']=value;(p/'session-report.json').write_text(json.dumps(data))
    self.assertIsNone(capture(r,'sim_test',{})['gazebo']['error_present'])
 def test_gateway_signal_and_returncode_without_text(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);p,_=self.fixture(r);(p/'gateway-stop.json').write_text(json.dumps({'session_id':'sim_test','returncode':-15,'steps':[{'text':'会话 120 s 内未退出，开始逐级发送信号 PRIVATE'},{'text':'发送 SIGTERM 到会话进程组 PRIVATE'}]}));o=capture(r,'sim_test',{})
  self.assertEqual(o['gateway_events'],['wait_exhausted','signal_term']);self.assertEqual(o['gateway_returncode'],-15);self.assertNotIn('PRIVATE',json.dumps(o))

if __name__=='__main__':unittest.main()
