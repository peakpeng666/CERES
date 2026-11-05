"""Map-frame scenarios built from the official TurtleBot4 occupancy maps (metres, ROS map frame)."""
from __future__ import annotations

import math
import random
from functools import lru_cache

import numpy as np
from scipy import ndimage

from . import maps

ARC_MIN_CLEARANCE_M = 0.45      # 机器人半径 0.175 m + 余量
SPAWN_MIN_CLEARANCE_M = 0.9
STATION_SPACING_M = 3.5
TURN_PENALTY = {'turn_90': 150, 'uturn_headland': 600, 'straight_deg': 30, 'uturn_deg': 135}
AISLE_LETTERS = 'ABCDEFGH'


def _plateau_mid(values: np.ndarray, coords: np.ndarray, tol: float) -> tuple[float, float]:
    best = float(values.max())
    sel = coords[values >= best - tol]
    return float((sel.min() + sel.max()) / 2), best


def _line_min(grid, a, b) -> float:
    return maps.segment_clearance(grid, a, b)[0]


def turn_penalty(a, b, c) -> int | None:
    """Penalty (cm-eq) for heading change at b; None for a U-turn (u == w)."""
    h1 = math.atan2(b[1] - a[1], b[0] - a[0])
    h2 = math.atan2(c[1] - b[1], c[0] - b[0])
    deg = abs(math.degrees(math.atan2(math.sin(h2 - h1), math.cos(h2 - h1))))
    if deg < TURN_PENALTY['straight_deg']:
        return 0
    if deg >= TURN_PENALTY['uturn_deg']:
        return TURN_PENALTY['uturn_headland']
    return TURN_PENALTY['turn_90']


@lru_cache(maxsize=None)
def warehouse_layout() -> dict:
    """Detect shelf rows, aisle centerlines, headlands and a dock-safe spawn from warehouse.pgm."""
    g = maps.load('warehouse')
    res = g.resolution
    labels, count = ndimage.label(~g.free, structure=np.ones((3, 3)))
    shelves = []
    for sl in ndimage.find_objects(labels):
        r0, r1, c0, c1 = sl[0].start, sl[0].stop - 1, sl[1].start, sl[1].stop - 1
        x0, y1 = g.xy(r0, c0)
        x1, y0 = g.xy(r1, c1)
        x0, x1, y0, y1 = x0 - res / 2, x1 + res / 2, y0 - res / 2, y1 + res / 2
        if y1 - y0 >= 12 and 1.0 <= x1 - x0 <= 4.0 and y1 < 0:
            shelves.append((x0, x1, y0, y1))
    shelves.sort()
    if len(shelves) != 3:
        raise RuntimeError(f'warehouse 地图分析：预期 3 排长货架，检测到 {len(shelves)} 个')
    band_lo, band_hi = max(s[2] for s in shelves), min(s[3] for s in shelves)
    in_lo, in_hi = band_lo + 0.5, band_hi - 0.5
    rows = slice(g.rc(0, in_hi)[0], g.rc(0, in_lo)[0] + 1)
    col_free = g.free[rows, :].all(axis=0)
    intervals, start = [], None
    for c, f in enumerate(list(col_free) + [False]):
        if f and start is None:
            start = c
        elif not f and start is not None:
            intervals.append((g.xy(0, start)[0], g.xy(0, c - 1)[0]))
            start = None
    aisles = []
    for lo, hi in intervals:
        if hi - lo < 2.0:
            continue
        x = (lo + hi) / 2
        aisles.append({'x': round(x, 3), 'interval': [round(lo, 3), round(hi, 3)],
                       'band_min_clearance_m': round(_line_min(g, (x, in_lo), (x, in_hi)), 3)})
    if len(aisles) != 4:
        raise RuntimeError(f'warehouse 地图分析：预期货架间/两侧 4 条通道，检测到 {len(aisles)} 条')
    xa, xd = aisles[0]['x'], aisles[-1]['x']

    def headland(lo, hi):
        ys = np.arange(lo, hi, res)
        mins = np.array([_line_min(g, (xa, y), (xd, y)) for y in ys])
        return _plateau_mid(mins, ys, res / 2)

    y_n, n_clear = headland(band_hi + 0.3, band_hi + 3.0)
    y_s, s_clear = headland(band_lo - 3.0, band_lo - 0.3)
    y_n, y_s = round(y_n, 3), round(y_s, 3)
    segments = max(2, round((y_n - y_s) / STATION_SPACING_M))
    for i, a in enumerate(aisles):
        a['label'] = AISLE_LETTERS[i]
        a['min_clearance_m'] = round(_line_min(g, (a['x'], y_s), (a['x'], y_n)), 3)
    centre = sum(a['x'] for a in aisles) / len(aisles)
    spawn = depot = None
    for a in sorted(aisles, key=lambda a: abs(a['x'] - centre)):
        for spur in (2.0, 1.5, 2.5):
            d = (a['x'], round(y_n + spur, 3))
            s = {'x': d[0], 'y': round(d[1] + maps.UNDOCK_BACKUP_M, 3), 'yaw': round(math.pi / 2, 6)}
            if (g.clearance_at(s['x'], s['y']) >= SPAWN_MIN_CLEARANCE_M and g.clearance_at(*d) >= SPAWN_MIN_CLEARANCE_M
                    and _line_min(g, (a['x'], y_n), d) >= 0.6 and maps.dock_clearance(g, s) >= 0.3):
                spawn, depot, depot_aisle = s, d, a['label']
                break
        if spawn:
            break
    if spawn is None:
        raise RuntimeError('warehouse 地图分析：北侧地头附近没有满足 ≥0.9 m 间隙与充电座空间的生成点')
    return {'shelves': shelves, 'band': [band_lo, band_hi], 'aisles': aisles, 'y_north': y_n, 'y_south': y_s,
            'headland_clearance_m': [round(n_clear, 3), round(s_clear, 3)], 'segments': segments,
            'spawn': spawn, 'depot': depot, 'depot_aisle': depot_aisle}


