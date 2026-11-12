"""Three separate things a vehicle asset can be, each with its own checklist:

display     a model that can be drawn (2D icon / 3D mesh), with orientation and scale confirmed;
planning    footprint, capacity, speed, motion model (Nav2 naming: differential / omni / ackermann …),
            minimum turning radius where the vehicle cannot turn in place, and working width for coverage;
simulation  collision geometry, inertia, a drive controller, a ROS 2 interface (cmd_vel / odom or
            ros2_control) and an execution adapter in this workbench's simulation stack.
A model that loads is only a display asset; nothing is marked simulatable unless all items hold.
"""
from __future__ import annotations

import math
from ..field_physics import FOOTPRINT, MODEL

KINEMATICS = {
    'differential': ('差速', True), 'skid_steer': ('滑移转向', True), 'tracked': ('履带', True), 'omni': ('全向', True),
    'ackermann': ('阿克曼转向', False), 'articulated': ('铰接转向', False),
}
ADAPTERS = {
    'fieldsim-diffdrive': {'label': '工作台田间差速机器人（Gazebo DiffDrive · ROS 2 cmd_vel / odom · 真值纯追踪）',
                           'problem_classes': ['fleet_routing']},
    'tb4-nav2': {'label': 'TurtleBot4 + Nav2 会话（AMCL 或真值定位）', 'problem_classes': ['turn_routing', 'point_to_point', 'coverage', 'euclidean_cvrp'],
                 'worlds': ['maze', 'warehouse', 'depot']},
}
BUILTIN_VEHICLES = {
    'vehicle-field-diffdrive-builtin': {
        'name': '田间差速机器人（工作台内置）', 'subtype': 'builtin', 'category': '移动平台',
        'meta': {
            'display': {'builtin': 'field-diffdrive', 'size_m': {'length_m': 0.8, 'width_m': 0.96, 'height_m': 1.15}, 'confirmed': True},
            'planning': {'footprint': {'type': 'polygon', 'points': FOOTPRINT}, 'capacity': 12, 'speed_mps': 1.0,
                         'kinematics': 'differential', 'min_turn_radius_m': 0.0, 'work_width_m': None},
            'simulation': {'adapter': 'fieldsim-diffdrive', 'collision': 'box 0.8×0.56×0.22 + 球面滚动轮（半径 0.15 m；差速无滑移参考）+ 球万向轮', 'inertial': True,
                           'model': MODEL, 'drive': '默认 Gazebo DiffDrive 速度伺服；可选双轮 force PID 有界力矩（轮距 0.66 m、轮半径 0.15 m；源时间关节里程计）', 'ros': '/vehicle_i/cmd_vel, /vehicle_i/odom, TF map→vehicle_i/base_link',
                           'sensors': [], 'localization': 'Gazebo 真值',
                           'energy': '真值轨迹后验能耗/力矩核算（滚动阻力 0.06、传动效率 0.8；非电池/电机模型）'},
        },
        'source': {'type': 'builtin', 'uri': 'src/lab/ceres_lab/fieldworld.py', 'license': 'Project', 'attribution': '本项目'},
    },
    'vehicle-turtlebot4-builtin': {
        'name': 'TurtleBot4 标准版（ROS 2 官方描述）', 'subtype': 'builtin', 'category': '移动平台',
        'meta': {
            'display': {'builtin': 'turtlebot4', 'urdf': 'ros/robots/turtlebot4.urdf', 'size_m': {'length_m': 0.342, 'width_m': 0.339, 'height_m': 0.351}, 'confirmed': True},
            'planning': {'footprint': {'type': 'circle', 'radius_m': 0.175}, 'capacity': None, 'payload': 'none', 'speed_mps': 0.31,
                         'kinematics': 'differential', 'min_turn_radius_m': 0.0, 'work_width_m': None},
            'simulation': {'adapter': 'tb4-nav2', 'collision': 'URDF 碰撞网格', 'inertial': True, 'drive': 'Create 3 差速驱动（ign 插件）',
                           'ros': '/cmd_vel, /odom, /scan, TF, Nav2 动作', 'sensors': ['lidar', 'camera', 'imu'], 'localization': 'AMCL 或 Gazebo 真值'},
        },
        'source': {'type': 'builtin', 'uri': 'package://turtlebot4_description', 'license': 'Apache-2.0', 'attribution': 'Clearpath Robotics / iRobot'},
    },
}


def _item(key, label, ok, value=None, detail=''):
    return {'key': key, 'label': label, 'ok': bool(ok), 'value': value, 'detail': detail}


