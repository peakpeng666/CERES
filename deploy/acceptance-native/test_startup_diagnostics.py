import json,pathlib,tempfile,unittest
from startup_diagnostics import session_id,project,collect,sanitize
from public_summary import project as public_project
SID='sim_20261022T080000_abcdef'

class StartupDiagnosticsTests(unittest.TestCase):
    def test_exact_start_step_recovers_id_before_ready_failure(self):
        report={'status':'FAIL','steps':[{'name':'start simulation session','ok':True,'response':{'session_id':SID}},{'name':'session ready','ok':False}]}
        self.assertEqual(session_id(report),SID)
        self.assertEqual(report['status'],'FAIL')
        for bad in ('../x',SID+'/x','sim_private',SID+'\\x'):
            report['steps'][0]['response']['session_id']=bad;self.assertIsNone(session_id(report))
    def test_ambiguous_or_wrong_step_rejected(self):
        report={'steps':[{'name':'session ready','ok':True,'response':{'session_id':SID}}]}
        self.assertIsNone(session_id(report))
        report={'steps':[{'name':'start simulation session','ok':True,'response':{'session_id':SID}},{'name':'start simulation session','ok':True,'response':{'session_id':SID[:-1]+'1'}}]}
        self.assertIsNone(session_id(report))
    def test_private_projection_drops_all_raw_strings(self):
        report={'errors':['RuntimeError: 控制器/传感器未就绪 PRIVATE_PATH','关键服务 sim 意外退出，状态 1 PRIVATE_URL'],'counts':{'clock':2,'odom':3,'PRIVATE':999},'stages':[{'name':'starting','wall_s':0},{'name':'failed','wall_s':80}], 'assets':{'complete':False,'missing':['PRIVATE_ASSET']}}
        logs={'sim':'PRIVATE_PID PRIVATE_CMD process has died exit code -11 PRIVATE_URL'}
        result=project(report,logs);text=json.dumps(result)
        self.assertNotIn('PRIVATE',text);self.assertIn('sensors_not_ready',result['error_codes'])
        self.assertEqual(result['counts']['clock'],2);self.assertEqual(result['counts']['scan'],0)
        self.assertEqual(result['assets_missing_count'],1)
        self.assertEqual(result['component_exits'],[{'component':'sim','exit_code':-11}])
        raw={'state':'FAIL','startup_diagnostics':result}
        self.assertEqual(public_project(raw,require_runner=False)['state'],'FAIL')
    def test_bounded_private_files_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)/'sim'/SID;root.mkdir(parents=True);(root/'logs').mkdir()
            (root/'session-report.json').write_text(json.dumps({'counts':{'clock':7},'errors':['AMCL 初始定位超时']}))
            self.assertEqual(collect(tmp,SID)['counts']['clock'],7)
            (root/'logs/sim.log').symlink_to('/etc/passwd')
            self.assertEqual(collect(tmp,SID)['component_exits'],[])
            self.assertFalse(collect(tmp,'../bad')['report_available'])
    def test_untrusted_diagnostic_shape_is_sanitized(self):
        result=sanitize({'phase':'PRIVATE','error_codes':['PRIVATE'],'counts':{'clock':True},'stages':[{'phase':'starting','wall_s':float('inf')}],'component_exits':[{'component':'PRIVATE','exit_code':4}]})
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertEqual(result['stages'],[])
        self.assertIsNone(result['counts']['clock'])
        self.assertIsNone(sanitize({'counts':{'clock':10**1000}})['counts']['clock'])
        self.assertEqual(project({'stages':None},{})['stages'],[])
        self.assertIn('python_module_missing',project({}, {'session':'ModuleNotFoundError: PRIVATE_MODULE'})['error_codes'])
    def test_install_progress_is_fixed_enum(self):
        from runner.run import installation_progress
        with tempfile.TemporaryDirectory() as tmp:
            log=pathlib.Path(tmp)/'install'
            for line,expected in [('sudo apt-get install PRIVATE','apt_install'),('uv sync PRIVATE','uv'),('npm ci PRIVATE','npm'),('bash src/lab/ros/native/install-ros-extras.sh PRIVATE','native')]:
                log.write_text('+URP_SETUP '+line+'\n')
                self.assertEqual(installation_progress(log),expected)