def _network(layout: dict) -> dict:
    coords, kinds, names = {0: layout['depot']}, {0: 'depot'}, {0: '仓库'}
    north, south, stations = {}, {}, {}
    nid = 1
    seg = layout['segments']
    for a in layout['aisles']:
        x, lab = a['x'], a['label']
        north[lab] = nid
        coords[nid], kinds[nid], names[nid] = (x, layout['y_north']), 'headland', f'{lab}北'
        nid += 1
        for k in range(1, seg):
            y = layout['y_north'] + (layout['y_south'] - layout['y_north']) * k / seg
            stations[f'{lab}{k}'] = nid
            coords[nid], kinds[nid], names[nid] = (x, round(y, 3)), 'row', f'{lab}{k}'
            nid += 1
        south[lab] = nid
        coords[nid], kinds[nid], names[nid] = (x, layout['y_south']), 'headland', f'{lab}南'
        nid += 1
    edges = [(0, north[layout['depot_aisle']])]
    labs = [a['label'] for a in layout['aisles']]
    for lab in labs:
        chain = [north[lab]] + [stations[f'{lab}{k}'] for k in range(1, seg)] + [south[lab]]
        edges += list(zip(chain, chain[1:]))
    for a, b in zip(labs, labs[1:]):
        edges += [(north[a], north[b]), (south[a], south[b])]
    return {'coords': coords, 'kinds': kinds, 'names': names, 'edges': edges, 'stations': stations}