def footprint_radius(fp: dict | None) -> float | None:
    if not fp:
        return None
    if fp.get('type') == 'circle' and fp.get('radius_m'):
        return float(fp['radius_m'])
    if fp.get('type') == 'rect' and fp.get('length_m') and fp.get('width_m'):
        return math.hypot(float(fp['length_m']) / 2, float(fp['width_m']) / 2)
    if fp.get('type') == 'polygon' and fp.get('points'):
        return max(math.hypot(x, y) for x, y in fp['points'])
    return None


def planning_items(p: dict | None) -> list[dict]:
    p = p or {}
    fp = p.get('footprint') or {}
    r = footprint_radius(fp)
    kin = p.get('kinematics')
    turn_free = KINEMATICS.get(kin, ('', True))[1]
    items = [
        _item('footprint', '足迹 / 尺寸', r is not None and r > 0,
              ({'rect': f"{fp.get('length_m')}×{fp.get('width_m')} m", 'circle': f"半径 {fp.get('radius_m')} m"}.get(fp.get('type'), '多边形') if r else None),
              '' if r else '需要长×宽、半径或多边形'),
        (_item('capacity', '容量', True, '不载货（巡检 / 采样）', '不设载重约束：每趟可服务任意多个任务点')
         if p.get('payload') == 'none' else
         _item('capacity', '容量（每趟可承载的需求量）', isinstance(p.get('capacity'), (int, float)) and p['capacity'] >= 1, p.get('capacity'),
               '' if p.get('capacity') else '运输任务需要；巡检 / 采样车可标为不载货')),
        _item('speed', '行驶速度', isinstance(p.get('speed_mps'), (int, float)) and 0 < p['speed_mps'] <= 15, p.get('speed_mps'), 'm/s'),
        _item('kinematics', '运动学类型', kin in KINEMATICS, KINEMATICS.get(kin, ('',))[0] or None, '差速 / 履带 / 全向可原地转向；阿克曼 / 铰接有最小转弯半径'),
    ]
    if not turn_free:
        mr = p.get('min_turn_radius_m')
        items.append(_item('min_turn_radius', '最小转弯半径', isinstance(mr, (int, float)) and mr > 0, mr, '阿克曼 / 铰接车辆必须给出'))
    items.append(_item('work_width', '作业幅宽（覆盖作业用）', isinstance(p.get('work_width_m'), (int, float)) and p['work_width_m'] > 0,
                       p.get('work_width_m'), '仅覆盖作业需要'))
    return items


def solver_mapping(p: dict | None) -> dict:
    """What the existing planners actually consume, and what they ignore (shown, never faked)."""
    p = p or {}
    r = footprint_radius(p.get('footprint'))
    kin = p.get('kinematics')
    up = (lambda x: math.ceil(x * 1000 - 1e-9) / 1000) if r else None   # never below the body: round up to the mm
    out = {'fleet_routing': {'robot_radius': up(r) if r else None, 'capacity': p.get('capacity'), 'payload': p.get('payload') or 'load',
                             'speed_mps': p.get('speed_mps'), 'min_turn_radius_m': p.get('min_turn_radius_m') or 0, 'ignored': []},
           'coverage': {'robot_radius': up(r) if r else None, 'swath_width': p.get('work_width_m'),
                        'turn_radius': p.get('min_turn_radius_m') or None, 'ignored': []},
           'point_to_point': {'motion_model': 'dubins' if kin in ('ackermann', 'articulated') else 'holonomic',
                              'turning_radius': p.get('min_turn_radius_m') or None, 'ignored': []}}
    if p.get('footprint', {}).get('type') in ('rect', 'polygon'):
        out['fleet_routing']['ignored'].append('多车路径按足迹外接圆膨胀障碍（矩形 / 多边形朝向未参与碰撞检查）')
    if kin in ('ackermann', 'articulated') and not p.get('min_turn_radius_m'):
        out['fleet_routing']['ignored'].append('阿克曼 / 铰接车型必须给出最小转弯半径，否则曲率约束不参与多车路径')
    return out


def simulation_items(meta: dict, inspection: dict | None) -> list[dict]:
    sim = meta.get('simulation') or {}
    ins = inspection or {}
    adapter = sim.get('adapter')
    collision = bool(sim.get('collision')) or bool(ins.get('collision_links'))
    inertial = bool(sim.get('inertial')) or bool(ins.get('inertial_links'))
    drive = sim.get('drive') or ins.get('drive')
    ros = sim.get('ros') or (('ros2_control' if ins.get('ros2_control') else None) or (ins.get('drive_source') if ins.get('drive') else None))
    return [
        _item('collision', '碰撞几何', collision, sim.get('collision') or (f"{len(ins.get('collision_links') or [])} 个 link 有 <collision>" if collision else None),
              '' if collision else '展示网格不能当碰撞体：需要 URDF/SDF 的 <collision>'),
        _item('inertial', '惯性 / 动力学参数', inertial, None, '' if inertial else '需要每个 link 的 <inertial>'),
        _item('drive', '驱动控制器', bool(drive), drive, '' if drive else '需要 DiffDrive / AckermannSteering 插件或 ros2_control'),
        _item('ros', 'ROS 2 接口', bool(ros), ros, '' if ros else '需要 cmd_vel / odom 或 ros2_control 控制器'),
        _item('sensors', '传感器', True, ', '.join(sim.get('sensors') or ins.get('sensors') or []) or '无', '纯追踪执行用 Gazebo 真值，不要求传感器；Nav2 定位需要激光'),
        _item('adapter', '工作台仿真适配', adapter in ADAPTERS, ADAPTERS.get(adapter, {}).get('label'),
              '' if adapter in ADAPTERS else '工作台当前只能执行内置田间差速机器人与 TurtleBot4；此车型需要后端提供生成 / 控制适配'),
    ]


