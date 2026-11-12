"""Gazebo Fortress world for a multi-vehicle field task.

The task map becomes a world whose frame equals the map frame (local metres): soil, plots with
crop rows along each plot's row direction (the same rows the planner uses, ``ceres_lab.lanes``),
obstacles by kind, the depot and a marker per task, coloured by the vehicle the plan assigned it to.
Every vehicle is a differential-drive robot built from the official Fortress ``diff_drive.sdf``
example (DiffDrive system), parked next to the depot.

Polygons are written as extruded binary STL meshes so both the Gazebo GUI and gzweb in the browser
can draw them; obstacle collisions use SDF polylines. When the plan travels along the lanes
(``vehicles.travel = 'rows'``) every crop row is solid too (boxes, polylines where a row is cut), so
the run shows whether the robots really stay between the rows; with free travel the rows are visual
only, because the planner treats plots as drivable.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from . import lanes
from .path_geometry import arc_lengths, locate
from .field_traffic import POLICY, PARKING_MARGIN_M
from . import field_physics
from . import field_nonideal
from .lanes import CROPS, DEFAULT_CROP  # noqa: F401 - re-exported for the field simulation layers

GRASS = (0.47, 0.63, 0.36, 1)
SOIL = (0.55, 0.45, 0.33, 1)
OBSTACLES = {  # kind → (height m, rgba, collides)
    'pond': (0.06, (0.27, 0.55, 0.86, 1), True),
    'ditch': (0.05, (0.33, 0.55, 0.74, 1), True),
    'building': (4.0, (0.74, 0.74, 0.76, 1), True),
    'greenhouse': (3.0, (0.86, 0.93, 0.95, 0.55), True),
    'rock': (0.7, (0.52, 0.5, 0.47, 1), True),
    'tree': (0.0, (0.2, 0.5, 0.24, 1), True),
}
# Robot: 0.8 × 0.56 m chassis plus spherical rolling contacts, overall width 0.96 m.
# Wheels on the centre line so it turns in place about its centre
# (circumscribed radius 0.49 m, inside the planner's robot radius).
WHEEL_R, WHEEL_SEP = field_physics.WHEEL_RADIUS, field_physics.WHEEL_SEPARATION
ROBOT_RADIUS = field_physics.RADIUS
MAX_SIM_VEHICLES = 15  # validated execution capacity, independent of collision masks
ROW_COLLISION_MIN_M = 0.5   # crop rows are at least this tall as obstacles


@dataclass
class Vehicle:
    name: str           # Gazebo model name, also the ROS namespace
    label: str          # what the plan calls it (① 号车 …)
    color: str          # hex from the plan
    start: tuple[float, float, float]
    path: list[list[float]]   # start → depot → planned polyline (incl. returns to the depot)
    tasks: list[dict]          # [{id, label, xy, service_s}] in visiting order


def _rgba(hexcolor: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    h = hexcolor.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (alpha,)  # type: ignore[return-value]


def _material(rgba) -> str:
    r, g, b, a = rgba
    return (f'<material><ambient>{r:.3f} {g:.3f} {b:.3f} {a:.3f}</ambient><diffuse>{r:.3f} {g:.3f} {b:.3f} {a:.3f}</diffuse>'
            f'<specular>0.05 0.05 0.05 1</specular></material>' + (f'<transparency>{1 - a:.2f}</transparency>' if a < 1 else ''))


_polys = lanes.polygons


def extrude(polys: list[Polygon], z0: float, z1: float) -> list[tuple]:
    """Triangles (a, b, c, normal) of extruded polygons: top face plus walls."""
    tris = []
    for poly in polys:
        poly = orient(poly.buffer(0), 1.0)
        for t in shapely.constrained_delaunay_triangles(poly).geoms:
            (x1, y1), (x2, y2), (x3, y3) = list(t.exterior.coords)[:3]
            if (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1) < 0:
                (x2, y2), (x3, y3) = (x3, y3), (x2, y2)
            tris.append(((x1, y1, z1), (x2, y2, z1), (x3, y3, z1), (0.0, 0.0, 1.0)))
        if z1 - z0 > 1e-6:
            for ring in [poly.exterior, *poly.interiors]:
                pts = list(ring.coords)
                for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                    dx, dy = bx - ax, by - ay
                    length = math.hypot(dx, dy)
                    if length < 1e-9:
                        continue
                    n = (dy / length, -dx / length, 0.0)
                    tris.append(((ax, ay, z0), (bx, by, z0), (bx, by, z1), n))
                    tris.append(((ax, ay, z0), (bx, by, z1), (ax, ay, z1), n))
    return tris


def write_stl(path: Path, polys: list[Polygon], z0: float, z1: float) -> int:
    """Binary STL: read by Gazebo and by gzweb over the WebSocket server (its OBJ path expects text)."""
    tris = extrude(polys, z0, z1)
    out = bytearray(b'generated field mesh'.ljust(80, b' '))
    out += struct.pack('<I', len(tris))
    for a, b, c, n in tris:
        out += struct.pack('<12fH', *n, *a, *b, *c, 0)
    path.write_bytes(bytes(out))
    return len(tris)


def row_collisions(rows: list[Polygon], height: float, prefix: str) -> list[str]:
    """SDF collisions for crop rows: a box per rectangular row, an extruded polyline where a row is cut."""
    out = []
    for i, row in enumerate(rows):
        rect = row.minimum_rotated_rectangle
        if rect.geom_type == 'Polygon' and rect.area - row.area <= 0.01 * rect.area:
            (x0, y0), (x1, y1), (x2, y2) = list(rect.exterior.coords)[:3]
            a, b = math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x1, y2 - y1)
            yaw = math.atan2(y1 - y0, x1 - x0) if a >= b else math.atan2(y2 - y1, x2 - x1)
            c = rect.centroid
            out.append(f"<collision name='{prefix}_{i}'><pose>{c.x:.3f} {c.y:.3f} {height / 2:.3f} 0 0 {yaw:.5f}</pose>"
                       f"<geometry><box><size>{max(a, b):.3f} {min(a, b):.3f} {height:.3f}</size></box></geometry></collision>")
        else:
            points = ''.join(f'<point>{x:.3f} {y:.3f}</point>' for x, y in list(orient(row, 1.0).exterior.coords)[:-1])
            out.append(f"<collision name='{prefix}_{i}'><geometry><polyline>{points}<height>{height:.3f}</height></polyline></geometry></collision>")
    return out


def _robot(v: Vehicle, index: int, speed: float, physics=None) -> str:
    physics = physics or field_physics.profile()
    x, y, yaw = v.start
    body = _rgba(v.color)
    bit = '0xffff'  # physical robot-robot contacts are enabled
    chassis_z = WHEEL_R + 0.1
    if physics['actuator_mode'] == 'torque_limited':
        eff = physics['wheel_torque_limit_nm']
        wheel_limits = (f'<lower>-1.79769e+308</lower><upper>1.79769e+308</upper>'
                        f'<effort>{eff}</effort><velocity>1e9</velocity>')
    else:
        wheel_limits = '<lower>-1.79769e+308</lower><upper>1.79769e+308</upper>'

    def inertia(part):
        p = physics[part]
        x, y, z = p['inertia_kgm2']
        return (f"<inertial><mass>{p['mass_kg']}</mass><inertia><ixx>{x:.12f}</ixx><ixy>0</ixy><ixz>0</ixz>"
                f"<iyy>{y:.12f}</iyy><iyz>0</iyz><izz>{z:.12f}</izz></inertia></inertial>")

    def contacts():
        return (f"<sensor name='contacts' type='contact'><contact><collision>collision</collision>"
                f"<topic>/model/{v.name}/contacts</topic></contact><always_on>1</always_on>"
                '<update_rate>20</update_rate></sensor>')

    def wheel(name, wy):
        if physics['wheel_contact'] == 'tyre':
            width = physics['tyre_width_m']
            geom = f"<cylinder><radius>{WHEEL_R}</radius><length>{width:.4f}</length></cylinder>"
        else:
            geom = f"<sphere><radius>{WHEEL_R}</radius></sphere>"
        return f"""
      <link name='{name}'>
        <pose>0 {wy} {WHEEL_R} {-math.pi / 2:.12f} 0 0</pose>
        {inertia('wheel')}
        <visual name='visual'><geometry>{geom}</geometry>{_material((0.12, 0.12, 0.13, 1))}</visual>
        <collision name='collision'><geometry>{geom}</geometry>
          <surface><friction><ode><mu>{physics['wheel_friction']}</mu><mu2>{physics.get('wheel_lateral_friction') or physics['wheel_friction']}</mu2></ode></friction>
          <contact><collide_bitmask>{bit}</collide_bitmask></contact></surface></collision>
        {contacts()}
      </link>"""

    def caster(name, cx):
        return f"""
      <link name='{name}'>
        <pose>{cx} 0 0.06 0 0 0</pose>
        {inertia('caster')}
        <visual name='visual'><geometry><sphere><radius>0.06</radius></sphere></geometry>{_material((0.2, 0.2, 0.2, 1))}</visual>
        <collision name='collision'><geometry><sphere><radius>0.06</radius></sphere></geometry>
          <surface><friction><ode><mu>0.01</mu><mu2>0.01</mu2></ode></friction><contact><collide_bitmask>{bit}</collide_bitmask></contact></surface></collision>
        {contacts()}
      </link>"""

    if physics['actuator_mode'] == 'torque_limited':
        from .field_actuation import plugins
        drive_plugins = plugins(v.name, physics)
    else:
        drive_plugins = f"""
      <plugin filename="ignition-gazebo-diff-drive-system" name="gz::sim::systems::DiffDrive">
        <left_joint>left_wheel_joint</left_joint>
        <right_joint>right_wheel_joint</right_joint>
        <wheel_separation>{WHEEL_SEP}</wheel_separation>
        <wheel_radius>{WHEEL_R}</wheel_radius>
        <odom_publish_frequency>20</odom_publish_frequency>
        <topic>/model/{v.name}/cmd_vel</topic>
        <odom_topic>/model/{v.name}/odometry</odom_topic>
        <frame_id>{v.name}/odom</frame_id>
        <child_frame_id>{v.name}/base_link</child_frame_id>
        <max_linear_acceleration>{physics['limits']['accel_mps2']}</max_linear_acceleration>
        <min_linear_acceleration>{-physics['limits']['decel_mps2']}</min_linear_acceleration>
        <max_angular_acceleration>{physics['limits']['yaw_accel_radps2']}</max_angular_acceleration>
        <min_angular_acceleration>{-physics['limits']['yaw_accel_radps2']}</min_angular_acceleration>
        <max_linear_velocity>{physics['limits']['max_speed_mps']}</max_linear_velocity>
        <min_linear_velocity>-0.5</min_linear_velocity>
        <max_angular_velocity>2.0</max_angular_velocity>
        <min_angular_velocity>-2.0</min_angular_velocity>
      </plugin>"""
    return f"""
    <model name='{v.name}'>
      <pose>{x:.3f} {y:.3f} 0.02 0 0 {yaw:.4f}</pose>
      <link name='chassis'>
        <pose>0 0 {chassis_z:.3f} 0 0 0</pose>
        {inertia('body')}
        <visual name='body'><geometry><box><size>0.8 0.56 0.22</size></box></geometry>{_material((0.93, 0.93, 0.92, 1))}</visual>
        <visual name='top'><pose>0 0 0.12 0 0 0</pose><geometry><box><size>0.7 0.5 0.03</size></box></geometry>{_material(body)}</visual>
        <visual name='mast'><pose>-0.25 0 0.45 0 0 0</pose><geometry><cylinder><radius>0.02</radius><length>0.7</length></cylinder></geometry>{_material((0.3, 0.3, 0.32, 1))}</visual>
        <visual name='beacon'><pose>-0.25 0 0.82 0 0 0</pose><geometry><sphere><radius>0.09</radius></sphere></geometry>{_material(body)}</visual>
        <visual name='front'><pose>0.41 0 0.02 0 0 0</pose><geometry><box><size>0.02 0.4 0.08</size></box></geometry>{_material((0.15, 0.15, 0.16, 1))}</visual>
        <collision name='collision'><geometry><box><size>0.8 0.56 0.22</size></box></geometry>
          <surface><contact><collide_bitmask>{bit}</collide_bitmask></contact></surface></collision>
        {contacts()}
      </link>{wheel('left_wheel', WHEEL_SEP / 2)}{wheel('right_wheel', -WHEEL_SEP / 2)}{caster('rear_caster', -0.3)}{caster('front_caster', 0.3)}
      <joint name='left_wheel_joint' type='revolute'><parent>chassis</parent><child>left_wheel</child>
        <axis><xyz>0 0 1</xyz><limit>{wheel_limits}</limit></axis></joint>
      <joint name='right_wheel_joint' type='revolute'><parent>chassis</parent><child>right_wheel</child>
        <axis><xyz>0 0 1</xyz><limit>{wheel_limits}</limit></axis></joint>
      <joint name='rear_caster_joint' type='ball'><parent>chassis</parent><child>rear_caster</child></joint>
      <joint name='front_caster_joint' type='ball'><parent>chassis</parent><child>front_caster</child></joint>
      {drive_plugins}
      <plugin filename="ignition-gazebo-pose-publisher-system" name="gz::sim::systems::PosePublisher">
        <publish_link_pose>false</publish_link_pose><publish_model_pose>true</publish_model_pose>
        <publish_nested_model_pose>true</publish_nested_model_pose><use_pose_vector_msg>true</use_pose_vector_msg>
        <update_frequency>{physics['truth_hz']}</update_frequency>
      </plugin>
    </model>"""


def plan_vehicles(inst: dict, routes: list[dict], net: lanes.Network | None = None) -> list[Vehicle]:
    """Parking spots next to the depot (clear of obstacles and crop rows), and each vehicle's drive path."""
    boundary = Polygon(inst['boundary'])
    net = net or lanes.Network(inst)
    solids = [Polygon(o['polygon']) for o in inst.get('obstacles', [])]
    if inst['vehicles'].get('travel') == 'rows':
        solids += [p.planted for p in net.plots]
    blocked = unary_union(solids)
    radius = max(0.7, inst['robot_radius'])
    free = boundary.buffer(-radius).difference(blocked.buffer(radius))
    depots = list(inst.get('depots') or [inst['depot']])
    clearance = 2 * ROBOT_RADIUS + PARKING_MARGIN_M
    missions = [LineString(r['polyline']) for r in routes]
    spots, approaches = [], []          # per vehicle, around its own home depot
    rings: dict[int, list] = {}

    def ring(home):
        if home not in rings:
            dx, dy = depots[home]
            rings[home] = sorted(((dx + 1.6 * i, dy + 1.6 * j) for d in range(0, 8)
                                  for i in range(-d, d + 1) for j in range(-d, d + 1)
                                  if max(abs(i), abs(j)) == d and (i, j) != (0, 0)),
                                 key=lambda c: (math.dist(c, (dx, dy)), c))
        return rings[home]

    for r in routes:
        home = r.get('depot', 0) if len(depots) > 1 else 0
        hx, hy = depots[home]
        spot = None
        for c in ring(home):
            approach = LineString([c, (hx, hy)])
            reachable = (boundary.covers(approach) and approach.distance(boundary.boundary) >= inst['robot_radius']
                         and all(approach.distance(o) >= inst['robot_radius'] for o in solids))
            # A waiting or finished robot must be clear of all mission paths and all other
            # parking approaches. Serialize motion, not physics; parked robots remain collidable.
            parking_clear = (all(p.distance(Point(c)) >= clearance for p in missions)
                             and all(a.distance(Point(c)) >= clearance for a in approaches)
                             and all(approach.distance(Point(s)) >= clearance for s in spots))
            if free.contains(Point(c)) and reachable and parking_clear:
                spot, _ = c, spots.append(c)
                approaches.append(approach)
                break
        if spot is None:
            raise ValueError('车库附近没有足够的安全停车位：须避开全部任务路线及其他车辆的出入通道')
    tasks = {t['id']: t for t in inst['tasks']}
    out = []
    for i, (r, spot) in enumerate(zip(routes, spots)):
        home = r.get('depot', 0) if len(depots) > 1 else 0
        poly = [list(map(float, p)) for p in r.get('polyline') or []]
        if not poly:
            raise ValueError('方案缺少路线几何')
        path = [list(spot), [float(depots[home][0]), float(depots[home][1])], *poly, list(spot)]
        clean = [path[0]]
        for p in path[1:]:
            if math.dist(p, clean[-1]) > 1e-3:
                clean.append(p)
        first = clean[1] if len(clean) > 1 else clean[0]
        yaw = math.atan2(first[1] - spot[1], first[0] - spot[0])
        order = [tasks[t] for t in r.get('sequence', []) if t in tasks]
        stops = {sv.get('task'): sv.get('stop') for sv in r.get('services') or [] if sv.get('stop')}
        # Keep the exact occurrence on paths that revisit the same place (including depot tasks).
        planned_s = arc_lengths(poly)
        home_xy = depots[r.get('depot', 0) if len(depots) > 1 else 0]
        approach_s = math.dist(spot, tuple(home_xy))
        cursor, offsets = approach_s, {}
        returns = {sum(len(t) for t in r['trips'][:i]): i - 1 for i in range(1, len(r['trips']))}
        reloads = r.get('reload_stops') or [home_xy] * len(returns)
        if len(reloads) != len(returns):
            raise ValueError('补给停靠清单与分趟清单不一致')
        for served, sv in enumerate(r['services'], 1):
            stop = stops.get(sv['task']) or tasks[sv['task']]['xy']
            pos = sv.get('position')
            at = approach_s + planned_s[pos] if pos is not None else locate(clean, stop, cursor)
            if at is None or at < cursor - 1e-5:
                raise ValueError('任务作业位置不在有序执行路径上')
            offsets[sv['task']] = cursor = at
            if served in returns:
                cursor = locate(clean, tuple(reloads[returns[served]]), cursor)
                if cursor is None:
                    raise ValueError('执行路径在分趟之间没有经过声明的补给点或家库')
        out.append(Vehicle(name=f'vehicle_{i + 1}', label=r.get('vehicle_name') or f'车{i + 1}', color=r.get('color') or '#0a84ff',
                           start=(spot[0], spot[1], yaw), path=clean,
                           tasks=[{'id': t['id'], 'label': str(t.get('label', t['id'])), 'xy': t['xy'],
                                   'stop': [float(c) for c in stops.get(t['id']) or t['xy']],
                                   'service_s': float(t.get('service_s') or 0), 'path_s': offsets[t['id']]} for t in order]))
    return out


