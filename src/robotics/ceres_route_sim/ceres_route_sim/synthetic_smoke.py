"""Opt-in, bounded closed-loop verification, ONLY for the bundled simulator.

No input URL, route JSON, target topic, or hardware parameter is accepted.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
from rosgraph_msgs.msg import Clock
from .control_core import WAYPOINTS, command, yaw_from_quaternion
from .scene_core import number
from .runtime_core import FeedbackTracker


class Smoke(Node):
    def __init__(self, stream):
        super().__init__('ceres_synthetic_smoke')
        self.stream = stream
        self.pose = self.truth = None
        self.feedback = FeedbackTracker()
        self.count = self.feedback.count
        self.count['commands'] = 0
        self.max_displacement = self.max_truth_displacement = self.max_imu_yaw_rate = 0.0
        self.clock_first = self.clock_last = None
        self.error = None
        self.goal_index = 0
        self.pub = self.create_publisher(Twist, '/ceres_synthetic/cmd_vel', 10)
        self.create_subscription(Odometry, '/ceres_synthetic/odom', self.odom, 10)
        self.create_subscription(Odometry, '/ceres_synthetic/ground_truth', self.ground_truth, 10)
        self.create_subscription(Imu, '/ceres_synthetic/imu', self.imu, qos_profile_sensor_data)
        self.create_subscription(Clock, '/clock', self.clock, qos_profile_sensor_data)

    def record(self, kind, data):
        self.stream.write(json.dumps({'kind': kind, 'wall_monotonic': time.monotonic(), **data}, allow_nan=False)+'\n')

    def stamp(self, kind, stamp):
        if not self.feedback.observe(kind, stamp.sec, stamp.nanosec, time.monotonic()):
            return None
        value = stamp.sec + stamp.nanosec*1e-9
        return value

    def parse_pose(self, kind, msg, expected_frame, expected_child):
        if msg.header.frame_id != expected_frame or msg.child_frame_id != expected_child:
            raise ValueError(f'{kind} frame mismatch: {msg.header.frame_id} -> {msg.child_frame_id}')
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        pose = (number(p.x, 'x'), number(p.y, 'y'), yaw_from_quaternion(q.x,q.y,q.z,q.w))
        stamp = self.stamp(kind, msg.header.stamp)
        if stamp is None:
            return None
        self.record(kind, {'stamp': stamp, 'frame': msg.header.frame_id, 'child': msg.child_frame_id,
                           'x': pose[0], 'y': pose[1], 'yaw': pose[2]})
        return pose

    def odom(self, msg):
        try:
            pose = self.parse_pose('odom', msg, 'synthetic_odom', 'synthetic_base_footprint')
            if pose is None:
                return
            self.pose = pose
            self.max_displacement = max(self.max_displacement, math.hypot(*self.pose[:2]))
        except ValueError as exc:
            self.error = str(exc)

    def ground_truth(self, msg):
        try:
            pose = self.parse_pose('ground_truth', msg, 'synthetic_world', 'synthetic_body')
            if pose is None:
                return
            self.truth = pose
            self.max_truth_displacement = max(self.max_truth_displacement, math.hypot(*self.truth[:2]))
            if not (-1.0 <= self.truth[0] <= 3.0 and -0.8 <= self.truth[1] <= 0.8):
                self.error = 'ground truth left the bounded synthetic aisle'
        except ValueError as exc:
            self.error = str(exc)

    def imu(self, msg):
        try:
            fields = [msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z,
                      msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z]
            fields = [number(v, 'IMU') for v in fields]
            if msg.header.frame_id != 'synthetic_imu':
                raise ValueError(f'IMU frame mismatch: {msg.header.frame_id}')
            stamp = self.stamp('imu', msg.header.stamp)
            if stamp is None:
                return
            self.max_imu_yaw_rate = max(self.max_imu_yaw_rate, abs(fields[2]))
            self.record('imu', {'stamp': stamp,
                               'frame':msg.header.frame_id, 'angular_velocity':fields[:3],
                               'linear_acceleration':fields[3:]})
        except ValueError as exc:
            self.error = str(exc)

    def clock(self, msg):
        try:
            value = self.stamp('clock', msg.clock)
            if value is None:
                return
            if self.clock_first is None:
                self.clock_first = value
            self.clock_last = value
        except ValueError as exc:
            self.error = str(exc)

    def send(self, linear=0.0, angular=0.0):
        msg = Twist()
        msg.linear.x, msg.angular.z = linear, angular
        self.pub.publish(msg)
        self.count['commands'] += 1
        self.record('command', {'linear_m_s': linear, 'angular_rad_s': angular, 'waypoint': self.goal_index})

    def ready(self):
        return self.feedback.ready()

    def fresh(self):
        return self.feedback.fresh(time.monotonic())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--timeout', type=float, default=65.0)
    args = parser.parse_args()
    if (os.environ.get('CERES_ALLOW_SYNTHETIC_COMMANDS') != '1' or
            os.environ.get('ROS_LOCALHOST_ONLY') != '1' or
            os.environ.get('ROS_DOMAIN_ID') != '71' or
            not os.environ.get('IGN_PARTITION','').startswith('ceres_synthetic_') or
            os.environ.get('IGN_IP') != '127.0.0.1'):
        parser.error('run through scripts/run_smoke.sh; isolated synthetic-only environment required')
    if not 10 <= args.timeout <= 90:
        parser.error('timeout must be 10..90 seconds')
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in ('telemetry.jsonl', 'smoke_result.json')):
        parser.error('refusing to overwrite existing telemetry or result; use a new evidence directory')
    rclpy.init(args=[])
    success = False
    reason = 'wall timeout'
    start = time.monotonic()
    with (out/'telemetry.jsonl').open('x', encoding='utf-8') as stream:
        node = Smoke(stream)
        try:
            while time.monotonic()-start < args.timeout:
                rclpy.spin_once(node, timeout_sec=0.05)
                if node.error:
                    reason = node.error
                    break
                if not node.ready():
                    node.send()
                    if time.monotonic()-start > 15:
                        reason = 'missing odom, physical ground truth, IMU, or advancing clock'
                        break
                    continue
                if not node.fresh():
                    reason = 'stale feedback; stopped'
                    break
                if node.goal_index == len(WAYPOINTS):
                    reason = 'all synthetic aisle waypoints reached'
                    success = True
                    break
                linear, angular, reached = command(*node.pose, WAYPOINTS[node.goal_index])
                if reached:
                    node.goal_index += 1
                    node.record('waypoint_reached', {'index': node.goal_index-1})
                node.send(linear, angular)
                # Rate cap is wall-clock independent of incoming message frequency.
                time.sleep(0.04)
        except KeyboardInterrupt:
            reason = 'interrupted'
        except Exception as exc:
            reason = f'controller exception ({type(exc).__name__}): {exc}'
            node.error = reason
        finally:
            for _ in range(8):
                try:
                    node.send()
                    rclpy.spin_once(node, timeout_sec=0.05)
                except Exception as exc:
                    node.error = f'stop/feedback exception ({type(exc).__name__}): {exc}'
                    break
            final_error = math.hypot(*node.pose[:2]) if node.pose else None
            truth_error = math.hypot(*node.truth[:2]) if node.truth else None
            checks = {
                'waypoints_completed': node.goal_index == len(WAYPOINTS),
                'all_feedback_counts': all(node.count[k] >= 10 for k in ('odom','ground_truth','imu','clock')),
                'clock_advanced': node.clock_first is not None and node.clock_last-node.clock_first > 1,
                'wheel_motion_observed': node.max_displacement > 1.7,
                'physical_motion_observed': node.max_truth_displacement > 1.7,
                'imu_turn_observed': node.max_imu_yaw_rate > 0.2,
                'wheel_return_error': final_error is not None and final_error <= 0.15,
                'physical_return_error': truth_error is not None and truth_error <= 0.25,
                'feedback_fresh_at_end': node.fresh(),
                'no_validation_error': node.error is None,
            }
            success = success and all(checks.values())
            failed_checks = [name for name, passed in checks.items() if not passed]
            if not success and failed_checks:
                reason += '; failed checks: ' + ', '.join(failed_checks)
            report = {'scope': 'SYNTHETIC_GREENHOUSE_ONLY', 'runtime_executed': True,
                      'success': success, 'reason': reason, 'checks': checks, 'failed_checks': failed_checks, 'counts':node.count,
                      'feedback_received_counts': node.feedback.received,
                      'waypoints_m': WAYPOINTS, 'completed_waypoints':node.goal_index,
                      'wall_seconds':time.monotonic()-start, 'odom_return_error_m':final_error,
                      'ground_truth_return_error_m':truth_error,
                      'max_odom_displacement_m':node.max_displacement,
                      'max_physical_displacement_m':node.max_truth_displacement,
                      'max_imu_yaw_rate_rad_s':node.max_imu_yaw_rate,
                      'limitations':['No Nav2, localization, obstacle avoidance or benchmark task validity demonstrated.']}
            try:
                with (out/'smoke_result.json').open('x', encoding='utf-8') as result_stream:
                    json.dump(report, result_stream, indent=2, allow_nan=False)
                    result_stream.write('\n')
            finally:
                node.destroy_node()
                rclpy.shutdown()
    print(json.dumps({'success':success, 'reason':reason, 'report':str(out/'smoke_result.json')}))
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
