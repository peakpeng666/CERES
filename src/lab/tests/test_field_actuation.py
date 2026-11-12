import copy
import math
from xml.etree import ElementTree as ET

import pytest

from ceres_lab.field_actuation import JOINTS, JOINT_STATE_PLUGIN, WheelOdometry
from ceres_lab import field_physics, fieldworld
from ceres_lab.scenarios.validate import fleet_routing
from ceres_lab.solvers import fleet


def test_encoder_missing_data_cannot_create_fresh_odometry():
    enc = WheelOdometry(.15, .66)
    assert enc.update(1., [], []) is None
    assert enc.update(1., list(JOINTS), [0.]) is None
    assert enc.update(1., [JOINTS[0]] * 2, [0., 0.]) is None
    assert enc.update(0., list(JOINTS), [0., 0.]) is None
    assert enc.update(1., list(JOINTS), [float('nan'), 0.]) is None
    assert enc.t is None and enc.accepted == 0
    assert enc.update(1., list(JOINTS), [0., 0.]) is not None
    assert enc.update(.9, list(JOINTS), [1., 1.]) is None
    assert enc.update(1.4, list(JOINTS), [1., 1.]) is None
    assert enc.t == 1. and enc.pose == [0., 0., 0.]


def test_continuous_angles_and_exact_arc_not_wrapped_displacements():
    enc = WheelOdometry(.15, .66)
    enc.update(1., list(JOINTS), [10., 10.])
    sample = enc.update(1.2, list(JOINTS), [14., 14.])
    assert sample[:3] == pytest.approx((.6, 0., 0.))  # delta > pi is forward motion
    sample = enc.update(1.4, list(JOINTS), [14., 15.])
    yaw = .15 / .66
    assert sample[:3] == pytest.approx((.6 + .33 * math.sin(yaw), .33 * (1-math.cos(yaw)), yaw))


def test_torque_sdf_rejects_effort_pid_topic_and_feedback_tampering(tmp_path):
    inst = fleet_routing({'boundary': [[0, 0], [20, 0], [20, 20], [0, 20]],
        'depot': [3, 3], 'tasks': [{'id': 1, 'xy': [8, 8], 'demand': 1}],
        'robot_radius': .5, 'vehicles': {'count': 1, 'capacity': 1, 'speed_mps': 1, 'travel': 'free'}})
    routes, check, *_ = fleet.solve({}, inst, 'panel.sweep', {})
    assert check['valid']
    plan = fieldworld.build(inst, routes, tmp_path, 'force_audit', 4, {'actuator_mode': 'torque_limited'})
    source = ET.parse(plan['sdf'])
    for xpath, value in [
        ("joint[@name='left_wheel_joint']/axis/limit/effort", '800'),
        ("plugin[@name='gz::sim::systems::JointController']/topic", '/joint/left_wheel_joint/cmd_vel'),
        ("plugin[@name='gz::sim::systems::JointController']/cmd_max", '1000'),
        ("plugin[@name='gz::sim::systems::JointController']/p_gain", '40'),
        (f"plugin[@name='{JOINT_STATE_PLUGIN}']/joint_name", 'left_wheel_joint,right_wheel_joint'),
        (f"plugin[@name='{JOINT_STATE_PLUGIN}']/update_rate", '0'),
    ]:
        changed = copy.deepcopy(source)
        changed.find(".//model[@name='vehicle_1']/" + xpath).text = value
        changed.write(tmp_path / 'tampered.sdf')
        assert not field_physics.audit_sdf(tmp_path / 'tampered.sdf', plan['physics'])['valid'], xpath