def build(inst: dict, routes: list[dict], out_dir: Path, world: str, real_time_factor: float = 4.0,
          physics=None, execution=None, *, inherit_ground=True) -> dict:
    """Writes world.sdf and its meshes into out_dir; returns what the session needs to drive it."""
    if not routes or len(routes) > MAX_SIM_VEHICLES:
        raise ValueError(f'当前 Gazebo 差速模型验收范围为 1–{MAX_SIM_VEHICLES} 台执行车辆')
    if inst['robot_radius'] < ROBOT_RADIUS:
        raise ValueError(f'规划半径 {inst["robot_radius"]:g} m 小于当前仿真机器人外接圆半径 {ROBOT_RADIUS:.3f} m')
    out_dir.mkdir(parents=True, exist_ok=True)
    mesh_dir = out_dir / 'meshes'
    mesh_dir.mkdir(exist_ok=True)
    boundary = Polygon(inst['boundary'])
    minx, miny, maxx, maxy = boundary.bounds
    net = lanes.Network(inst)
    solid_rows = inst['vehicles'].get('travel') == 'rows'
    vehicles = plan_vehicles(inst, routes, net)
    hazards = [Polygon(o['polygon']) for o in inst.get('obstacles', [])] + (list(net.rows()) if solid_rows else [])
    speed = float(inst['vehicles'].get('speed_mps') or 1.0)
    from . import ground_execution
    if inherit_ground and execution is None and inst.get('ground'):
        execution = ground_execution.resolve(None, inst, routes)
    physical = field_physics.profile(physics)
    if not 0 < speed <= physical['limits']['max_speed_mps']:
        raise ValueError(f"规划车速超过当前实体模型范围 (0, {physical['limits']['max_speed_mps']}] m/s；通用规划不受此执行范围限制")
    triangles = 0
    visuals = []

    def mesh_visual(name, polys, z0, z1, rgba):
        nonlocal triangles
        if not polys:
            return
        path = mesh_dir / f'{name}.stl'
        triangles += write_stl(path, polys, z0, z1)
        visuals.append(f"<visual name='{name}'><geometry><mesh><uri>{escape(str(path))}</uri></mesh></geometry>{_material(rgba)}</visual>")

    mesh_visual('field', [boundary], 0.0, 0.02, SOIL)
    row_collisions_sdf = []
    for k, (plot, rows) in enumerate(zip(inst.get('plots', []), net.plots)):
        mesh_visual(f'plot_{k + 1}', _polys(Polygon(plot['polygon']).buffer(0)), 0.02, 0.035,
                    tuple(c * 0.82 for c in SOIL[:3]) + (1,))
        mesh_visual(f'rows_{k + 1}', rows.rows, 0.035, 0.035 + rows.height, rows.rgba)
        if solid_rows:
            row_collisions_sdf += row_collisions(rows.rows, max(rows.height, ROW_COLLISION_MIN_M), f'row_{k + 1}')

    obstacles = []
    for k, o in enumerate(inst.get('obstacles', [])):
        kind = o.get('kind', 'building')
        height, rgba, collides = OBSTACLES.get(kind, OBSTACLES['building'])
        poly = Polygon(o['polygon']).buffer(0)
        name = f'obstacle_{k + 1}'
        if kind == 'tree':
            c = poly.centroid
            radius = max(0.6, math.sqrt(poly.area / math.pi))
            # Collision follows the map polygon; the canopy is only a visual representation.
            points = ''.join(f'<point>{x - c.x:.6f} {y - c.y:.6f}</point>' for x, y in list(orient(poly, 1.0).exterior.coords)[:-1])
            obstacles.append(f"""
    <model name='{name}'><static>true</static><pose>{c.x:.3f} {c.y:.3f} 0 0 0 0</pose><link name='link'>
      <visual name='trunk'><pose>0 0 1 0 0 0</pose><geometry><cylinder><radius>{min(0.35, radius / 4):.2f}</radius><length>2</length></cylinder></geometry>{_material((0.42, 0.3, 0.2, 1))}</visual>
      <visual name='canopy'><pose>0 0 {2 + radius * 0.7:.2f} 0 0 0</pose><geometry><sphere><radius>{radius:.2f}</radius></sphere></geometry>{_material(rgba)}</visual>
      <collision name='collision'><geometry><polyline>{points}<height>2</height></polyline></geometry></collision>
    </link></model>""")
            continue
        path = mesh_dir / f'{name}.stl'
        triangles += write_stl(path, _polys(poly), 0.0, max(height, 0.04))
        ring = list(orient(poly, 1.0).exterior.coords)[:-1] if isinstance(poly, Polygon) else []
        collision = ''
        if collides and ring:
            points = ''.join(f'<point>{x:.3f} {y:.3f}</point>' for x, y in ring)
            collision = f"<collision name='collision'><geometry><polyline>{points}<height>{max(height, 0.5):.2f}</height></polyline></geometry></collision>"
        obstacles.append(f"""
    <model name='{name}'><static>true</static><link name='link'>
      <visual name='visual'><geometry><mesh><uri>{escape(str(path))}</uri></mesh></geometry>{_material(rgba)}</visual>{collision}
    </link></model>""")

    task_colors = {t['id']: v.color for v in vehicles for t in v.tasks}
    markers = []
    for t in inst['tasks']:
        x, y = t['xy']
        rgba = _rgba(task_colors.get(t['id'], '#8e8e93'))
        markers.append(f"<visual name='task_{escape(str(t['id']))}'><pose>{x:.3f} {y:.3f} 0.35 0 0 0</pose>"
                       f"<geometry><cylinder><radius>0.18</radius><length>0.7</length></cylinder></geometry>{_material(rgba)}</visual>")
    dx, dy = inst['depot']
    w, h = maxx - minx + 60, maxy - miny + 60
    crops = (f"""
    <model name="crop_rows"><static>true</static><link name="link">
      {''.join(row_collisions_sdf)}
    </link></model>""" if row_collisions_sdf else '')
    zones = (execution or {}).get('ground', {}).get('zones') or []
    ground_zones = (f"""
    <model name="ground_zones"><static>true</static><link name="link">
      {''.join(field_nonideal.zone_sdf(z, i) for i, z in enumerate(zones))}
    </link></model>""" if zones else '')
    ground_collision = (field_nonideal.ground_collisions(zones, boundary.bounds, physical['ground_friction']) if zones else
        f'<collision name="collision"><geometry><plane><normal>0 0 1</normal><size>{w:.1f} {h:.1f}</size></plane></geometry>'
        f'<surface><friction><ode><mu>{physical["ground_friction"]}</mu><mu2>{physical["ground_friction"]}</mu2></ode></friction></surface></collision>')
    sdf = f"""<?xml version="1.0"?>
<sdf version="1.8">
  <world name="{world}">
    <physics name="fast" type="ignored">
      <max_step_size>{physical['max_step_size_s']}</max_step_size>
      <dart><collision_detector>{physical['collision_detector']}</collision_detector><solver><solver_type>dantzig</solver_type></solver></dart>
      <real_time_factor>{real_time_factor:g}</real_time_factor>
    </physics>
    <gravity>{' '.join(map(str, physical['gravity_mps2']))}</gravity>
    <plugin filename="ignition-gazebo-physics-system" name="gz::sim::systems::Physics">
      <engine><filename>{physical['engine']}</filename></engine>
    </plugin>
    <plugin filename="ignition-gazebo-contact-system" name="gz::sim::systems::Contact"/>
    <plugin filename="ignition-gazebo-user-commands-system" name="gz::sim::systems::UserCommands"/>
    <plugin filename="ignition-gazebo-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
    <scene><ambient>0.72 0.72 0.72 1</ambient><background>0.8 0.87 0.94 1</background><shadows>false</shadows><grid>false</grid></scene>
    <light type="directional" name="sun">
      <cast_shadows>false</cast_shadows>
      <pose>0 0 80 0 0 0</pose>
      <diffuse>0.95 0.94 0.9 1</diffuse>
      <specular>0.2 0.2 0.2 1</specular>
      <direction>-0.35 0.25 -0.9</direction>
    </light>
    <model name="ground"><static>true</static><pose>{(minx + maxx) / 2:.9f} {(miny + maxy) / 2:.9f} -0.01 0 0 0</pose><link name="link">
      {ground_collision}
      <visual name="visual"><geometry><plane><normal>0 0 1</normal><size>{w:.1f} {h:.1f}</size></plane></geometry>{_material(GRASS)}</visual>
    </link></model>
    <model name="field"><static>true</static><link name="link">
      {''.join(visuals)}
    </link></model>
    <model name="depot"><static>true</static><pose>{dx:.3f} {dy:.3f} 0 0 0 0</pose><link name="link">
      <visual name="pad"><pose>0 0 0.03 0 0 0</pose><geometry><box><size>3 3 0.04</size></box></geometry>{_material((0.62, 0.62, 0.6, 1))}</visual>
      <visual name="pole"><pose>0 0 1.5 0 0 0</pose><geometry><cylinder><radius>0.04</radius><length>3</length></cylinder></geometry>{_material((0.3, 0.3, 0.32, 1))}</visual>
      <visual name="flag"><pose>0.3 0 2.75 0 0 0</pose><geometry><box><size>0.6 0.02 0.4</size></box></geometry>{_material((0.95, 0.75, 0.1, 1))}</visual>
    </link></model>
    <model name="tasks"><static>true</static><link name="link">
      {''.join(markers)}
    </link></model>{crops}{ground_zones}{''.join(obstacles)}{''.join(_robot(v, i, speed, physical) for i, v in enumerate(vehicles))}
  </world>
</sdf>
"""
    (out_dir / 'world.sdf').write_text(sdf)
    audit = field_physics.audit_sdf(out_dir / 'world.sdf', physical)
    if not audit['valid']:
        raise ValueError('生成的物理模型未通过独立审计：' + '；'.join(audit['errors']))
    zone_audit = field_nonideal.audit_ground_zones(out_dir / 'world.sdf', execution)
    if not zone_audit['valid']:
        raise ValueError('生成的地面分区未通过独立审计：' + '；'.join(zone_audit['errors']))
    references = {v.name: field_physics.timing_reference(v.path, speed, sum(t['service_s'] for t in v.tasks)) for v in vehicles}
    return {'world': world, 'sdf': str(out_dir / 'world.sdf'), 'triangles': triangles, 'speed_mps': speed,
            'real_time_factor': real_time_factor, 'bounds': [minx, miny, maxx, maxy], 'depot': [dx, dy],
            'travel': 'rows' if solid_rows else 'free', 'row_collision_shapes': len(row_collisions_sdf),
            'instance': inst, 'robot_radius_m': ROBOT_RADIUS,
            'physics': physical, 'physics_audit': audit,
            'execution': execution, 'ground_zones_audit': zone_audit,
            'execution_timing_reference': {'vehicles': references,
                'serial_makespan_s': sum(v['duration_s'] for v in references.values()) + max(0, len(vehicles)-1),
                'queue_release_s': 1, 'scope': 'full missions including parking; stop/turn/go reference plus serial release waits; not a prediction'},
            'traffic': {'policy': POLICY, 'robot_collisions': True, 'parking_margin_m': PARKING_MARGIN_M,
                        'scope': '整段任务互斥预约，完成后回独立车位；非并行多车路径规划',
                        'parking': {v.name: list(v.start[:2]) for v in vehicles}},
            'vehicles': [{'name': v.name, 'label': v.label, 'color': v.color, 'start': list(v.start), 'path': v.path,
                          'tasks': v.tasks, 'hazard_s': hazard_s(v.path, hazards, boundary),
                          **ground_execution.vehicle_fields(inst, routes[i], v.path, speed)}
                         for i, v in enumerate(vehicles)],
            'ground_knowledge': ground_execution.knowledge(inst, execution)}


def hazard_s(path, solids, boundary):
    """Arc lengths where the planned path runs within a hazard margin of obstacles, crop rows or the
    field boundary — computed here (shapely available) so the ROS-side tracker only handles numbers."""
    import shapely
    margin = ROBOT_RADIUS + 0.5
    union = shapely.union_all(solids) if len(solids) else None
    ring = boundary.boundary
    out, s = [], 0.0
    for a, b in zip(path, path[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(1, math.ceil(seg / 0.5))
        for k in range(steps + 1):
            u = min(1.0, k / steps)
            pt = shapely.points(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
            if ((union is not None and union.distance(pt) < margin) or ring.distance(pt) < margin) \
                    and (not out or s + u * seg - out[-1] > 0.4):
                out.append(round(s + u * seg, 3))
        s += seg
    return out
