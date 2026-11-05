"""Point-to-point motion planning scenarios on the official maps; start = the robot's pose after undocking."""
from __future__ import annotations

import math

from . import maps
from .field import _frame, warehouse_layout

ROBOT = {'robot_radius': maps.ROBOT_RADIUS_M, 'clearance_margin': maps.MAP_CHECK_MARGIN_M, 'goal_tolerance': 0.05}


def _scenario(sid, title, summary, world, spawn, goal, model, radius, note):
    start = maps.start_pose_after_undock(spawn)
    g = maps.load(world)
    for name, (x, y) in (('起点', (start['x'], start['y'])), ('终点', goal[:2])):
        need = ROBOT['robot_radius'] + ROBOT['clearance_margin']
        if g.clearance_at(x, y) < need:
            raise AssertionError(f'{sid} {name} 净空不足')
    return {
        'id': sid, 'title': title, 'summary': summary, 'problem_class': 'point_to_point', 'frame': _frame(world),
        'cost_unit': 'm',
        'provenance': {'generator': 'urp_lab.scenarios.motion', 'params': {'world': world, 'motion_model': model, 'turning_radius': radius},
                       'seed': None, 'note': note},
        'sim': {'supported': True, 'world': world, 'spawn': spawn, 'reason': None, 'start_pose_after_undock': start},
        'instance': {'start': [start['x'], start['y'], start['yaw']], 'goal': list(goal), 'motion_model': model,
                     'turning_radius': radius, **ROBOT},
        'editable': {'tasks': True, 'depot': False, 'capacity': False, 'demands': False},
        'layers': {}, 'origin': 'builtin',
    }


# Stable reserved identities, independent of which optional map assets are installed.
MAP_SCENARIO_IDS = frozenset({'p2p-warehouse-aisle', 'p2p-warehouse-dubins', 'p2p-maze-cross', 'p2p-depot-reeds'})


def motion_scenarios() -> list[dict]:
    installed = set(maps.known_worlds())
    out = []
    if 'warehouse' in installed:
        layout = warehouse_layout()
        spawn = layout['spawn']
        aisles = layout['aisles']
        far = max(aisles, key=lambda a: abs(a['x'] - spawn['x']) if isinstance(a, dict) else 0) if aisles and isinstance(aisles[0], dict) else None
        gx = far['x'] if far else 9.55
        goal_wh = (gx, layout['y_south'] + 1.6, -math.pi / 2)
        out.extend([
            _scenario('p2p-warehouse-aisle', '仓库 · 跨通道到达', '从北侧地头出发，到最远一条作物行通道的南端；全向运动模型。',
                      'warehouse', spawn, goal_wh, 'holonomic', 0.0,
                      '起点为 TurtleBot4 脱离充电座后的位姿，终点位于远端通道南端，均按机器人足迹核验地图净空。'),
            _scenario('p2p-warehouse-dubins', '仓库 · 农机转弯半径', '同一起终点，但按 Dubins 车（最小转弯半径 0.6 m，只能前进）规划。',
                      'warehouse', spawn, goal_wh, 'dubins', 0.6,
                      '用 Dubins 模型模拟只能前进、有最小转弯半径的农机，可对照全向模型看转弯约束的代价。'),
        ])
    if 'maze' in installed:
        out.append(_scenario('p2p-maze-cross', '迷宫 · 对角穿越', '迷宫起点到西北角走廊；考验搜索类与采样类规划器在窄通道中的表现。',
                  'maze', {'x': 0.0, 'y': 0.0, 'yaw': 0.0}, (-8.4, 8.6, math.pi / 2), 'holonomic', 0.0,
                  '官方 maze 世界，窄走廊与死胡同较多。'))
    if 'depot' in installed:
        out.append(_scenario('p2p-depot-reeds', '车间 · 可倒车泊位', '车间空地上按 Reeds–Shepp（可倒车，转弯半径 0.5 m）驶入作业区一角。',
                  'depot', {'x': -6.0, 'y': 0.0, 'yaw': 0.0}, (-12.8, -5.6, 0.0), 'reeds_shepp', 0.5,
                  'Reeds–Shepp 允许倒车，适合模拟进出狭窄泊位的作业车辆。'))
    return out
