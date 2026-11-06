"""The frozen field plan, executed in MuJoCo.

The plan, the vehicle's mass, inertia, geometry and friction, the velocity limiter, the control and truth rates, the path
follower and the mission queue are the ones the Gazebo session uses, so a difference between the two records comes from
the physics engines. Contact models differ by design: MuJoCo's soft convex contacts against DART's LCP. Only the ideal
configuration is modelled: declared ground zones, synthetic localization and anisotropic tyres raise ``Unsupported``
rather than being approximated.
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import Callable

from shapely.geometry import Polygon
from shapely.ops import triangulate

from . import lanes
from .field_energy import account
from .field_tracking import Driver
from .field_traffic import MissionQueue, PASSAGE_POLICY, PassageQueue

ENGINE = 'mujoco'
SCHEMA = 'urp.physics-run/1'
CONTROL_S = .05            # the session's control tick
SETTLE_S = 1.0             # the session waits for truth before release; the robot drops onto the plane meanwhile
# N·m·s/rad. Gazebo sets the joint velocity exactly; MuJoCo reaches it through a force, and its contact solver does not see
# the servo's implicit damping, so a stiff servo chatters on the contacts (roll and pitch appear from about 100 upward).
# 30 is a 9 ms time constant on the vehicle's inertia at the wheel. It is fixed here, not fitted per record.
SERVO_KV = 30.0
OBSTACLE_H = 1.0           # m: tall enough that nothing drives over a solid
ROW_H = .6
GROUND_Z = -.01            # fieldworld places its ground plane here
SPAWN_Z = .02              # and spawns the model here


class Unsupported(ValueError):
    """The plan asks for physics this adapter does not model."""


def supports(plan: dict) -> str | None:
    """Why this plan cannot run here, or None."""
    physics = plan.get('physics') or {}
    if plan.get('execution'):
        return '此方案使用非理想执行配置（地面分区或合成定位），MuJoCo 适配器只覆盖理想配置'
    if physics.get('wheel_lateral_friction') is not None:
        return 'MuJoCo 适配器不模拟各向异性轮胎摩擦'
    if not {'actuator_mode', 'wheel_contact', 'wheel', 'caster', 'truth_hz'} <= set(physics):
        return f"这条记录的物理模型 {physics.get('schema', '未记录')} 缺少完整参数，无法在 MuJoCo 中复现"
    return None


def _triangles(poly: Polygon) -> list[list[tuple[float, float]]]:
    out = []
    for tri in triangulate(poly):
        if poly.contains(tri.representative_point()):
            out.append([tuple(p) for p in tri.exterior.coords[:3]])
    return out


def _prism(name: str, tri: list[tuple[float, float]], height: float) -> str:
    verts = ' '.join(f'{x:.4f} {y:.4f} {z:.4f}' for z in (0, height) for x, y in tri)
    return f'<mesh name="{name}" vertex="{verts}"/>'


def _solids(plan: dict) -> tuple[list[str], list[str]]:
    inst = plan['instance']
    assets, geoms = [], []

    def add(prefix: str, poly: Polygon, height: float):
        for i, tri in enumerate(_triangles(poly)):
            name = f'{prefix}_{i}'
            assets.append(_prism(name, tri, height))
            geoms.append(f'<geom name="{name}" type="mesh" mesh="{name}" contype="2" conaffinity="1" condim="3"/>')

    for i, o in enumerate(inst.get('obstacles', [])):
        add(f'obstacle_{i}', Polygon(o['polygon']), OBSTACLE_H)
    if plan.get('travel') == 'rows':
        for i, row in enumerate(lanes.Network(inst).rows()):
            add(f'row_{i}', row, ROW_H)
    return assets, geoms


def _vehicle(v: dict, physics: dict) -> tuple[str, str]:
    x, y, yaw = v['start']
    name = v['name']
    wheel, caster, body = physics['wheel'], physics['caster'], physics['body']
    radius, sep = wheel['radius_m'], wheel['separation_m']
    chassis_z = radius + .1
    mu_wheel = min(physics['wheel_friction'], physics['ground_friction'])
    mu_caster = min(caster['friction'], physics['ground_friction'])

    def inertial(part):
        ix, iy, iz = part['inertia_kgm2']
        return f'<inertial pos="0 0 0" mass="{part["mass_kg"]}" diaginertia="{ix:.12f} {iy:.12f} {iz:.12f}"/>'

    if physics['wheel_contact'] == 'tyre':
        shape = f'type="cylinder" size="{radius} {physics["tyre_width_m"] / 2:.4f}" euler="{math.pi / 2:.12f} 0 0"'
    else:
        shape = f'type="sphere" size="{radius}"'

    def wheel_body(side, wy):
        return (f'<body name="{name}_{side}" pos="0 {wy} {radius - chassis_z}">{inertial(wheel)}'
                f'<joint name="{name}_{side}" type="hinge" axis="0 1 0"/>'
                f'<geom {shape} friction="{mu_wheel} 0 0" priority="1" condim="3" contype="1" conaffinity="3"/></body>')

    def caster_body(tag, cx):
        return (f'<body name="{name}_{tag}" pos="{cx} 0 {caster["radius_m"] - chassis_z}">{inertial(caster)}'
                f'<joint name="{name}_{tag}" type="ball"/>'
                f'<geom type="sphere" size="{caster["radius_m"]}" friction="{mu_caster} 0 0" priority="1" condim="3" contype="1" conaffinity="3"/></body>')

    sx, sy, sz = body['size_m']
    xml = (f'<body name="{name}" pos="{x:.4f} {y:.4f} {SPAWN_Z + chassis_z}" euler="0 0 {yaw:.6f}">'
           f'<freejoint name="{name}"/>{inertial(body)}'
           f'<geom type="box" size="{sx / 2} {sy / 2} {sz / 2}" contype="1" conaffinity="3"/>'
           f'{wheel_body("left", sep / 2)}{wheel_body("right", -sep / 2)}{caster_body("rear", -.3)}{caster_body("front", .3)}</body>')
    if physics['actuator_mode'] == 'torque_limited':
        tau, kv = physics['wheel_torque_limit_nm'], physics['wheel_torque_kp']
        limit = f'kv="{kv}" forcelimited="true" forcerange="{-tau} {tau}"'
    else:
        limit = f'kv="{SERVO_KV}"'
    act = ''.join(f'<velocity name="{name}_{side}" joint="{name}_{side}" {limit}/>' for side in ('left', 'right'))
    return xml, act


def _mujoco():
    """Imported on use: the gateway reads this module for ``supports`` and must not load the native library for that."""
    import mujoco
    return mujoco


def build_mjcf(plan: dict) -> str:
    physics = plan['physics']
    minx, miny, maxx, maxy = plan['bounds']
    assets, solids = _solids(plan)
    parts = [_vehicle(v, physics) for v in plan['vehicles']]
    gravity = ' '.join(str(g) for g in physics['gravity_mps2'])
    return f"""<mujoco model="{plan.get('world', 'field')}">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="{physics['max_step_size_s']}" gravity="{gravity}" integrator="implicitfast" cone="elliptic"/>
  <size memory="32M"/>
  <asset>{''.join(assets)}</asset>
  <worldbody>
    <geom name="ground" type="plane" pos="{(minx + maxx) / 2:.3f} {(miny + maxy) / 2:.3f} {GROUND_Z}" size="{(maxx - minx) / 2 + 30:.1f} {(maxy - miny) / 2 + 30:.1f} 1"
      friction="{physics['ground_friction']} 0 0" condim="3" contype="2" conaffinity="1"/>
    {''.join(solids)}
    {''.join(p[0] for p in parts)}
  </worldbody>
  <actuator>{''.join(p[1] for p in parts)}</actuator>
