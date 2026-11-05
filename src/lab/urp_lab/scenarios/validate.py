"""Instance validation and normalisation (ValueError with a Chinese message when invalid)."""
from __future__ import annotations

import copy
import math

from shapely.geometry import Polygon

from .. import coverage_limits

PROBLEM_CLASSES = ('turn_routing', 'euclidean_cvrp', 'coverage', 'point_to_point', 'fleet_routing')
OBSTACLE_KINDS = ('pond', 'building', 'tree', 'pole', 'ditch', 'greenhouse', 'rock', 'other')
FLEET_OBJECTIVES = ('total_distance', 'makespan')
CVRP_OBJECTIVES = ('sum_unrounded_euclidean_distance', 'sum_rounded_euclidean_distance')
MOTION_MODELS = ('holonomic', 'dubins', 'reeds_shepp')
MAX_NODES = 5000
MAX_CVRP_TASKS = 1000


def _int(v, what: str, lo: int | None = None) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or float(v) != int(v):
        raise ValueError(f'{what} 应为整数，实际 {v!r}')
    v = int(v)
    if lo is not None and v < lo:
        raise ValueError(f'{what} 应 ≥ {lo}，实际 {v}')
    return v


def _num(v, what: str, positive: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ValueError(f'{what} 应为有限数值，实际 {v!r}')
    if positive and v <= 0:
        raise ValueError(f'{what} 应为正数，实际 {v}')
    return float(v)


def _xy(p, what: str) -> list[float]:
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        raise ValueError(f'{what} 应为 [x, y]')
    return [_num(p[0], what + '.x'), _num(p[1], what + '.y')]


def turn_routing(inst: dict) -> dict:
    out = copy.deepcopy(inst)
    arcs = []
    for i, a in enumerate(inst.get('arcs') or []):
        if not isinstance(a, (list, tuple)) or len(a) != 3:
            raise ValueError(f'arcs[{i}] 应为 [u, v, cost]')
        u, v, c = (_int(x, f'arcs[{i}]') for x in a)
        if c < 0:
            raise ValueError(f'arcs[{i}] 成本不能为负')
        if u == v:
            raise ValueError(f'arcs[{i}] 不能是自环')
        arcs.append([u, v, c])
    if not arcs:
        raise ValueError('转弯路网至少需要一条有向路段 arcs')
    if len({(u, v) for u, v, _ in arcs}) != len(arcs):
        raise ValueError('arcs 中存在重复的有向路段')
    nodes = {u for u, _, _ in arcs} | {v for _, v, _ in arcs}
    if len(nodes) > MAX_NODES:
        raise ValueError(f'节点数 {len(nodes)} 超过上限 {MAX_NODES}')
    depot = _int(inst.get('depot'), 'depot')
    if depot not in nodes:
        raise ValueError(f'仓库节点 {depot} 不在路网中')
    capacity = _int(inst.get('capacity'), 'capacity', 1)
    customers, seen_ids, seen_nodes = [], set(), set()
    for i, c in enumerate(inst.get('customers') or []):
        if not isinstance(c, dict):
            raise ValueError(f'customers[{i}] 应为对象')
        cid, node = _int(c.get('id'), f'customers[{i}].id', 0), _int(c.get('node'), f'customers[{i}].node')
        demand = _int(c.get('demand', 1), f'customers[{i}].demand', 1)
        if cid in seen_ids:
            raise ValueError(f'任务 id {cid} 重复')
        if node not in nodes:
            raise ValueError(f'任务 {cid} 的节点 {node} 不在路网中')
        if node == depot:
            raise ValueError(f'任务 {cid} 不能位于仓库节点')
        if node in seen_nodes:
            raise ValueError(f'节点 {node} 上有多个任务；请合并为一个任务')
        seen_ids.add(cid)
        seen_nodes.add(node)
        item = {**c, 'id': cid, 'node': node, 'demand': demand}
        item['label'] = str(c.get('label') or f'T{cid}')
        customers.append(item)
    if not customers:
        raise ValueError('至少需要一个任务')
    if len(customers) > 64:
        raise ValueError('任务数超过 64，超出面板支持范围')
    arcset = {(u, v) for u, v, _ in arcs}

    def triples(key: str, width: int) -> list:
        res = []
        for i, t in enumerate(inst.get(key) or []):
            if not isinstance(t, (list, tuple)) or len(t) != width:
                raise ValueError(f'{key}[{i}] 长度应为 {width}')
            t = [_int(x, f'{key}[{i}]') for x in t]
            if (t[0], t[1]) not in arcset or (t[1], t[2]) not in arcset:
                raise ValueError(f'{key}[{i}] {t[:3]} 引用了不存在的路段')
            if width == 4 and t[3] < 0:
                raise ValueError(f'{key}[{i}] 惩罚不能为负')
            res.append(t)
        return res

    coords = {}
    raw = inst.get('coordinates') or {}
    if raw:
        if not isinstance(raw, dict):
            raise ValueError('coordinates 应为 {节点id: [x, y]}')
        coords = {str(_int(int(k) if str(k).lstrip('-').isdigit() else k, 'coordinates 键')): _xy(v, f'coordinates[{k}]')
                  for k, v in raw.items()}
        missing = sorted(n for n in nodes if str(n) not in coords)
        if missing:
            raise ValueError(f'缺少节点坐标：{missing[:10]}')
    fleet = inst.get('fleet_limit')
    out.update(depot=depot, capacity=capacity, customers=customers, arcs=arcs,
               forbidden_turns=triples('forbidden_turns', 3), turn_penalties=triples('turn_penalties', 4),
               coordinates=coords, units=str(inst.get('units') or 'synthetic_integer_cost'),
               service_heading_reset=bool(inst.get('service_heading_reset', False)),
               depot_reset=str(inst.get('depot_reset') or 'between_routes_only'),
               fleet_limit=None if fleet is None else _int(fleet, 'fleet_limit', 1))
    if out['service_heading_reset']:
        raise ValueError('项目转弯模型要求 service_heading_reset=false（服务点不重置朝向）')
    if out['fleet_limit'] is not None:
        raise ValueError('项目转弯模型只支持不限数量的同型车辆（fleet_limit=null）')
    if isinstance(inst.get('node_kinds'), dict):
        out['node_kinds'] = {str(k): str(v) for k, v in inst['node_kinds'].items()}
    return out


def euclidean_cvrp(inst: dict) -> dict:
    out = copy.deepcopy(inst)
    coords = inst.get('coordinates')
    demands = inst.get('demands')
    if not isinstance(coords, list) or not isinstance(demands, list) or len(coords) != len(demands):
        raise ValueError('需要等长的 coordinates 与 demands 数组（下标 0 为仓库）')
    if len(coords) < 2:
        raise ValueError('至少需要一个任务点')
    if len(coords) - 1 > MAX_CVRP_TASKS:
        raise ValueError(f'任务数超过 {MAX_CVRP_TASKS}')
    coords = [_xy(p, f'coordinates[{i}]') for i, p in enumerate(coords)]
    demands = [_int(d, f'demands[{i}]', 0) for i, d in enumerate(demands)]
    if demands[0] != 0:
        raise ValueError('仓库（下标 0）的需求必须为 0')
    if any(d < 1 for d in demands[1:]):
        raise ValueError('任务需求须为正整数')
    capacity = _int(inst.get('capacity'), 'capacity', 1)
    if inst.get('depot', 0) != 0:
        raise ValueError('仓库必须是下标 0')
    fleet = inst.get('fleet', 'unlimited_identical_vehicles')
    if fleet != 'unlimited_identical_vehicles':
        raise ValueError('只支持不限数量的同型车辆（unlimited_identical_vehicles）')
    objective = inst.get('objective', 'sum_unrounded_euclidean_distance')
    if objective not in CVRP_OBJECTIVES:
        raise ValueError('目标须为欧氏总距离（未取整，或 TSPLIB EUC_2D 四舍五入）')
    labels = inst.get('labels')
    if labels is not None and (not isinstance(labels, list) or len(labels) != len(coords)):
        raise ValueError('labels 长度应与坐标一致')
    out.update(coordinates=coords, demands=demands, capacity=capacity, depot=0, fleet=fleet, objective=objective,
               labels=[str(x) for x in labels] if labels else ['D'] + [str(i) for i in range(1, len(coords))])
    return out


def coverage(inst: dict) -> dict:
    out = copy.deepcopy(inst)
    field = [_xy(p, f'field[{i}]') for i, p in enumerate(inst.get('field') or [])]
    if len(field) < 3:
        raise ValueError('田块边界至少需要 3 个顶点')
    if field[0] == field[-1]:
        field = field[:-1]
    poly = Polygon(field)
    if not poly.is_valid or poly.area <= 0:
        raise ValueError('田块边界多边形无效（自交或面积为 0）')
    if not poly.exterior.is_ccw:
        field = field[::-1]
    obstacles = []
    for k, ob in enumerate(inst.get('obstacles') or []):
        pts = [_xy(p, f'obstacles[{k}][{i}]') for i, p in enumerate(ob)]
        if len(pts) < 3 or not Polygon(pts).is_valid or Polygon(pts).area <= 0:
            raise ValueError(f'障碍 {k} 多边形无效')
        obstacles.append(pts)
    swath = _num(inst.get('swath_width'), 'swath_width', True)
    head = _num(inst.get('headland_width', 0), 'headland_width')
    if head < 0:
        raise ValueError('headland_width 不能为负')
    turn = _num(inst.get('turn_radius', 0), 'turn_radius')
    width = _num(inst.get('robot_width', swath), 'robot_width', True)
    pattern = inst.get('route_pattern', 'boustrophedon')
    if pattern not in ('boustrophedon', 'snake', 'spiral'):
        raise ValueError('route_pattern 只能是 boustrophedon / snake / spiral')
    angle = inst.get('angle_deg')
    angle = None if angle is None else _num(angle, 'angle_deg')
    _, inside = coverage_limits.geometry(field, obstacles, head)
    # Retain the existing area budget, avoiding swath² underflow/overflow.
    if math.sqrt(poly.area) / swath > math.sqrt(200000):
        raise ValueError('田块相对作业幅宽过大（面积预算超出面板范围）')
    # None is automatic direction for f2c.full, not a global x-direction promise.
    # The fixed-direction panel adapter preflights its own y span at solve time.
    if angle is not None:
        coverage_limits.scan_budget(inside, swath, angle)
    out.update(field=field, obstacles=obstacles, swath_width=swath, headland_width=head, turn_radius=max(turn, 0.0),
               robot_width=width, start=_xy(inst.get('start') or field[0], 'start'), route_pattern=pattern,
               angle_deg=angle)
    return out


def _pose(p, what: str) -> list[float]:
    if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
        raise ValueError(f'{what} 应为 [x, y] 或 [x, y, yaw]')
    return [_num(p[0], what + '.x'), _num(p[1], what + '.y'), _num(p[2] if len(p) == 3 else 0.0, what + '.yaw')]


def point_to_point(inst: dict) -> dict:
    out = copy.deepcopy(inst)
    model = inst.get('motion_model', 'holonomic')
    if model not in MOTION_MODELS:
        raise ValueError('motion_model 须为 holonomic / dubins / reeds_shepp')
    radius = _num(inst.get('turning_radius', 0.5), 'turning_radius')
    if model != 'holonomic' and radius <= 0:
        raise ValueError('Dubins / Reeds–Shepp 需要正的最小转弯半径')
    out.update(start=_pose(inst.get('start'), 'start'), goal=_pose(inst.get('goal'), 'goal'), motion_model=model,
               turning_radius=radius, robot_radius=_num(inst.get('robot_radius', 0.175), 'robot_radius', True),
               clearance_margin=max(0.0, _num(inst.get('clearance_margin', 0.05), 'clearance_margin')),
               goal_tolerance=_num(inst.get('goal_tolerance', 0.05), 'goal_tolerance', True))
    if math.dist(out['start'][:2], out['goal'][:2]) < 1e-6:
        raise ValueError('起点与终点重合')
    return out


def _polygon(points, what: str) -> list[list[float]]:
    pts = [_xy(p, f'{what}[{i}]') for i, p in enumerate(points or [])]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError(f'{what} 至少需要 3 个顶点')
    poly = Polygon(pts)
    if not poly.is_valid or poly.area <= 0:
        raise ValueError(f'{what} 多边形无效（自交或面积为 0）')
    return pts if poly.exterior.is_ccw else pts[::-1]


def _fleet_names(fleet: list[dict]) -> list[str]:
    return [f"{c['name']}-{k + 1}" if c['count'] > 1 or len(fleet) > 1 else c['name']
            for c in fleet for k in range(c['count'])]


def _vehicle_fleet(veh: dict, tasks: list[dict], radius: float) -> list[dict]:
    """Normalised `vehicles.fleet` class list: per-class count / capacity / speed / footprint.

    `radius` is the instance-level robot radius; a class without its own footprint uses it, and the
    instance radius is raised to the largest class radius so graphs and clearance stay conservative.
    Validation is idempotent: top-level count / capacity / names that match the derived fleet values
    (as written by normalisation) pass; conflicting values are rejected.
    """
    raw = veh.get('fleet')
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise ValueError('vehicles.fleet 须为 1–8 个车型类别的数组')
    demands = []
    for j, t in enumerate(tasks if isinstance(tasks, list) else []):
        if not isinstance(t, dict):
            raise ValueError(f'tasks[{j}] 应为对象')
        demands.append(_int(t.get('demand', 1), f'tasks[{j}].demand', 1))
    total_demand, max_demand = sum(demands), max(demands, default=1)
    classes, seen_names = [], set()
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            raise ValueError(f'fleet[{i}] 应为对象')
        payload = c.get('payload', 'load')
        if payload not in ('load', 'none'):
            raise ValueError(f'fleet[{i}].payload 只能是 load（载货）或 none（不载货）')
        capacity = total_demand if payload == 'none' else _int(c.get('capacity'), f'fleet[{i}].capacity', 1)
        name = str(c.get('name') or f'车型{i + 1}')[:24]
        if name in seen_names:
            raise ValueError(f'车型类别名重复：{name}')
        seen_names.add(name)
        speed = _num(c.get('speed_mps', veh.get('speed_mps', 1.0)), f'fleet[{i}].speed_mps（或全局默认车速）', True)
        if speed > 15:
            raise ValueError(f'fleet[{i}] 车速 {speed} m/s 超出规划范围（≤ 15）')
        cls_radius = radius if c.get('robot_radius') is None else _num(c.get('robot_radius'), f'fleet[{i}].robot_radius', True)
        turn = 0.0 if c.get('min_turn_radius_m') is None else _num(c.get('min_turn_radius_m'), f'fleet[{i}].min_turn_radius_m')
        if not 0 <= turn <= 10:
            raise ValueError(f'fleet[{i}].min_turn_radius_m 应为 0–10 的数值（0 = 可原地转向；阿克曼/铰接车型须给出正的最小转弯半径）')
        if turn > 0 and veh.get('travel') == 'rows':
            raise ValueError(f'fleet[{i}] 的最小转弯半径 {turn} m 暂不支持行间通行（rows）：通道内转弯的曲率规划尚未实现，请改为自由通行')
        fp = c.get('footprint')
        if fp is not None:
            # body-frame polygon [[x, y], …]: oriented clearance is reported post-hoc;
            # its circumscribed radius must stay inside the class planning circle
            if (not isinstance(fp, dict) or fp.get('type') != 'polygon'
                    or not isinstance(fp.get('points'), list) or len(fp['points']) < 3):
                raise ValueError(f'fleet[{i}].footprint 须为 {{type: polygon, points: [[x,y],…]}}')
            pts = [_xy(p, f'fleet[{i}].footprint.points') for p in fp['points']]
            if max(math.hypot(x, y) for x, y in pts) > cls_radius + 1e-9:
                raise ValueError(f'fleet[{i}].footprint 外接半径超过该类别规划圆 {cls_radius:g} m：朝向足迹不得超出规划契约')
            fp = {'type': 'polygon', 'points': pts}
        asset = c.get('asset')
        if asset is not None and not (isinstance(asset, dict) and isinstance(asset.get('id'), str)
                                      and isinstance(asset.get('version'), int) and not isinstance(asset.get('version'), bool)
                                      and asset['version'] >= 1 and isinstance(asset.get('sha256'), str) and asset['sha256']):
            raise ValueError(f'fleet[{i}].asset 须为 {{id, version, sha256}} 资产引用')
        classes.append({'name': name, 'count': _int(c.get('count', 1), f'fleet[{i}].count', 1),
                        'capacity': capacity, 'speed_mps': speed, 'robot_radius': cls_radius,
                        'min_turn_radius_m': turn, 'payload': payload,
                        **({'footprint': fp} if fp is not None else {}),
                        **({'asset': asset} if asset is not None else {})})
    total = sum(c['count'] for c in classes)
    if max(c['capacity'] for c in classes) < max_demand:
        raise ValueError(f'最大单点需求 {max_demand} 超过所有车型容量，没有可服务该任务的车辆')
    if total > 60:
        raise ValueError(f'车队总台数 {total} 超过 60')
    derived_names = _fleet_names(classes)
    for key, derived in (('count', total), ('capacity', max(c['capacity'] for c in classes))):
        if veh.get(key) is not None and veh[key] != derived:
            raise ValueError(f'vehicles.{key}（{veh[key]}）与 fleet 类别清单推得的值（{derived}）不一致；台数与容量按类别写在 fleet 里')
    if veh.get('names') is not None and [str(n) for n in (veh['names'] if isinstance(veh['names'], list) else [])] != derived_names:
        raise ValueError('vehicles.names 与 fleet 类别清单推得的名字不一致；类别名与编号由 fleet 生成')
    return classes


def fleet_routing(inst: dict) -> dict:
    out = copy.deepcopy(inst)
    boundary = _polygon(inst.get('boundary'), '场地边界')
    outer = Polygon(boundary)
    radius = _num(inst.get('robot_radius', 0.5), 'robot_radius', True)
    veh = inst.get('vehicles') or {}
    if not isinstance(veh, dict):
        raise ValueError('vehicles 应为对象')
    fleet = _vehicle_fleet(veh, inst.get('tasks') or [], radius) if veh.get('fleet') is not None else None
    if fleet:
        radius = max([radius] + [c['robot_radius'] for c in fleet])
    plots = []
    for i, p in enumerate(inst.get('plots') or []):
        if not isinstance(p, dict):
            raise ValueError(f'plots[{i}] 应为对象')
        plots.append({**p, 'id': str(p.get('id') or f'P{i + 1}'), 'name': str(p.get('name') or f'地块{i + 1}'),
                      'polygon': _polygon(p.get('polygon'), f'地块 {i + 1}'),
                      'rows_deg': _num(p.get('rows_deg', 0), f'地块 {i + 1} 作物行方向')})
    obstacles = []
    for i, o in enumerate(inst.get('obstacles') or []):
        if not isinstance(o, dict):
            raise ValueError(f'obstacles[{i}] 应为对象')
        kind = o.get('kind', 'other')
        obstacles.append({**o, 'id': str(o.get('id') or f'O{i + 1}'), 'kind': kind if kind in OBSTACLE_KINDS else 'other',
                          'polygon': _polygon(o.get('polygon'), f'障碍 {i + 1}')})
    # 道路 / 机耕道 / 地头走廊：可通行、不种植；行间通行的开阔区自动包含它们
    corridors = []
    for i, c in enumerate(inst.get('corridors') or []):
        if not isinstance(c, dict):
            raise ValueError(f'corridors[{i}] 应为对象')
        corridors.append({**c, 'id': str(c.get('id') or f'C{i + 1}'), 'name': str(c.get('name') or ''),
                          'polygon': _polygon(c.get('polygon'), f'通行走廊 {i + 1}')})
    if len(corridors) > 200:
        raise ValueError('通行走廊超过 200 条')
    from shapely.geometry import Point
    from shapely.ops import unary_union
    blocked = unary_union([Polygon(o['polygon']).buffer(radius) for o in obstacles]) if obstacles else None
    free = outer.buffer(-radius)
    if blocked is not None:
        free = free.difference(blocked)

    def placeable(xy, what):
        if not free.buffer(1e-6).contains(Point(xy)):
            raise ValueError(f'{what} 位于障碍物内、离障碍太近或在边界外（需留出机器人半径 {radius:g} m）')
    depot = _xy(inst.get('depot'), 'depot')
    placeable(depot, '车库')
    unload_points = []
    for i, p in enumerate(inst.get('unload_points') or []):
        xy = _xy(p, f'unload_points[{i}]')
        placeable(xy, f'补给点 {i + 1}')
        unload_points.append(xy)
    if len(unload_points) > 16:
        raise ValueError('补给点超过 16 个')
    depots = [depot]
    if inst.get('depots') is not None:
        raw_depots = inst['depots']
        if not isinstance(raw_depots, list) or not 1 <= len(raw_depots) <= 8:
            raise ValueError('depots 须为 1–8 个车库坐标的数组')
        depots = []
        for i, d in enumerate(raw_depots):
            xy = _xy(d, f'depots[{i}]')
            placeable(xy, f'车库 {i + 1}')
            if any(math.dist(xy, other) < 1e-6 for other in depots):
                raise ValueError(f'depots[{i}] 与已有车库坐标重复')
            depots.append(xy)
        depot = depots[0]
    tasks, seen = [], set()
    for i, t in enumerate(inst.get('tasks') or []):
        if not isinstance(t, dict):
            raise ValueError(f'tasks[{i}] 应为对象')
        tid = _int(t.get('id', i + 1), f'tasks[{i}].id', 1)
        if tid in seen:
            raise ValueError(f'任务编号 {tid} 重复')
        seen.add(tid)
        xy = _xy(t.get('xy'), f'任务 {tid}')
        placeable(xy, f"任务 {t.get('label') or tid}")
        tasks.append({**t, 'id': tid, 'xy': xy, 'demand': _int(t.get('demand', 1), f'任务 {tid} 需求', 1),
                      'label': str(t.get('label') or f'T{tid}'), 'service_s': max(0.0, _num(t.get('service_s', 0), f'任务 {tid} 作业时间'))})
    if not tasks:
        raise ValueError('至少需要一个任务点')
    if len(tasks) > 800:
        raise ValueError('任务点超过 800 个')
    veh = inst.get('vehicles') or {}
    if not isinstance(veh, dict):
        raise ValueError('vehicles 应为对象')
    if veh.get('travel', 'free') not in ('free', 'rows') or veh.get('trips', 'multi') not in ('single', 'multi'):
        raise ValueError('travel 须为 free/rows，trips 须为 single/multi')
    objective = inst.get('objective', 'total_distance')
    if objective not in FLEET_OBJECTIVES:
        raise ValueError('objective 须为 total_distance（总里程最短）或 makespan（最晚完成时间最短）')
    if fleet is not None:
        count = sum(c['count'] for c in fleet)
        capacity = max(c['capacity'] for c in fleet)
        speed = max(c['speed_mps'] for c in fleet)
        names = _fleet_names(fleet)
    else:
        count = _int(veh.get('count', 1), '车辆数', 1)
        if count > 60:
            raise ValueError('车辆数超过 60')
        capacity = _int(veh.get('capacity', max(t['demand'] for t in tasks) * len(tasks)), '车辆容量', 1)
        if capacity < max(t['demand'] for t in tasks):
            raise ValueError(f'单车容量 {capacity} 小于最大单点需求 {max(t["demand"] for t in tasks)}')
        speed = _num(veh.get('speed_mps', 1.0), '车速', True)
        names = veh.get('names') if isinstance(veh.get('names'), list) else None
    out.update(boundary=boundary, plots=plots, obstacles=obstacles, corridors=corridors, depot=depot, depots=depots,
               unload_points=unload_points, tasks=tasks, robot_radius=radius,
               vehicles={'count': count, 'capacity': capacity, 'speed_mps': speed,
                         'trips': 'single' if veh.get('trips') == 'single' else 'multi',
                         # how vehicles move: along crop-row lanes and headlands, or anywhere clear of obstacles
                         'travel': 'rows' if veh.get('travel') == 'rows' else 'free',
                         'names': [str(n) for n in names][:count] if names else [f'车{i + 1}' for i in range(count)],
                         # per-class count / capacity / speed / footprint; absent for legacy homogeneous instances
                         **({'fleet': fleet} if fleet is not None else {})},
               objective=objective, units='m')
    if inst.get('ground') is not None:
        from ..ground_map import normalize_ground
        out['ground'] = normalize_ground(out)
    return out


def validate_instance(problem_class: str, instance: dict) -> dict:
    if problem_class not in PROBLEM_CLASSES:
        raise ValueError(f'未知问题类别 {problem_class!r}')
    if not isinstance(instance, dict):
        raise ValueError('instance 应为 JSON 对象')
    unsupported = {
        'time_windows': '时间窗', 'precedence': '任务先后约束', 'line_tasks': '线服务任务',
        'area_tasks': '混合面服务任务', 'mixed_tasks': '混合作业', 'dynamic_obstacles': '动态障碍约束',
        'energy_constraints': '能源约束', 'resource_capacities': '补给并发容量',
    }
    for key, label in unsupported.items():
        if instance.get(key):
            raise ValueError(f'当前 {problem_class} 适配器尚未实现{label}（{key}）；输入保留，请勿将其作为已生效约束提交')
    tasks = instance.get('tasks') or instance.get('customers') or []
    for task in (tasks if isinstance(tasks, list) else []):
        if isinstance(task, dict) and (task.get('time_window') or task.get('time_windows')):
            raise ValueError('当前任务访问模型尚未实现时间窗，不能静默忽略该约束')
    return {'turn_routing': turn_routing, 'euclidean_cvrp': euclidean_cvrp, 'coverage': coverage,
            'point_to_point': point_to_point, 'fleet_routing': fleet_routing}[problem_class](instance)
