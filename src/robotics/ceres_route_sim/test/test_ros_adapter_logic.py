"""Exercise actual callback code with message doubles; no ROS runtime is used."""
import importlib.util
import io
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


class NodeDouble:
    def __init__(self, name):
        pass

    def create_publisher(self, *args):
        return SimpleNamespace(publish=lambda message: None)

    def create_subscription(self, *args):
        return None


def module_with(**attrs):
    module = ModuleType('offline_ros_test_double')
    module.__dict__.update(attrs)
    return module


def load_callbacks():
    doubles = {
        'rclpy': module_with(), 'rclpy.node': module_with(Node=NodeDouble),
        'rclpy.qos': module_with(qos_profile_sensor_data=object()),
        'geometry_msgs': module_with(), 'geometry_msgs.msg': module_with(Twist=object),
        'nav_msgs': module_with(), 'nav_msgs.msg': module_with(Odometry=object),
        'sensor_msgs': module_with(), 'sensor_msgs.msg': module_with(Imu=object),
        'rosgraph_msgs': module_with(), 'rosgraph_msgs.msg': module_with(Clock=object),
    }
    path = Path(__file__).resolve().parents[1] / 'ceres_route_sim' / 'synthetic_smoke.py'
    spec = importlib.util.spec_from_file_location('ceres_route_sim.callback_unit_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict('sys.modules', doubles):
        spec.loader.exec_module(module)
    return module


def stamp(nanosec):
    return SimpleNamespace(sec=0, nanosec=nanosec)


def odom(frame, child, nanosec, x=0.0):
    return SimpleNamespace(
        header=SimpleNamespace(frame_id=frame, stamp=stamp(nanosec)), child_frame_id=child,
        pose=SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=x, y=0.0),
                            orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0))))


def imu(nanosec, frame='synthetic_imu'):
    vector = SimpleNamespace(x=0.0, y=0.0, z=0.0)
    return SimpleNamespace(header=SimpleNamespace(frame_id=frame, stamp=stamp(nanosec)),
                           angular_velocity=vector, linear_acceleration=vector)


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.module = load_callbacks()
        self.node = self.module.Smoke(io.StringIO())

    def test_duplicate_odometry_does_not_replace_accepted_pose(self):
        self.node.odom(odom('synthetic_odom', 'synthetic_base_footprint', 100, x=0.0))
        self.node.odom(odom('synthetic_odom', 'synthetic_base_footprint', 100, x=2.0))
        self.assertEqual(self.node.pose, (0.0, 0.0, 0.0))
        self.assertEqual(self.node.max_displacement, 0.0)
        self.assertEqual(self.node.count['odom'], 1)
        self.assertEqual(self.node.feedback.received['odom'], 2)

    def test_actual_callbacks_require_progress_to_remain_fresh(self):
        for index in range(5):
            with patch.object(self.module.time, 'monotonic', return_value=float(index)):
                self.node.odom(odom('synthetic_odom', 'synthetic_base_footprint', index))
                self.node.ground_truth(odom('synthetic_world', 'synthetic_body', index))
                self.node.imu(imu(index))
                self.node.clock(SimpleNamespace(clock=stamp(index)))
        self.assertTrue(self.node.ready())
        with patch.object(self.module.time, 'monotonic', return_value=9.0):
            self.node.odom(odom('synthetic_odom', 'synthetic_base_footprint', 4))
            self.node.ground_truth(odom('synthetic_world', 'synthetic_body', 4))
            self.node.imu(imu(4))
            self.node.clock(SimpleNamespace(clock=stamp(4)))
            self.assertFalse(self.node.fresh())

    def test_backward_clock_is_a_recorded_validation_failure(self):
        self.node.clock(SimpleNamespace(clock=stamp(100)))
        self.node.clock(SimpleNamespace(clock=stamp(99)))
        self.assertIn('clock timestamp went backwards', self.node.error)
        self.assertAlmostEqual(self.node.clock_last, 100e-9, places=16)

    def test_nonempty_but_wrong_imu_frame_fails(self):
        self.node.imu(imu(100, 'other_robot/imu'))
        self.assertIn('IMU frame mismatch', self.node.error)
        self.assertEqual(self.node.count['imu'], 0)


if __name__ == '__main__':
    unittest.main()