</mujoco>"""


class Limiter:
    """The DiffDrive plugin's speed limiter: clamp the acceleration, then the velocity, at every physics step."""

    def __init__(self, lo_v, hi_v, lo_a, hi_a):
        self.lo_v, self.hi_v, self.lo_a, self.hi_a = lo_v, hi_v, lo_a, hi_a
        self.value = 0.

    def step(self, target: float, dt: float) -> float:
        step = max(self.lo_a * dt, min(self.hi_a * dt, target - self.value))
        self.value = max(self.lo_v, min(self.hi_v, self.value + step))
        return self.value


class _Axle:
    def __init__(self, model, name, physics):
        limits = physics['limits']
        self.body = model.body(name).id
        self.free = model.jnt_qposadr[model.joint(name).id]
        self.left, self.right = model.actuator(f'{name}_left').id, model.actuator(f'{name}_right').id
        self.linear = Limiter(-.5, limits['max_speed_mps'], -limits['decel_mps2'], limits['accel_mps2'])
        self.angular = Limiter(-2., 2., -limits['yaw_accel_radps2'], limits['yaw_accel_radps2'])
        self.radius, self.half = physics['wheel']['radius_m'], physics['wheel']['separation_m'] / 2
        self.limited = physics['actuator_mode'] != 'torque_limited'

    def pose(self, data) -> tuple[float, float, float, float, float, float]:
        q = data.qpos[self.free:self.free + 7]
        w, x, y, z = q[3:7]
        roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
        pitch = math.asin(max(-1., min(1., 2 * (w * y - z * x))))
        yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        return float(q[0]), float(q[1]), float(q[2]), roll, pitch, yaw

    def drive(self, data, v: float, w: float, dt: float):
        if self.limited:
            v, w = self.linear.step(v, dt), self.angular.step(w, dt)
        data.ctrl[self.left] = (v - w * self.half) / self.radius
        data.ctrl[self.right] = (v + w * self.half) / self.radius


