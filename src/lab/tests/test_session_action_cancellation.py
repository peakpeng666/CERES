"""ROS-free executable-method contract tests, not native simulation acceptance."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE=Path(os.environ['SESSION_SOURCE']) if 'SESSION_SOURCE' in os.environ else Path(__file__).parents[1]/'ros/urp_sim_session.py'
tree=ast.parse(SOURCE.read_text())
methods=[n for n in next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Session').body if isinstance(n,ast.FunctionDef) and n.name in ('wait','future','action','cancel_active')]

class Interrupted(Exception):pass
class Future:
    def __init__(self,clock,at,value):self.clock=clock;self.at=at;self.value=value
    def done(self):return self.clock.wall>=self.at
    def result(self):
        assert self.done(), 'read of incomplete future'
        return self.value
class Flag:
    def __init__(self,clock,at=float('inf')):self.clock=clock;self.at=at
    def is_set(self):return self.clock.wall>=self.at
    def set(self):self.at=self.clock.wall
class Clock:
    def __init__(self,rtf=1):self.wall=0.;self.rtf=rtf
    def monotonic(self):return self.wall
class Handle:
    def __init__(self,clock,result_at,status,accepted,ack_delay,ack_accept,terminal_delay):
        self.clock=clock;self.accepted=accepted;self.result_future=Future(clock,result_at,NS(status=status));self.cancel_calls=0
        self.ack_delay=ack_delay;self.ack_accept=ack_accept;self.terminal_delay=terminal_delay
    def get_result_async(self):return self.result_future
    def cancel_goal_async(self):
        self.cancel_calls+=1
        if self.terminal_delay is not None:
            self.result_future.at=min(self.result_future.at,self.clock.wall+self.terminal_delay)
        return Future(self.clock,self.clock.wall+self.ack_delay,NS(goals_canceling=[1] if self.ack_accept else []))

def session(*,result_at=2.,status=4,accepted=True,ack_delay=.5,ack_accept=True,terminal_delay=.75,stop_at=float('inf'),cancel_at=float('inf'),rtf=1,stale=False,terminal_processing=0.):
    clock=Clock(rtf)
    h=Handle(clock,result_at,status,accepted,ack_delay,ack_accept,terminal_delay)
    class Client:
        def __init__(self,*args):pass
        def server_is_ready(self):return True
        def send_goal_async(self,*args,**kw):return Future(clock,clock.wall,h)
    ns={'time':clock,'ActionClient':Client,'Interrupted':Interrupted}
    module=ast.Module(body=[ast.ClassDef(name='Session',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(SOURCE),'exec'),ns)
    s=ns['Session']();s.node=None;s.actions=[];s.goal=None;s.shutting=False;s.st={'errors':[]}
    s.stop=Flag(clock,stop_at);s.cancel=Flag(clock,cancel_at);s.execution={'nav2':[]}
    s.at={k:0. for k in ('clock','scan','odom','ground_truth')}
    s.sim_time=lambda:clock.wall*clock.rtf
    def spin(seconds=.05,allow_stop=False):
        clock.wall+=.25
        if s.stop.is_set() and not allow_stop:raise Interrupted('会话停止请求')
        if not stale:s.at={k:clock.wall for k in s.at}
    s.spin=spin
    original=h.result_future.result
    def result():
        value=original();clock.wall+=terminal_processing;return value
    h.result_future.result=result
    return s,h,clock

class CancellationContract(unittest.TestCase):
    def run_action(self,s,seconds=5,topic='/undock'):return s.action(None,topic,None,seconds)
    def test_normal(self):
        s,h,c=session();self.assertEqual(self.run_action(s).status,4);self.assertEqual(h.cancel_calls,0)
    def test_clock_policy_is_unchanged_for_slow_frozen_and_backward_clock(self):
        for rtf in (.01,0,-1):
            with self.subTest(rtf=rtf):
                s,h,c=session(result_at=100,rtf=rtf)
                with self.assertRaisesRegex(TimeoutError,'预算耗尽'):self.run_action(s)
                self.assertEqual(h.cancel_calls,1);self.assertLess(c.wall,7)
    def test_stop_waits_for_cancel_ack_and_terminal(self):
        s,h,c=session(result_at=100,stop_at=1)
        with self.assertRaises(Interrupted):self.run_action(s)
        self.assertEqual(h.cancel_calls,1);self.assertGreaterEqual(c.wall,1.75)
        self.assertIsNone(s.goal);self.assertTrue(s.execution['nav2'][0]['cancel_accepted'])
    def test_user_cancel_waits(self):
        s,h,c=session(result_at=100,cancel_at=1)
        with self.assertRaises(Interrupted):self.run_action(s)
        self.assertIsNone(s.goal)
    def test_empty_ack_then_terminal_race(self):
        s,h,c=session(result_at=100,ack_accept=False,terminal_delay=1)
        with self.assertRaisesRegex(TimeoutError,'预算耗尽'):self.run_action(s)
        self.assertIsNone(s.goal);self.assertFalse(s.execution['nav2'][0]['cancel_accepted'])
    def test_terminal_before_missing_ack(self):
        s,h,c=session(result_at=100,ack_delay=100,terminal_delay=.25)
        with self.assertRaisesRegex(TimeoutError,'预算耗尽'):self.run_action(s)
        self.assertLess(c.wall,7);self.assertIsNone(s.goal)
    def test_empty_ack_never_terminal(self):
        s,h,c=session(result_at=100,ack_accept=False,terminal_delay=None)
        with self.assertRaisesRegex(RuntimeError,'未确认取消'):self.run_action(s)
        self.assertIsNotNone(s.goal);self.assertLess(c.wall,19)
    def test_accepted_cancel_never_terminal(self):
        s,h,c=session(result_at=100,terminal_delay=None)
        with self.assertRaisesRegex(TimeoutError,'取消未收敛'):self.run_action(s)
        self.assertIsNotNone(s.goal);self.assertLess(c.wall,19)
    def test_ack_and_terminal_both_missing(self):
        s,h,c=session(result_at=100,ack_delay=100,terminal_delay=None)
        with self.assertRaisesRegex(TimeoutError,'ROS 响应超时'):self.run_action(s)
        self.assertLess(c.wall,14)
    def test_late_result_processing_does_not_invent_budget_expiry(self):
        s,h,c=session(result_at=2,terminal_processing=100)
        self.assertEqual(self.run_action(s).status,4);self.assertEqual(h.cancel_calls,0)
    def test_rejected(self):
        s,h,c=session(accepted=False)
        with self.assertRaisesRegex(RuntimeError,'拒绝目标'):self.run_action(s)
        self.assertEqual(h.cancel_calls,0)
    def test_aborted_not_reclassified_as_timeout(self):
        s,h,c=session(status=6,terminal_processing=100)
        with self.assertRaisesRegex(RuntimeError,'状态 6'):self.run_action(s)
    def test_nav2_stale_sensor_guard_preserved(self):
        for topic in ('/follow_path','/navigate_to_pose'):
            s,h,c=session(result_at=100,stale=True)
            with self.assertRaisesRegex(RuntimeError,'传感器/时钟数据超时'):self.run_action(s,topic=topic)
            self.assertEqual(h.cancel_calls,0);self.assertIsNotNone(s.goal)
    def test_undock_does_not_gain_unapproved_sensor_guard(self):
        s,h,c=session(result_at=4,stale=True)
        self.assertEqual(self.run_action(s).status,4)

    def test_cleanup_after_action_ack_timeout_remains_stop_safe(self):
        s,h,c=session(result_at=100,stop_at=1,ack_delay=100,terminal_delay=None)
        with self.assertRaisesRegex(TimeoutError,'ROS 响应超时'):self.run_action(s)
        self.assertIsNotNone(s.goal)
        h.ack_delay=.5;h.terminal_delay=1
        s.cancel_active()
        self.assertIsNone(s.goal);self.assertEqual(s.st['errors'],[])
    def test_cleanup_unsettled_goal_retained_and_stops_session(self):
        s,h,c=session(result_at=100,cancel_at=1,terminal_delay=None)
        with self.assertRaisesRegex(TimeoutError,'取消未收敛'):self.run_action(s)
        start=c.wall;s.cancel_active()
        self.assertIsNotNone(s.goal);self.assertTrue(s.stop.is_set())
        self.assertLess(c.wall-start,14);self.assertEqual(len(s.st['errors']),1)
    def test_cleanup_terminal_before_missing_ack(self):
        s,h,c=session(result_at=100,ack_delay=100,terminal_delay=.5)
        s.goal=(h,h.result_future);s.cancel_active()
        self.assertIsNone(s.goal);self.assertEqual(s.st['errors'],[])
        self.assertLess(c.wall,1)
    def test_cleanup_empty_ack_settles_terminal(self):
        s,h,c=session(result_at=100,ack_accept=False,terminal_delay=1)
        s.goal=(h,h.result_future);s.cancel_active()
        self.assertIsNone(s.goal);self.assertEqual(s.st['errors'],[])
    def test_cleanup_missing_ack_keeps_goal_and_requests_shutdown(self):
        s,h,c=session(result_at=100,ack_delay=100,terminal_delay=None)
        s.goal=(h,h.result_future);s.cancel_active()
        self.assertIsNotNone(s.goal);self.assertTrue(s.stop.is_set());self.assertLess(c.wall,6)
    def test_cleanup_already_terminal_needs_no_cancel(self):
        s,h,c=session(result_at=0,status=5)
        s.goal=(h,h.result_future);s.cancel_active()
        self.assertIsNone(s.goal);self.assertEqual(h.cancel_calls,0)
    def test_cleanup_nonterminal_result_is_not_proof_of_stop(self):
        s,h,c=session(result_at=0,status=2)
        s.goal=(h,h.result_future);s.cancel_active()
        self.assertIsNotNone(s.goal);self.assertTrue(s.stop.is_set())

    def test_shutdown_retry_can_clear_retained_goal(self):
        s,h,c=session(result_at=100,ack_delay=100,terminal_delay=None,rtf=0)
        s.goal=(h,h.result_future);s.cancel_active()
        self.assertIsNotNone(s.goal);self.assertTrue(s.stop.is_set())
        h.ack_delay=.25;h.terminal_delay=.5;s.shutting=True
        s.cancel_active()
        self.assertIsNone(s.goal);self.assertEqual(len(s.st['errors']),1)

if __name__=='__main__':unittest.main()