class UndockDiagnosticsTests(unittest.TestCase):
    def test_exact_fixed_source_action_errors(self):
        labels={'/undock 动作服务不可用':'undock_server_unavailable','/undock 拒绝目标':'undock_goal_rejected','/undock 未确认取消':'undock_cancel_unconfirmed','/undock 执行预算耗尽':'undock_budget_exhausted','动作取消未收敛':'action_cancel_not_settled','ROS 响应超时':'ros_response_timeout','/undock 状态 5，未到达':'undock_canceled','/undock 状态 6，未到达':'undock_aborted'}
        for label,code in labels.items():
            result=project({'errors':[label+' PRIVATE_PATH_URL']},{})
            self.assertIn(code,result['error_codes'])
            self.assertNotIn('PRIVATE',json.dumps(result))
    def test_official_log_events_are_not_inferred_from_generic_abort(self):
        result=project({}, {'sim':'Received new undock goal PRIVATE\nUndock Goal Exceeded Runtime\nUndock Goal Aborted'})
        self.assertEqual(result['undock_events'],['aborted','received','runtime_exceeded'])
        self.assertEqual(project({}, {'sim':'Aborted PRIVATE'})['undock_events'],[])
        self.assertNotIn('PRIVATE',json.dumps(result))
    def test_available_scalars_and_unavailable_final_dock(self):
        report={'stages':[{'name':'undocking','wall_s':12.738},{'name':'failed','wall_s':62.831}], 'final_state':{'robot':{'t_sim':31.5,'speed':0.02,'ground_truth':['PRIVATE_COORDINATES']}}}
        result=project(report,{})
        self.assertEqual(result['dock_state_at_undock_entry'],'docked')
        self.assertEqual(result['final_dock_state'],'unavailable')
        self.assertEqual(result['final_sim_time_s'],31.5)
        self.assertEqual(result['final_speed_m_s'],0.02)
        self.assertEqual(result['error_codes'],[]) # Duration alone is not timeout evidence.
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertEqual(sanitize(result),result)
        self.assertEqual(public_project({'state':'FAIL','startup_diagnostics':result},require_runner=False)['state'],'FAIL')
    def test_invalid_scalars_and_unknown_dock_never_pass_through(self):
        result=sanitize({'final_sim_time_s':float('inf'),'final_speed_m_s':True,'dock_state_at_undock_entry':'PRIVATE','final_dock_state':'undocked'})
        self.assertIsNone(result['final_sim_time_s']);self.assertIsNone(result['final_speed_m_s'])
        self.assertEqual(result['dock_state_at_undock_entry'],'unavailable')
        self.assertEqual(result['final_dock_state'],'unavailable')

class UndockTimingProjectionTests(unittest.TestCase):
    def test_timing_whitelist_and_idempotence(self):
        from startup_diagnostics import sanitize_undock_timing
        raw={'policy':'undock_dual_clock_v1','simulation_budget_s':35.,'minimum_planned_rtf':.05,'wall_watchdog_s':700.,'no_progress_wall_s':30.,'stream_gap_wall_s':30.,'elapsed_wall_s':500.,'elapsed_sim_s':33.,'total_elapsed_wall_s':501.,'total_elapsed_sim_s':33.1,'terminal_status':4,'trigger':None,'samples':30000,'cancel_accepted':False,'max_stream_gap_wall_s':{'clock':.1,'dock':.5,'odom':.2,'ground_truth':.2},'start_wall_monotonic_s':123456,'start_sim_s':123,'raw':'PRIVATE'}
        result=sanitize_undock_timing(raw)
        self.assertEqual(result['terminal_status'],'succeeded');self.assertEqual(result['trigger'],'none')
        self.assertEqual(result,sanitize_undock_timing(result));self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('start_sim_s',result);self.assertNotIn('start_wall_monotonic_s',result)
        projected=project({'undock_timing':raw},{})
        self.assertEqual(projected['undock_timing'],result);self.assertEqual(sanitize(projected),projected)
        self.assertEqual(public_project({'state':'FAIL','startup_diagnostics':projected},require_runner=False)['state'],'FAIL')
    def test_timing_missing_and_malicious_values(self):
        from startup_diagnostics import sanitize_undock_timing
        empty=sanitize_undock_timing(None);self.assertEqual(empty['policy'],'unavailable');self.assertIsNone(empty['cancel_accepted']);self.assertIsNone(empty['samples']);self.assertEqual(empty,sanitize_undock_timing(empty))
        bad={'policy':'undock_dual_clock_v1','terminal_status':{'SECRET':'x'},'trigger':'stream_gap:PRIVATE','samples':True,'cancel_accepted':'true','wall_watchdog_s':float('inf'),'elapsed_wall_s':10**1000,'simulation_budget_s':-1,'max_stream_gap_wall_s':{'clock':float('nan'),'PRIVATE':1}}
        result=sanitize_undock_timing(bad)
        self.assertEqual(result['terminal_status'],'unavailable');self.assertEqual(result['trigger'],'unavailable')
        self.assertIsNone(result['samples']);self.assertIsNone(result['wall_watchdog_s']);self.assertIsNone(result['elapsed_wall_s']);self.assertIsNone(result['max_stream_gap_wall_s']['clock'])
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('SECRET',json.dumps(result))
    def test_each_explicit_stream_trigger_and_backwards_delta(self):
        from startup_diagnostics import sanitize_undock_timing
        for stream in ('clock','dock','odom','ground_truth'):
            for prefix in ('stream_gap','stream_missing'):
                trigger=prefix+':'+stream
                self.assertEqual(sanitize_undock_timing({'policy':'undock_dual_clock_v1','trigger':trigger})['trigger'],trigger)
        self.assertEqual(sanitize_undock_timing({'policy':'undock_dual_clock_v1','trigger':'clock_backwards','elapsed_sim_s':-1})['elapsed_sim_s'],-1)

if __name__=='__main__':unittest.main()