def _contact_names(model, data, vehicles: set[str]) -> list[list[str]]:
    """Vehicle-to-solid or vehicle-to-vehicle contacts of this step; the ground and a vehicle's own parts do not count."""
    out = []
    for i in range(data.ncon):
        c = data.contact[i]
        roots = []
        for g in (c.geom1, c.geom2):
            root = model.body(model.body_rootid[model.geom_bodyid[g]]).name
            roots.append(root if root in vehicles else model.geom(g).name)
        if roots[0] == roots[1] or 'ground' in roots:
            continue
        out.append(sorted(roots))
    return out


def execute(plan: dict, *, progress: Callable[[dict], None] | None = None, should_stop: Callable[[], bool] | None = None) -> dict:
    """Run the plan to its end and return a record shaped like the Gazebo session's."""
    reason = supports(plan)
    if reason:
        raise Unsupported(reason)
    mujoco = _mujoco()
    started = time.monotonic()
    physics = plan['physics']
    mjcf = build_mjcf(plan)
    model = mujoco.MjModel.from_xml_string(mjcf)
    data = mujoco.MjData(model)
    dt = float(model.opt.timestep)
    truth_every = max(1, round(1 / (physics['truth_hz'] * dt)))
    drivers = [Driver(v, float(plan['speed_mps'])) for v in plan['vehicles']]
    axles = {d.name: _Axle(model, d.name, physics) for d in drivers}
    names = {d.name for d in drivers}
    poses: dict[str, list] = {d.name: [] for d in drivers}
    traces: dict[str, list] = {d.name: [] for d in drivers}
    contacts: list[dict] = []
    seen_contact: set[tuple] = set()
    errors: list[str] = []
    state = 'running'

    def truth(t: float):
        for d in drivers:
            x, y, z, roll, pitch, yaw = axles[d.name].pose(data)
            if not all(map(math.isfinite, (x, y, yaw))):
                raise RuntimeError(f'{d.name} 的 MuJoCo 状态发散')
            d.observe((x, y, yaw), t, record_distance=execution_started)
            poses[d.name].append([round(t, 3), x, y, z, roll, pitch, yaw])

    execution_started = False
    steps = 0
    while steps * dt < SETTLE_S:
        mujoco.mj_step(model, data)
        steps += 1
        if steps % truth_every == 0:
            truth(steps * dt)
    t0 = steps * dt
    execution_started = True
    traffic = plan.get('traffic') or {}
    if traffic.get('policy') == PASSAGE_POLICY:
        queue = PassageQueue(drivers, plan['robot_radius_m'], t0, traffic['gates'])
    else:
        queue = MissionQueue(drivers, plan['robot_radius_m'], t0)
    planned = max(sum(v.get('planned_duration_s') or 0 for v in plan['vehicles']), 60)
    deadline = t0 + 2.5 * planned + 120
    last_ctrl = last_report = -1.
    terminal_since = None
    commands = {d.name: (0., 0.) for d in drivers}
    try:
        while True:
            mujoco.mj_step(model, data)
            steps += 1
            t = steps * dt
            if steps % truth_every == 0:
                truth(t)
            if t - last_ctrl >= CONTROL_S - 1e-9:
                last_ctrl = t
                queue.step(t)
                for d in drivers:
                    before = len(d.cte)
                    v, w = d.step(t)
                    d.v_cmd, d.w_cmd = v, w
                    commands[d.name] = (v, w)
                    traces[d.name].append([round(t, 3), d.s, d.cte[-1] if len(d.cte) > before else None, v, w, d.state])
                for pair in _contact_names(model, data, names):
                    key = tuple(pair)
                    if key not in seen_contact:
                        seen_contact.add(key)
                        contacts.append({'t_sim': round(t, 3), 'between': pair})
                if should_stop and should_stop():
                    state = 'canceled'
                    break
                if progress and t - last_report >= 5:
                    last_report = t
                    progress({'sim_time': round(t, 1), 'tasks_done': sum(1 for d in drivers for k in d.tasks if k['state'] == 'reached'),
                              'tasks_total': sum(len(d.tasks) for d in drivers)})
            for d in drivers:
                v, w = commands[d.name]
                axles[d.name].drive(data, v, w, dt)
            if all(d.state in ('done', 'failed') for d in drivers):
                terminal_since = t if terminal_since is None else terminal_since
                if t - terminal_since >= 1 and all(d.speed_now < .02 and d.angular_speed_now < .05 for d in drivers):
                    break
                if t - terminal_since > 5:
                    errors.append('终点发出停车命令后，实测速度未在 5 秒仿真时间内稳定')
                    break
            if t > deadline:
                errors.append(f'执行超时：仿真时间超过 {deadline:.0f} s')
                for d in drivers:
                    if d.state not in ('done', 'failed'):
                        d.state = 'failed'
                break
    except RuntimeError as exc:
        errors.append(str(exc))
    for d in drivers:
        if d.failure:
            errors.append(f'{d.label}: {d.failure}')
    if state != 'canceled':
        state = 'succeeded' if not errors and all(d.state == 'done' for d in drivers) else 'failed'
    return _record(plan, drivers, poses, traces, contacts, errors, state, t0, steps * dt, time.monotonic() - started, mjcf, queue)


