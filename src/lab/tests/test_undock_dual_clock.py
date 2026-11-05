"""Deterministic timing policy tests; no native ROS acceptance implied."""
import ast
import math
import json
from pathlib import Path
import unittest
from test_session_action_cancellation import session, Interrupted

SOURCE=Path(__file__).parents[1]/'ros/urp_sim_session.py'
tree=ast.parse(SOURCE.read_text())
ns={'math':math}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='UndockTiming'],type_ignores=[]),str(SOURCE),'exec'),ns)
UndockTiming=ns['UndockTiming']

class DualClock(unittest.TestCase):
    def setup_session(self,**kw):
        s,h,c=session(**kw);s.report={}
        spin=s.spin
        # Extend the existing fake sensor arrivals with the required dock stream.
        s.at['dock']=0.
        def tick(*args,**kwargs):
            spin(*args,**kwargs)
            if not kw.get('stale'):s.at['dock']=c.wall
        s.spin=tick
        return s,h,c
    def action(self,s,timing=None):return s.action(None,'/undock',None,700,False,timing=timing or UndockTiming())
    def test_policy_defaults_and_bad_configuration(self):
        p=UndockTiming();self.assertEqual(p.wall_watchdog_s,700.)
        for val in (0,-1,float('inf'),float('nan')):
            with self.assertRaises(ValueError):UndockTiming(simulation_budget_s=val)
    def test_normal_success(self):
        s,h,c=self.setup_session(result_at=10)
        self.assertEqual(self.action(s).status,4);self.assertEqual(h.cancel_calls,0)
        self.assertIsNone(s.report['undock_timing']['trigger'])
    def test_supported_slow_clock_exceeds_old_50_wall_limit(self):
        s,h,c=self.setup_session(result_at=200,rtf=.05)
        self.assertEqual(self.action(s).status,4);self.assertEqual(h.cancel_calls,0)
        self.assertGreater(c.wall,50)
    def test_frozen_duplicate_clock_even_with_fresh_arrivals(self):
        s,h,c=self.setup_session(result_at=10000,rtf=0)
        with self.assertRaisesRegex(TimeoutError,'clock_no_progress'):self.action(s)
        self.assertLess(c.wall,33);self.assertEqual(h.cancel_calls,1)
        self.assertEqual(s.report['undock_timing']['trigger'],'clock_no_progress')
    def test_backwards_jump(self):
        s,h,c=self.setup_session(result_at=10000)
        s.sim_time=lambda:c.wall if c.wall<5 else c.wall-4
        with self.assertRaisesRegex(TimeoutError,'clock_backwards'):self.action(s)
        self.assertLess(c.wall,7)
    def test_simulation_budget_bounded(self):
        s,h,c=self.setup_session(result_at=10000,rtf=.1)
        with self.assertRaisesRegex(TimeoutError,'simulation_budget'):self.action(s)
        self.assertLess(c.wall,353)
    def test_wall_watchdog_below_planned_rtf(self):
        s,h,c=self.setup_session(result_at=10000,rtf=.001)
        with self.assertRaisesRegex(TimeoutError,'wall_watchdog'):self.action(s)
        self.assertLess(c.wall,703)
    def test_missing_each_required_stream(self):
        for stream in UndockTiming.required:
            s,h,c=self.setup_session(result_at=10000)
            base=s.spin
            def spin(*args,_stream=stream,**kwargs):
                base(*args,**kwargs);s.at.pop(_stream,None)
            s.spin=spin;s.at.pop(stream,None)
            with self.subTest(stream=stream),self.assertRaisesRegex(TimeoutError,'stream_missing:'+stream):self.action(s)
            self.assertLess(c.wall,3)
    def test_stale_each_required_stream(self):
        for stream in UndockTiming.required:
            s,h,c=self.setup_session(result_at=10000)
            base=s.spin
            def spin(*args,_stream=stream,**kwargs):
                base(*args,**kwargs);s.at[_stream]=0.
            s.spin=spin
            with self.subTest(stream=stream),self.assertRaisesRegex(TimeoutError,'stream_gap:'+stream):self.action(s)
            self.assertLess(c.wall,32)
    def test_stop_and_cancel_trigger_recorded_and_cleanup_bounded(self):
        for flag in ('stop_at','cancel_at'):
            s,h,c=self.setup_session(result_at=10000,rtf=0,**{flag:5})
            with self.assertRaises(Interrupted):self.action(s)
            self.assertEqual(s.report['undock_timing']['trigger'],flag.removesuffix('_at'))
            self.assertIsNone(s.goal);self.assertLess(c.wall,7)
    def test_terminal_processing_does_not_invent_timeout(self):
        s,h,c=self.setup_session(result_at=2,terminal_processing=1000)
        self.assertEqual(self.action(s).status,4)
        self.assertIsNone(s.report['undock_timing']['trigger'])
    def test_late_terminal_after_trigger_does_not_erase_timeout(self):
        s,h,c=self.setup_session(result_at=10000,rtf=0,terminal_delay=1)
        with self.assertRaisesRegex(TimeoutError,'clock_no_progress'):self.action(s)
        self.assertEqual(s.report['undock_timing']['terminal_status'],4)
        self.assertEqual(s.report['undock_timing']['trigger'],'clock_no_progress')
    def test_reject_and_abort_not_bypassed(self):
        s,h,c=self.setup_session(accepted=False)
        with self.assertRaisesRegex(RuntimeError,'拒绝目标'):self.action(s)
        s,h,c=self.setup_session(status=6)
        with self.assertRaisesRegex(RuntimeError,'状态 6'):self.action(s)
    def test_nav2_cannot_receive_dual_clock_policy(self):
        s,h,c=self.setup_session()
        with self.assertRaises(ValueError):s.action(None,'/follow_path',None,5,timing=UndockTiming())
    def test_generic_nav2_wall_budget_unchanged_with_slow_clock(self):
        s,h,c=self.setup_session(result_at=10000,rtf=.001)
        with self.assertRaisesRegex(TimeoutError,'预算耗尽'):s.action(None,'/follow_path',None,5)
        self.assertLess(c.wall,7);self.assertNotIn('undock_timing',s.report)
    def test_bounded_metadata_shape(self):
        s,h,c=self.setup_session(result_at=200,rtf=.05)
        self.action(s);data=s.report['undock_timing']
        self.assertGreater(data['samples'],100)
        self.assertLess(len(str(data)),2500)
        self.assertEqual(set(data['max_stream_gap_wall_s']),set(UndockTiming.required))
    def test_full_loop_keeps_overall_cap_and_only_changes_ready_cap(self):
        p=Path(__file__).parents[1]/'e2e/full_loop.py';text=p.read_text()
        self.assertIn('end = deadline(1200)',text)
        self.assertIn('return min(time.monotonic() + seconds, started + args.budget)',text)
        self.assertIn('default=1800',text)

    def test_invalid_initial_and_later_clock_evidence_is_strict_json(self):
        for bad in (float('nan'),float('inf'),float('-inf')):
            for initially_bad in (True,False):
                with self.subTest(bad=bad,initially_bad=initially_bad):
                    s,h,c=self.setup_session(result_at=10000)
                    s.sim_time=lambda:bad if initially_bad or c.wall>=2 else c.wall
                    with self.assertRaisesRegex(TimeoutError,'clock_invalid'):self.action(s)
                    data=s.report['undock_timing'];json.dumps(data,allow_nan=False)
                    self.assertEqual(data['trigger'],'clock_invalid')
                    self.assertIsNone(data['trigger_sim_s']);self.assertIsNone(data['finished_sim_s'])
                    self.assertTrue(data['invalid_clock_observed'])
                    if initially_bad:
                        self.assertIsNone(data['start_sim_s']);self.assertIsNone(data['total_elapsed_sim_s'])
                    else:self.assertIsNotNone(data['start_sim_s'])
    def test_invalid_origin_is_not_replaced_when_clock_recovers(self):
        p=UndockTiming();p.start(0,float('nan'))
        self.assertEqual(p.observe(1,10,{k:1 for k in p.required}),'clock_invalid')
        p.trigger('clock_invalid',1,10);p.finish(2,11,5)
        self.assertIsNone(p.data['start_sim_s']);self.assertIsNone(p.data['total_elapsed_sim_s'])
        json.dumps(p.data,allow_nan=False)
    def test_invalid_terminal_reading_does_not_poison_success_evidence(self):
        p=UndockTiming();p.start(0,0);p.observe(1,1,{k:1 for k in p.required})
        p.finish(2,float('inf'),4)
        self.assertIsNone(p.data['finished_sim_s']);self.assertIsNone(p.data['total_elapsed_sim_s'])
        self.assertFalse(p.data['finished_clock_valid']);self.assertIsNone(p.data['trigger'])
        json.dumps(p.data,allow_nan=False)

if __name__=='__main__':unittest.main()