@lru_cache(maxsize=None)
def farm_rows_warehouse() -> dict:
    layout = warehouse_layout()
    g = maps.load('warehouse')
    net = _network(layout)
    coords, kinds = net['coords'], net['kinds']
    arcs, clearance = [], []
    for u, v in net['edges']:
        c, where = maps.segment_clearance(g, coords[u], coords[v])
        if c < ARC_MIN_CLEARANCE_M:
            raise RuntimeError(f'路段 {u}-{v} 最小间隙 {c:.2f} m < {ARC_MIN_CLEARANCE_M} m')
        cost = round(math.dist(coords[u], coords[v]) * 100)
        arcs += [[u, v, cost], [v, u, cost]]
        clearance.append([u, v, round(c, 3)])
    nbrs = {}
    for u, v, _ in arcs:
        nbrs.setdefault(v, []).append(u)
    forbidden, penalties = [], []
    for u, v, _ in sorted(arcs):
        for w in sorted(nbrs[v]):
            if u == w and kinds[v] == 'row':
                forbidden.append([u, v, w])
                continue
            if kinds[v] == 'row':
                continue
            p = TURN_PENALTY['uturn_headland'] if u == w else turn_penalty(coords[u], coords[v], coords[w])
            if p:
                penalties.append([u, v, w, p])
    default = ['B2', 'B4', 'C1', 'C3', 'C5']
    customers = [{'id': i, 'node': net['stations'][lab], 'demand': 1, 'label': lab} for i, lab in enumerate(default)]
    inst = {'depot': 0, 'capacity': 6, 'customers': customers, 'arcs': sorted(arcs), 'forbidden_turns': forbidden,
            'turn_penalties': penalties, 'coordinates': {str(k): [round(v[0], 3), round(v[1], 3)] for k, v in coords.items()},
            'node_kinds': {str(k): v for k, v in kinds.items()}, 'node_labels': {str(k): v for k, v in net['names'].items()},
            'units': 'cm_equivalent', 'service_heading_reset': False, 'depot_reset': 'between_routes_only', 'fleet_limit': None}
    spawn = layout['spawn']
    start = maps.start_pose_after_undock(spawn)
    shelves = layout['shelves']
    a0, a1 = layout['aisles'][0]['x'], layout['aisles'][-1]['x']
    labels = [{'xy': [a['x'], round(layout['y_north'] + 0.8, 3)], 'text': f"通道 {a['label']}"} for a in layout['aisles']]
    labels += [{'xy': [round((s[0] + s[1]) / 2, 3), round((s[2] + s[3]) / 2, 3)], 'text': f'作物行 {i + 1}（货架）'}
               for i, s in enumerate(shelves)]
    labels += [{'xy': list(layout['depot']), 'text': '仓库/起点'}]
    params = {
        'world': 'warehouse', 'map_yaml': str(g.yaml), 'free_rule': 'p=(255-像素)/255 < free_thresh 为空闲；未知(205)按占据',
        'shelves_detected': [[round(v, 3) for v in s] for s in shelves],
        'aisle_centers_x': [a['x'] for a in layout['aisles']],
        'aisle_detection': '货架带（内缩 0.5 m）内整列空闲的连续区间（宽 ≥ 2 m）取中点为通道中心线；地头取最小间隙最大的水平线',
        'headland_y': {'north': layout['y_north'], 'south': layout['y_south']},
        'headland_clearance_m': layout['headland_clearance_m'],
        'station_spacing_m': round(abs(layout['y_north'] - layout['y_south']) / layout['segments'], 3),
        'arc_cost': 'round(路段长度 m × 100)（cm 当量）',
        'turn_rules': {'row_interior_uturn': '禁止（作物行内不得掉头）', 'headland_turn_30_135_deg': TURN_PENALTY['turn_90'],
                       'headland_uturn': TURN_PENALTY['uturn_headland'], 'straight_lt_30_deg': 0,
                       'depot_uturn': TURN_PENALTY['uturn_headland']},
        'robot_radius_m': maps.ROBOT_RADIUS_M, 'arc_min_clearance_required_m': ARC_MIN_CLEARANCE_M,
        'min_arc_clearance_m': min(c[2] for c in clearance),
        'spawn': spawn, 'start_pose_after_undock': start,
        'spawn_note': '生成朝向朝北（机器人面向充电座，座在前方 0.16–0.35 m）；脱离充电座后退约 0.35 m 并转 180°，朝南沿仓库弧驶向地头；仓库节点 = 脱离后起点',
        'robot_speed_m_s': 0.26,
    }
    return {
        'id': 'farm-rows-warehouse', 'title': '仓储货架通道 · 作物行代理',
        'summary': '官方 warehouse 地图南部三排长货架作为作物行，货架间/两侧 4 条通道为行驶行；行内禁止掉头，地头转弯有惩罚。',
        'problem_class': 'turn_routing', 'frame': _frame('warehouse'), 'cost_unit': 'cm_equivalent',
        'provenance': {'generator': 'urp_lab.scenarios.field:farm_rows_warehouse', 'params': params, 'seed': None,
                       'note': '由官方占据栅格地图自动检测通道中心线并生成路网；货架只是作物行的几何代理，不是真实农田数据。'},
        'sim': {'supported': True, 'world': 'warehouse', 'spawn': dict(spawn), 'reason': None, 'start_pose_after_undock': start},
        'instance': inst,
        'editable': {'tasks': True, 'depot': False, 'capacity': True, 'demands': True},
        'layers': {
            'row_polygons': [[[s[0], s[2]], [s[1], s[2]], [s[1], s[3]], [s[0], s[3]]] for s in shelves],
            'headland': [[[a0, layout['y_north']], [a1, layout['y_north']]], [[a0, layout['y_south']], [a1, layout['y_south']]]],
            'labels': labels, 'arc_clearance': clearance,
            'aisles': [{k: a[k] for k in ('label', 'x', 'min_clearance_m')} for a in layout['aisles']],
            'dock': maps.dock_footprint(spawn), 'spawn': spawn,
        },
    }


