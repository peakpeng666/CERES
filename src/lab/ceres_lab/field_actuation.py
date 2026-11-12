"""Force-actuated wheel configuration and encoder integration (stdlib only).

Gazebo JointStatePublisher accepts repeated joint_name elements, not a CSV list.
Commands are explicitly scoped to the vehicle; no default topic is relied on.
Continuous revolute positions must not be reduced modulo 2 pi before differencing.
"""
from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape

JOINT_STATE_LIBRARY = str(Path(__file__).resolve().parents[1] / 'runtime/native/joint_state/liburp_joint_state.so')
JOINT_STATE_PLUGIN = 'urp::systems::JointStatePublisher'
JOINTS = ('left_wheel_joint', 'right_wheel_joint')
ENCODER_SCHEMA = 'urp.continuous-wheel-odometry/1'
MAX_FEEDBACK_GAP_S = .25


def plugins(name, physics):
    tau, kp = physics['wheel_torque_limit_nm'], physics['wheel_torque_kp']
    controllers = ''.join(f'''
      <plugin filename="ignition-gazebo-joint-controller-system" name="gz::sim::systems::JointController">
        <joint_name>{joint}</joint_name><use_force_commands>true</use_force_commands>
        <topic>/model/{name}/joint/{joint}/cmd_vel</topic><initial_velocity>0</initial_velocity>
        <p_gain>{kp}</p_gain><i_gain>0</i_gain><d_gain>0</d_gain>
        <cmd_max>{tau}</cmd_max><cmd_min>{-tau}</cmd_min>
      </plugin>''' for joint in JOINTS)
    return controllers + f'''
      <plugin filename="{escape(JOINT_STATE_LIBRARY)}" name="{JOINT_STATE_PLUGIN}">
        <joint_name>left_wheel_joint</joint_name><joint_name>right_wheel_joint</joint_name>
        <topic>/model/{name}/joint_state</topic><update_rate>50</update_rate>
      </plugin>'''


def audit(model, physics):
    """Check both force bounds and all command/feedback routing in the frozen SDF."""
    errors = []
    name = model.get('name')
    tau, kp = physics['wheel_torque_limit_nm'], physics['wheel_torque_kp']
    allowed = {'gz::sim::systems::JointController': 'ignition-gazebo-joint-controller-system',
               JOINT_STATE_PLUGIN: JOINT_STATE_LIBRARY,
               'gz::sim::systems::PosePublisher': 'ignition-gazebo-pose-publisher-system'}
    if any(p.get('name') not in allowed or p.get('filename') != allowed[p.get('name')] for p in model.findall('plugin')):
        errors.append('力矩模型包含未声明的插件或插件库，不允许旁路驱动')
    controllers = model.findall("plugin[@name='gz::sim::systems::JointController']")
    if sorted(c.findtext('joint_name', '') for c in controllers) != list(JOINTS):
        errors.append('力矩模式要求恰好两个独立的左右轮 JointController')
    if model.find("plugin[@name='gz::sim::systems::DiffDrive']") is not None:
        errors.append('力矩模式禁止同时加载 DiffDrive 速度伺服')
    for joint in JOINTS:
        limit = model.find(f"joint[@name='{joint}']/axis/limit")
        if limit is None or float(limit.findtext('effort', 'nan')) != tau:
            errors.append(f'{joint} SDF effort 与冻结力矩上限不一致')
    for controller in controllers:
        joint = controller.findtext('joint_name')
        wanted = {'p_gain': kp, 'i_gain': 0., 'd_gain': 0.,
                  'cmd_max': tau, 'cmd_min': -tau, 'initial_velocity': 0.}
        if (controller.findtext('use_force_commands') != 'true' or
                controller.findtext('topic') != f'/model/{name}/joint/{joint}/cmd_vel' or
                any(float(controller.findtext(k, 'nan')) != v for k, v in wanted.items())):
            errors.append(f'{joint} force PID 参数、饱和范围或隔离话题与冻结配置不一致')
    pubs = model.findall(f"plugin[@name='{JOINT_STATE_PLUGIN}']")
    if (len(pubs) != 1 or sorted(p.text for p in pubs[0].findall('joint_name')) != list(JOINTS)
            or pubs[0].findtext('topic') != f'/model/{name}/joint_state'
            or pubs[0].findtext('update_rate') != '50'):
        errors.append('力矩模式缺少正确配置的独立双轮 JointStatePublisher')
    return errors


class WheelOdometry:
    """SE(2) integration of actual continuous wheel angles, never command targets.

    Missing/malformed feedback cannot refresh readiness. After a source gap larger
    than the evidence contract, fail closed rather than inventing the lost motion.
    The absolute encoder origin is arbitrary; the first complete sample sets it.
    """
    def __init__(self, radius, separation):
        self.radius, self.separation = radius, separation
        self.t, self.angles = None, None
        self.pose = [0., 0., 0.]
        self.omega = {'left_wheel': 0., 'right_wheel': 0.}
        self.accepted, self.rejected = 0, {}

    def update(self, stamp, names, positions):
        reason = None
        if not math.isfinite(stamp) or stamp <= 0:
            reason = 'invalid_source_stamp'
        elif self.t is not None and stamp <= self.t:
            reason = 'duplicate_or_stale'
        elif len(names) != len(positions) or any(names.count(j) != 1 for j in JOINTS):
            reason = 'missing_or_duplicate_wheel'
        elif any(not math.isfinite(float(p)) for p in positions):
            reason = 'nonfinite_position'
        elif self.t is not None and stamp - self.t > MAX_FEEDBACK_GAP_S:
            reason = 'source_gap'
        if reason:
            self.rejected[reason] = self.rejected.get(reason, 0) + 1
            return None
        angles = [float(positions[names.index(j)]) for j in JOINTS]
        v = w = 0.
        if self.t is not None:
            dt = stamp - self.t
            left, right = [self.radius * (a - b) for a, b in zip(angles, self.angles)]
            ds, dyaw = (left + right) / 2, (right - left) / self.separation
            x, y, yaw = self.pose
            # Exact constant-curvature increment; stable at zero angular velocity.
            half = dyaw / 2
            scale = math.sin(half) / half if abs(half) > 1e-9 else 1.
            self.pose = [x + ds * scale * math.cos(yaw + half),
                         y + ds * scale * math.sin(yaw + half),
                         (yaw + dyaw + math.pi) % (2 * math.pi) - math.pi]
            v, w = ds / dt, dyaw / dt
            self.omega = {'left_wheel': left / (self.radius * dt),
                          'right_wheel': right / (self.radius * dt)}
        self.t, self.angles = stamp, angles
        self.accepted += 1
        return (*self.pose, v, w)

    def report(self):
        return {'schema': ENCODER_SCHEMA, 'accepted': self.accepted, 'rejected': self.rejected,
                'last_source_time_s': self.t, 'max_feedback_gap_s': MAX_FEEDBACK_GAP_S,
                'position_semantics': 'unwrapped continuous revolute joint angle in rad; SE(2) wheel integration'}