def _record(plan, drivers, poses, traces, contacts, errors, state, t0, t_end, wall_s, mjcf, queue) -> dict:
    plan_of = {v['name']: v for v in plan['vehicles']}
    vehicles = [d.summary(plan_of[d.name]) for d in drivers]
    for row, d in zip(vehicles, drivers):
        row['speed'] = round(d.speed_now, 3)
    cte = [c for d in drivers for c in d.cte]
    energy = {d.name: account(poses[d.name], plan['physics'], plan['instance'].get('corridors')) for d in drivers}
    done = [d.t_done for d in drivers if d.t_done is not None]
    planned = plan.get('planned') or {}
    tasks = [{'id': t['id'], 'vehicle': d.name, 'state': t['state'], 't_sim': t['t_sim'], 'serviced_at': t.get('serviced_at'),
              'service_started_at': t.get('service_started_at'), 'distance_m': t['distance_m']} for d in drivers for t in d.tasks]
    metrics = {'tasks_total': len(tasks), 'tasks_reached': sum(t['state'] == 'reached' for t in tasks),
               'tasks_completed': sum(t['serviced_at'] is not None for t in tasks), 'vehicles_done': sum(d.state == 'done' for d in drivers),
               'makespan_s': round(max(done) - t0, 2) if done else None, 'planned_makespan_s': planned.get('makespan_s'),
               'total_distance_m': round(sum(d.distance for d in drivers), 2), 'planned_total_distance_m': planned.get('total_distance_m'),
               'cross_track_mean_m': round(sum(cte) / len(cte), 4) if cte else None, 'cross_track_max_m': round(max(cte), 4) if cte else None,
               'energy_wh': round(sum((e or {}).get('energy_wh', 0) for e in energy.values()), 3)}
    return {'schema': SCHEMA, 'engine': ENGINE, 'engine_version': _mujoco().__version__, 'state': state, 'errors': errors,
            'run_id': plan.get('run_id'), 'session_id': plan.get('session_id'), 'scenario_id': plan.get('scenario_id'),
            'algorithm': plan.get('algorithm'), 'world': plan.get('world'), 'sim_time': round(t_end, 3), 'wall_s': round(wall_s, 3),
            'real_time_factor': round(t_end / wall_s, 1) if wall_s else None, 'execution_started_sim_s': t0, 'metrics': metrics,
            'vehicles': vehicles, 'tasks': tasks, 'trails': {name: [[p[0], p[1], p[2], p[6]] for p in rows] for name, rows in poses.items()}, 'rigid_body_poses': poses,
            'rigid_body_pose_columns': ['t_sim', 'x_m', 'y_m', 'z_m', 'roll_rad', 'pitch_rad', 'yaw_rad'],
            'control_traces': traces, 'control_trace_columns': ['t_sim', 'path_s_m', 'cross_track_m', 'cmd_v_mps', 'cmd_w_radps', 'state'],
            'contacts': contacts, 'energy': energy, 'physics': plan['physics'],
            'traffic': {**{k: v for k, v in (plan.get('traffic') or {}).items() if k in ('policy', 'predicted_makespan_s', 'meetings', 'boxes', 'clearance_m')},
                        'events': queue.events, 'held_s': getattr(queue, 'held_s', None)},
            'model_sha256': hashlib.sha256(mjcf.encode()).hexdigest(), 'servo_kv': SERVO_KV,
            'scope': '与 Gazebo 同一方案、同一路径跟踪器、同一车辆参数；接触模型按引擎各自实现；未经实车标定'}