def _frame(world: str) -> dict:
    meta = maps.world_map(world)
    return {'kind': 'map', 'units': 'm', 'world': world,
            'map': {k: meta[k] for k in ('world', 'yaml', 'resolution', 'origin', 'width', 'height', 'image')}}


DEPOT_REGION = (-14.0, -2.0, -6.5, 6.5)
FIELD_RECT = (-13.5, -7.5, -2.0, 2.0)


@lru_cache(maxsize=None)
def cvrp_depot_floor() -> dict:
    g = maps.load('depot')
    spawn = maps.WORLDS['depot']['spawn']
    start = maps.start_pose_after_undock(spawn)
    seed = 20261015
    rng = random.Random(seed)
    pts = []
    x0, x1, y0, y1 = DEPOT_REGION
    while len(pts) < 10:
        p = (round(rng.uniform(x0, x1), 2), round(rng.uniform(y0, y1), 2))
        if g.clearance_at(*p) >= 0.6 and all(math.dist(p, q) >= 1.5 for q in pts + [(start['x'], start['y'])]):
            pts.append(p)
    demands = [0] + [rng.randint(1, 3) for _ in pts]
    inst = {'coordinates': [[start['x'], start['y']]] + [list(p) for p in pts], 'demands': demands, 'capacity': 8,
            'depot': 0, 'fleet': 'unlimited_identical_vehicles', 'objective': 'sum_unrounded_euclidean_distance',
            'labels': ['D'] + [f'P{i}' for i in range(1, len(pts) + 1)]}
    return {
        'id': 'cvrp-depot-floor', 'title': '车间空地 · 欧氏 CVRP',
        'summary': '官方 depot 地图左侧开阔地面 10 个任务点，仓库 = 机器人起点；直线段经地图间隙检查（可能穿过柱子，会如实报告）。',
        'problem_class': 'euclidean_cvrp', 'frame': _frame('depot'), 'cost_unit': 'm',
        'provenance': {'generator': 'urp_lab.scenarios.field:cvrp_depot_floor',
                       'params': {'world': 'depot', 'region_xy': list(DEPOT_REGION), 'n_tasks': 10, 'min_point_clearance_m': 0.6,
                                  'min_separation_m': 1.5, 'demand_range': [1, 3], 'capacity': 8, 'spawn': spawn,
                                  'start_pose_after_undock': start, 'distance': '未取整欧氏距离（米），不考虑障碍'},
                       'seed': seed,
                       'note': '任务点由固定种子在空闲区域内抽样（只按点位间隙筛选，不按求解结果筛选）；路线为直线段，碰撞由 map_check 报告。'},
        'sim': {'supported': True, 'world': 'depot', 'spawn': dict(spawn), 'reason': None, 'start_pose_after_undock': start},
        'instance': inst,
        'editable': {'tasks': True, 'depot': False, 'capacity': True, 'demands': True},
        'layers': {'labels': [{'xy': p, 'text': lab} for p, lab in zip(inst['coordinates'], inst['labels'])],
                   'dock': maps.dock_footprint(spawn), 'spawn': spawn},
    }