def adapter_options(manifest: dict, ver: dict) -> list[dict]:
    """Execution adapters that really spawn this vehicle; a vehicle never borrows another model's simulation."""
    uri = str((ver.get('source') or {}).get('uri') or '')
    aid = manifest.get('id')
    out = []
    for key, spec in ADAPTERS.items():
        if key == 'fieldsim-diffdrive':
            ok = aid == 'vehicle-field-diffdrive-builtin'
            why = '' if ok else '田间仿真只生成内置差速机器人（0.8×0.56 m 车体）；导入车型的外观、尺寸与动力学不会进入仿真，需要后端按资产生成车辆'
        else:
            ok = aid == 'vehicle-turtlebot4-builtin' or uri.startswith('ros:turtlebot4_description/urdf/standard/')
            why = '' if ok else 'TB4 会话只生成 TurtleBot4 标准版'
        out.append({'id': key, 'label': spec['label'], 'ok': ok, 'reason': why, 'problem_classes': spec['problem_classes']})
    return out


def check_adapter(manifest: dict, ver: dict, adapter: str | None) -> str | None:
    """None when the adapter may be set on this version, else the reason it may not."""
    if adapter in (None, ''):
        return None
    for o in adapter_options(manifest, ver):
        if o['id'] == adapter:
            return None if o['ok'] else o['reason']
    return f'未知的仿真适配 {adapter}'


def capabilities(manifest: dict, ver: dict) -> dict:
    kind, meta = manifest['kind'], ver.get('meta') or {}
    ins = meta.get('inspection') or {}
    errors = (ver.get('validation') or {}).get('errors') or []
    if kind not in ('vehicle', 'facility'):
        return {}
    disp = meta.get('display') or {}
    size = disp.get('size_m') or {}
    display = [
        _item('model', '可显示的模型', not errors and bool(disp.get('builtin') or disp.get('model') or disp.get('urdf') or ins.get('bounds')),
              disp.get('model') or disp.get('builtin') or disp.get('urdf'), '; '.join(errors[:2])),
        _item('resources', '外部资源齐全（贴图 / 缓冲 / 网格）', not any(e for e in errors), None,
              '; '.join(w for w in (ver.get('validation') or {}).get('warnings', []) if '贴图' in w)[:200]),
        _item('size', '尺寸合理（0.05–60 m）', bool(size) and 0.05 <= max(size.values() or [0]) <= 60,
              f"{size.get('length_m')}×{size.get('width_m')}×{size.get('height_m')} m" if size else None),
        _item('orientation', '朝向与缩放已确认', bool(disp.get('confirmed')), disp.get('forward'), '' if disp.get('confirmed') else '在预览里确认前方与向上的轴'),
    ]
    out = {'display': {'ok': all(i['ok'] for i in display), 'items': display}}
    if kind == 'vehicle':
        planning = planning_items(meta.get('planning'))
        needed = [i for i in planning if i['key'] != 'work_width']
        out['planning'] = {'ok': all(i['ok'] for i in needed), 'items': planning, 'solver': solver_mapping(meta.get('planning'))}
        simulation = simulation_items(meta, ins)
        out['simulation'] = {'ok': all(i['ok'] for i in simulation) and out['planning']['ok'], 'items': simulation,
                             'adapters': adapter_options(manifest, ver)}
        out['level'] = 'simulation' if out['simulation']['ok'] else 'planning' if out['planning']['ok'] else 'display' if out['display']['ok'] else 'incomplete'
    else:
        fp = (meta.get('planning') or {}).get('footprint')
        out['planning'] = {'ok': bool(fp), 'items': [_item('footprint', '规划占地（多边形或长×宽）', bool(fp), None, '设施放进场景时按它生成障碍')]}
        out['level'] = 'planning' if out['planning']['ok'] else 'display' if out['display']['ok'] else 'incomplete'
    return out