@lru_cache(maxsize=None)
def field_coverage_depot() -> dict:
    spawn = maps.WORLDS['depot']['spawn']
    start = maps.start_pose_after_undock(spawn)
    x0, x1, y0, y1 = FIELD_RECT
    field = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    inst = {'field': field, 'obstacles': [], 'headland_width': 0.8, 'swath_width': 0.5, 'turn_radius': 0.3,
            'robot_width': 0.35, 'start': [start['x'], start['y']], 'route_pattern': 'boustrophedon', 'angle_deg': None}
    return {
        'id': 'field-coverage-depot', 'title': '车间空地 · 内缩作业区覆盖',
        'summary': '官方 depot 空地上 6×4 m 矩形，幅宽 0.5 m、地头 0.8 m。Fields2Cover 使用 0.3 m 最小转弯半径的 Dubins 连接；基线允许原地转向。地头不计覆盖作业。',
        'problem_class': 'coverage', 'frame': _frame('depot'), 'cost_unit': 'm',
        'provenance': {'generator': 'urp_lab.scenarios.field:field_coverage_depot',
                       'params': {'world': 'depot', 'field_rect': list(FIELD_RECT), 'spawn': spawn, 'start_pose_after_undock': start},
                       'seed': None, 'note': '田块为面板设定的矩形区域（地图空地），不是真实农田边界。'},
        'sim': {'supported': True, 'world': 'depot', 'spawn': dict(spawn), 'reason': None, 'start_pose_after_undock': start},
        'instance': inst,
        'editable': {'tasks': False, 'depot': False, 'capacity': False, 'demands': False},
        'layers': {'field_polygon': field, 'labels': [{'xy': inst['start'], 'text': '起点'}],
                   'dock': maps.dock_footprint(spawn), 'spawn': spawn},
    }


# Reserved even in the bench image, where official ROS maps are not installed.
MAP_SCENARIO_IDS = frozenset({'maze-inspection', 'farm-rows-warehouse', 'cvrp-depot-floor', 'field-coverage-depot'})


def map_scenarios() -> list[dict]:
    installed = set(maps.known_worlds())
    builders = [('maze', maze_inspection), ('warehouse', farm_rows_warehouse),
                ('depot', cvrp_depot_floor), ('depot', field_coverage_depot)]
    # Missing maps are an optional deployment capability. Errors in an installed
    # map must propagate; never replace corrupt maps with fabricated geometry.
    return [build() for world, build in builders if world in installed]


def maze_inspection() -> dict:
    """Short, repeatable official-world integration demonstration (not agricultural validation)."""
    coords = {'0': [-0.35, 0.0], '1': [-1.5, 0.0], '2': [-2.5, 0.0]}
    inst = {'depot': 0, 'capacity': 2, 'customers': [{'id': 0, 'node': 1, 'demand': 1, 'label': '巡检 A'},
            {'id': 1, 'node': 2, 'demand': 1, 'label': '巡检 B'}],
            'arcs': [[0,1,115],[1,0,115],[1,2,100],[2,1,100]], 'forbidden_turns': [],
            'turn_penalties': [[0,1,0,60],[1,2,1,60],[2,1,2,60]], 'coordinates': coords,
            'node_kinds': {'0':'depot','1':'grid','2':'grid'}, 'units':'cm_equivalent',
            'service_heading_reset':False,'depot_reset':'between_routes_only','fleet_limit':None}
    return dict(id='maze-inspection', title='官方迷宫 · 双点巡检闭环',
                summary='4.3 m 往返路网、两个访问任务，用于集成验收；不是农业场景验证。',
                problem_class='turn_routing', frame=_frame('maze'), cost_unit='cm_equivalent', instance=inst,
                provenance=dict(generator='urp_lab.scenarios.field:maze_inspection',params={'arc_cost':'厘米取整；掉头罚 60 cm 当量'},seed=None,
                    note='官方 maze 地图与 world 原点对齐；脱座位置约 (-0.35,0)，全路径由地图与机器人足迹独立检查。'),
                sim=dict(supported=True,world='maze',spawn=maps.default_spawn('maze'),reason=None),
                editable=dict(tasks=True,depot=False,capacity=True,demands=True),layers={})
