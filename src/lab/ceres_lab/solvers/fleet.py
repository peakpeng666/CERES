# CERES 多机协同任务分配与路径规划求解器 (fleet_routing)
# 拓扑行作图与障碍物膨胀可视度图最短路规划 (All-pairs Dijkstra / A*)
# 求解器决策机群分配与访问次序，轨迹经运动学转弯平滑后由独立核验器二次复验
from __future__ import annotations

import math
import time
from collections import Counter

import numpy as np

from .. import lanes, time_windows
from ..path_geometry import arc_lengths, locate
from .fleet_graph import Graph, LaneGraph
from .fleet_common import depot_list, fleet_classes, vehicle_table  # 机群拓扑辅助
from .fleet_curvature import (  # 曲率与转弯平滑
    _circumradius, dubins_realize, min_curvature_radius, ompl_dubins_fallback, relax_corners)
from .fleet_balanced import pyvrp_makespan
from .fleet_engines import metaheuristic, ortools_makespan  # 元启发式求解引擎

COLORS = ['#007aff', '#ff6b00', '#34a853', '#a142f4', '#e8336d', '#00a6a6', '#c49000', '#5856d6', '#8e5a2b', '#2f7d32',
          '#d81b60', '#1e88e5']
# How each fleet algorithm treats task time windows. The catalog shows this, and an unsupported one refuses a windowed task.
TIME_WINDOW_SUPPORT = {'pyvrp.balanced': 'native', 'ortools.fleet': 'time_dimension', 'pyvrp.fleet': 'unsupported',
                       'panel.savings_fleet': 'unsupported', 'panel.sweep': 'unsupported', 'panel.cluster_first': 'unsupported'}
WINDOW_ROUNDS = (.5, .3, .2)    # budget shares: search, then search again with the deadlines moved up by what the checker found
# Aim this far ahead of a deadline. Recorded arrivals ran up to 3 s behind the time model on 40 tasks in MuJoCo (2026-11-06).
WINDOW_MARGIN_S = 3.0


# ------------------------------------------------------------------- fleet --
def travel_graph(inst, ground_zones=None, risk_weight=0.0):
    return LaneGraph(inst) if inst['vehicles'].get('travel') == 'rows' \
        else Graph(inst, ground_zones, risk_weight)


# ----------------------------------------------------------------- solvers --
def _tour_cost(D, tour):
    return sum(D[a, b] for a, b in zip(tour, tour[1:]))


def two_opt(D, tour, deadline):
    """2-opt improvement of a closed tour [0, ..., 0]."""
    best = tour[:]
    improved = True
    while improved and time.perf_counter() < deadline:
        improved = False
        for i in range(1, len(best) - 2):
            for j in range(i + 1, len(best) - 1):
                a, b, c, d = best[i - 1], best[i], best[j], best[j + 1]
                if D[a, c] + D[b, d] < D[a, b] + D[c, d] - 1e-9:
                    best[i:j + 1] = best[i:j + 1][::-1]
                    improved = True
    return best


def tsp(D, nodes, deadline):
    """Nearest-neighbour + 2-opt tour from the depot through `nodes`."""
    left, tour = set(nodes), [0]
    while left:
        nxt = min(left, key=lambda j: (D[tour[-1], j], j))
        tour.append(nxt)
        left.remove(nxt)
    return two_opt(D, tour + [0], deadline)


def split_capacity(order, demand, cap):
    routes, cur, load = [], [], 0
    for t in order:
        if load + demand[t] > cap and cur:
            routes.append(cur)
            cur, load = [], 0
        cur.append(t)
        load += demand[t]
    if cur:
        routes.append(cur)
    return routes


def sweep(inst, D, demand, deadline):
    """Gillett–Miller sweep: polar order around the depot, split by capacity, 2-opt each route."""
    dx, dy = inst['depot']
    n = len(inst['tasks'])
    angles = {i: math.atan2(inst['tasks'][i - 1]['xy'][1] - dy, inst['tasks'][i - 1]['xy'][0] - dx) for i in range(1, n + 1)}
    best = None
    for start in sorted(angles, key=angles.get)[:: max(1, n // 12)]:
        order = sorted(angles, key=lambda i: (angles[i] - angles[start]) % (2 * math.pi))
        routes = [tsp(D, r, deadline)[1:-1] for r in split_capacity(order, demand, inst['vehicles']['capacity'])]
        cost = sum(_tour_cost(D, [0] + r + [0]) for r in routes)
        if best is None or cost < best[0]:
            best = (cost, routes)
    return best[1]


def cluster_first(inst, D, demand, deadline, seed):
    """Capacity-aware k-means on task positions (k = fleet size), then a 2-opt tour per cluster."""
    rng = np.random.default_rng(seed)
    K, cap = inst['vehicles']['count'], inst['vehicles']['capacity']
    X = np.array([t['xy'] for t in inst['tasks']], dtype=float)
    n = len(X)
    k = max(1, min(K, n))
    centers = X[rng.choice(n, k, replace=False)]
    for _ in range(30):
        order = np.argsort(-np.array([demand[i + 1] for i in range(n)]))
        loads, assign = np.zeros(k), np.zeros(n, dtype=int)
        for i in order:
            pref = np.argsort(np.hypot(*(centers - X[i]).T))
            limit = cap if inst['vehicles'].get('trips', 'multi') != 'multi' else math.inf
            choice = next((c for c in pref if loads[c] + demand[i + 1] <= limit), pref[0])
            assign[i] = choice
            loads[choice] += demand[i + 1]
        new = np.array([X[assign == c].mean(axis=0) if (assign == c).any() else centers[c] for c in range(k)])
        if np.allclose(new, centers):
            break
        centers = new
    routes, owner = [], []
    for c in range(k):
        members = [i + 1 for i in range(n) if assign[i] == c]
        for part in split_capacity(members, demand, cap):
            routes.append(tsp(D, part, deadline)[1:-1])
            owner.append(c)
    return routes, owner


def savings(inst, D, demand):
    """Clarke–Wright parallel savings on the obstacle-aware distance matrix."""
    cap = inst['vehicles']['capacity']
    n = len(inst['tasks'])
    route = {i: [i] for i in range(1, n + 1)}
    owner = {i: i for i in route}
    load = {i: demand[i] for i in route}
    pairs = sorted(((D[0, a] + D[0, b] - D[a, b], a, b) for a in range(1, n + 1) for b in range(a + 1, n + 1)), reverse=True)
    for s, a, b in pairs:
        if s <= 0:
            break
        ra, rb = owner[a], owner[b]
        if ra == rb or load[ra] + load[rb] > cap:
            continue
        A, B = route[ra], route[rb]
        if a not in (A[0], A[-1]) or b not in (B[0], B[-1]):
            continue
        if A[-1] != a:
            A = A[::-1]
        if B[0] != b:
            B = B[::-1]
        route[ra] = A + B
        load[ra] += load[rb]
        for t in B:
            owner[t] = ra
        del route[rb], load[rb]
    return list(route.values())


def _validated_reload_stops(r, trips, inst, home, errors):
    """Declared between-trip supply stops: None when absent/invalid, else the xy list to verify."""
    declared = r.get('reload_stops')
    if declared is None:
        return None
    if not isinstance(declared, list) or len(declared) != max(0, len(trips) - 1):
        errors.append(f"车辆 {r.get('vehicle_name')} 的补给停靠清单与分趟数不一致")
        return None
    valid_pts = [(float(p[0]), float(p[1])) for p in inst.get('unload_points') or []] + [tuple(home)]
    for p in declared:
        try:
            ok = len(p) == 2 and (float(p[0]), float(p[1])) in valid_pts
        except (TypeError, ValueError):
            ok = False
        if not ok:
            errors.append(f'车辆 {r.get("vehicle_name")} 声明了不在补给点/家库之列的停靠 {p}')
            return None
    return declared


def oriented_clearance(inst, polyline, footprint, obstacles, boundary, crops=()):
    """Post-hoc oriented-footprint clearance along a realised path (report only).

    Headings come from the polyline segment tangents; the footprint polygon is placed at
    sampled vertices and its minimum distance to obstacles/boundary is measured. The
    circumscribed-circle contract still governs acceptance — this quantifies how much
    conservative headroom the circle assumption leaves on the table."""
    from shapely import affinity
    from shapely.geometry import Point, Polygon
    poly0 = Polygon(footprint['points'])
    best = math.inf
    step = max(1, len(polyline) // 400)               # ≤ ~400 poses per route
    for i in range(0, len(polyline), step):
        x, y = polyline[i]
        nxt = polyline[min(i + 1, len(polyline) - 1)]
        prv = polyline[max(i - 1, 0)]
        yaw = math.atan2(nxt[1] - prv[1], nxt[0] - prv[0])
        pose_poly = affinity.translate(affinity.rotate(poly0, yaw, origin=(0, 0), use_radians=True), x, y)
        d = min([pose_poly.distance(o) for o in obstacles] + [pose_poly.distance(boundary.boundary)]
                + [pose_poly.distance(row) for row in crops])
        best = min(best, d)
    return None if best is math.inf else float(best)


def check_time_windows(inst, routes, errors, net=None):
    """Every windowed task's arrival, recomputed from the route geometry with the follower's time model, every vehicle moving
    at once. No time reported by a solver is read. A plan whose arrivals cannot be computed has not met its windows."""
    from ..field_coordination import arrivals
    tasks = {t['id']: t for t in inst['tasks']}
    try:
        found = arrivals(inst, routes, net)
        rows = [time_windows.row(tasks[stop['task']], v['label'], stop['arrive_s'], stop['start_s'])
                for v in found['vehicles'] for stop in v['stops'] if time_windows.of(tasks[stop['task']])]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        errors.append(f'时间窗未能校验：{exc}')
        return {'evaluated': False, 'reason': str(exc), 'late': []}
    seen = {r['task'] for r in rows}
    rows += [time_windows.row(t, '未分配', None) for t in inst['tasks'] if time_windows.of(t) and t['id'] not in seen]
    scope = {'coordinated': '规划模型：沿路径匀速行驶，拐角原地转向，任务驻留，从停车位驶出，路线相遇处等待；全部车辆同时出发',
             'route_only': f"规划模型：只沿路线本身计时，不含停车位驶出和路线相遇等待，因为{found.get('note')}"}[found['model']]
    record = {**time_windows.summary(rows, found['model'], scope, inst.get('start_clock')), 'execution_estimate': found.get('estimate')}
    for r in record['late'][:6]:
        errors.append(f"任务 {r['label']}（{r['vehicle']}）未分配，无法到达" if r['arrival_s'] is None else
                      f"任务 {r['label']}（{r['vehicle']}）预计 {r['arrival_s']:.1f} s 到达，晚于截止 {r['latest_s']:g} s（晚 {-r['slack_s']:.1f} s）")
    if len(record['late']) > 6:
        errors.append(f"另有 {len(record['late']) - 6} 个任务晚于截止时间")
    return record


def check(inst, routes, params=None):
    """Recompute task order, actual depot reloads, capacity, metrics and swept-radius clearance.

    Sequence/trip/service metadata are cross-checked against one another and the ordered geometry;
    none of them alone is a witness that a task was served. Invalid candidates remain diagnostics.
    Per-vehicle capacity, speed and footprint come from the route's fleet class (`vehicles.fleet`,
    synthesised as one class for legacy homogeneous instances). Task time windows are recomputed
    from the geometry by `check_time_windows`.
    """
    from shapely.geometry import LineString, Point, Polygon
    errors, details = [], []
    tasks = {t['id']: t for t in inst['tasks']}
    rows = inst['vehicles'].get('travel') == 'rows'
    net = lanes.Network(inst) if rows else None
    area = net.travel_area() if rows else None
    boundary = Polygon(inst['boundary'])
    obstacles = [Polygon(o['polygon']) for o in inst.get('obstacles', [])]
    crop_rows = net.rows() if rows else []
    classes = fleet_classes(inst)
    depots = depot_list(inst)
    nd = len(depots)
    table = {vi: (ci, cls, di) for vi, ci, cls, di in vehicle_table(inst)}
    class_use: Counter = Counter()
    seen, vehicles, total = Counter(), set(), 0.0
    if not routes:
        errors.append('方案没有路线')
    if len(routes) > inst['vehicles']['count']:
        errors.append(f"使用 {len(routes)} 台车，超过车队规模 {inst['vehicles']['count']}")
    for index, r in enumerate(routes):
        name = str(r.get('vehicle_name') or f'车{index + 1}')
        seq = r.get('sequence') or []
        seen.update(seq)
        if any(t not in tasks for t in seq):
            errors.append(f'车辆 {name} 引用了不存在的任务')
        vehicle = r.get('vehicle', index)
        if vehicle in vehicles:
            errors.append(f'车辆编号 {vehicle} 重复')
        vehicles.add(vehicle)
        entry = table.get(vehicle)
        if entry is None:
            errors.append(f'车辆编号 {vehicle} 超出车队台数 {inst["vehicles"]["count"]}')
            cls, home = classes[0], depots[0]
        else:
            cls, home = entry[1], depots[entry[2]]
            class_use[entry[0]] += 1
        if 'fleet_class' in r and str(r['fleet_class']) != str(cls['name']):
            errors.append(f"车辆 {name} 声明的车型类别 {r['fleet_class']} 与车队定义 {cls['name']} 不一致")
        for key, expected in (('capacity', cls['capacity']), ('speed_mps', cls['speed_mps']), ('robot_radius', cls['robot_radius'])):
            if key in r and not math.isclose(float(r[key]), float(expected), rel_tol=1e-9):
                errors.append(f'车辆 {name} 声明的 {key}（{r[key]}）与类别 {cls["name"]}（{expected}）不一致')
        trips = r.get('trips')
        if not isinstance(trips, list) or not trips or any(not isinstance(t, list) or not t for t in trips):
            errors.append(f'车辆 {name} 缺少有效的分趟清单')
            trips = [seq]
        if [t for trip in trips for t in trip] != seq:
            errors.append(f'车辆 {name} 的分趟清单与访问顺序不一致')
        if any(t not in tasks for trip in trips for t in trip):
            errors.append(f'车辆 {name} 的分趟清单含未知任务')
        load = max((sum(tasks[t]['demand'] for t in trip if t in tasks) for trip in trips), default=0)
        if load > cls['capacity']:
            errors.append(f"车辆 {name}（{cls['name']}）单趟载重 {load} 超过该类容量 {cls['capacity']}")
        if inst['vehicles'].get('trips', 'multi') != 'multi' and len(trips) > 1:
            errors.append(f'车辆 {name} 跑了 {len(trips)} 趟，但当前设置为单趟')
        services = r.get('services') or []
        if [sv.get('task') for sv in services] != seq:
            errors.append(f'车辆 {name} 的作业位置清单与访问顺序不一致')
        pts = r.get('polyline') or []
        try:
            valid_points = bool(pts) and all(len(p) == 2 and all(math.isfinite(float(x)) for x in p) for p in pts)
        except (TypeError, ValueError):
            valid_points = False
        if not valid_points:
            errors.append(f'车辆 {name} 缺少有限数值的路线几何')
            continue
        radius = float(cls['robot_radius'])
        lengths = arc_lengths(pts)
        length = lengths[-1]
        turn = float(cls.get('min_turn_radius_m') or 0)
        if 'min_turn_radius_m' in r and not math.isclose(float(r['min_turn_radius_m']), turn, rel_tol=1e-9):
            errors.append(f'车辆 {name} 声明的最小转弯半径（{r["min_turn_radius_m"]}）与类别 {cls["name"]}（{turn}）不一致')
        if turn > 0:
            spacing = max((math.dist(a, b) for a, b in zip(pts, pts[1:])), default=0.0)
            if spacing > 0.15:
                errors.append(f'车辆 {name} 的曲率路线采样间距 {spacing:.3f} m 过稀，不能据以复核转弯半径')
            sampled = min_curvature_radius(pts)
            if sampled < turn * 0.99:
                errors.append(f'车辆 {name}（{cls["name"]}）路线实测最小转弯半径 {sampled:.3f} m 小于类别约束 {turn} m')
        geom = LineString(pts) if len(pts) > 1 and length > 1e-12 else Point(pts[0])
        # Distance to original polygons avoids under-inflated round buffer approximations.
        clear = boundary.covers(geom) and geom.distance(boundary.boundary) >= radius - 1e-6
        clear = clear and all(geom.distance(o) >= radius - 1e-6 for o in obstacles)
        if not clear:
            errors.append(f'车辆 {name} 的机器人占用空间穿过障碍或越出边界')
        segs = [LineString([a, b]) for a, b in zip(pts, pts[1:]) if a != b]
        off = sum(not area.covers(seg) for seg in segs) if rows else 0
        if off:
            errors.append(f'车辆 {name} 有 {off} 段路线离开行间通道和地头，斜穿了作物')
        crop_clear = all(geom.distance(row) >= radius - 1e-6 for row in crop_rows)
        if not crop_clear:
            errors.append(f'车辆 {name} 的机器人占用空间侵入作物行')
        if math.dist(pts[0], home) > 1e-6 or math.dist(pts[-1], home) > 1e-6:
            errors.append(f'车辆 {name} 未从自己的车库出发并返回')
        if 'depot' in r and nd > 1:
            di = r.get('depot')
            if not isinstance(di, int) or isinstance(di, bool) or not 0 <= di < nd:
                errors.append(f'车辆 {name} 声明的车库编号 {di} 无效')
            elif 'depot_xy' in r and (math.dist(r['depot_xy'], depots[di]) > 1e-6 or math.dist(home, depots[di]) > 1e-6):
                errors.append(f'车辆 {name} 声明的车库坐标与车队分配不一致')
        # Locate each stop in sequence, including a real depot return between trips.
        cursor, served_count = 0.0, 0
        returns = list(np.cumsum([len(trip) for trip in trips]).tolist()[:-1])
        declared = _validated_reload_stops(r, trips, inst, home, errors)
        for sv in services:
            task = tasks.get(sv.get('task'))
            if task is None:
                continue
            stop = sv.get('stop') or task['xy']
            try:
                if len(stop) != 2 or not all(math.isfinite(float(x)) for x in stop):
                    raise ValueError
                nearest = net.attach(task['xy'])[2] if rows else 0.0
                if math.dist(stop, task['xy']) > nearest + 1e-5 or (rows and nearest > net.max_reach):
                    errors.append(f"车辆 {name} 任务 {task['id']} 的作业位置不可达或偏离最近可达处")
                at = locate(pts, stop, cursor)
                pos = sv.get('position')
                if pos is not None:
                    if not isinstance(pos, int) or isinstance(pos, bool) or not 0 <= pos < len(pts):
                        raise ValueError
                    at = lengths[pos] if math.dist(pts[pos], stop) <= 1e-5 and lengths[pos] >= cursor - 1e-5 else None
                if at is None:
                    errors.append(f"车辆 {name} 未按顺序经过任务 {task['id']} 的作业位置")
                else:
                    cursor = at
            except (ValueError, TypeError, IndexError):
                errors.append(f"车辆 {name} 任务 {task['id']} 的作业位置无效")
            served_count += 1
            if served_count in returns:
                stop_at = home if declared is None else declared[returns.index(served_count)]
                at = locate(pts, stop_at, cursor)
                if at is None:
                    errors.append(f'车辆 {name} 分趟之间未返回车库/补给点卸载')
                else:
                    cursor = at
        duration = length / cls['speed_mps'] + sum(tasks[t]['service_s'] for t in seq if t in tasks)
        for key, expected, label in [('cost', length, '里程'), ('load', load, '载重'), ('duration_s', duration, '用时')]:
            value = r.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not math.isclose(value, expected, rel_tol=1e-6, abs_tol=1e-5):
                errors.append(f'车辆 {name} 的{label}与独立复算不一致')
        from ..ground_risk import check_route_ground
        check_route_ground(r, inst, pts, errors, cls['speed_mps'], params)
        total += length
        oriented = None
        if isinstance(cls.get('footprint'), dict):
            try:
                oriented = oriented_clearance(inst, pts, cls['footprint'], obstacles, boundary, crop_rows)
            except Exception:                            # reporting only; never blocks the circle contract
                oriented = None
        details.append({'vehicle': name, 'fleet_class': cls['name'], 'load': load, 'length_m': length, 'duration_s': duration,
                        'collisions': int(not clear) + int(not crop_clear), 'off_network': off,
                        'oriented_clearance_m': oriented,
                        'oriented_scope': 'post-hoc pose-sampled polygon clearance; the circumscribed-circle contract still governs acceptance'
                        if oriented is not None else None})
    for ci, cls in enumerate(classes):
        if class_use[ci] > cls['count']:
            errors.append(f"车型类别 {cls['name']} 使用 {class_use[ci]} 台，超过该类台数 {cls['count']}")
    missing = sorted(set(tasks) - seen.keys())
    dup = sorted(t for t, count in seen.items() if count > 1)
    if missing:
        errors.append(f'{len(missing)} 个任务未被分配：{missing[:8]}')
    if dup:
        errors.append(f'任务重复服务：{dup[:8]}')
    windows = check_time_windows(inst, routes, errors, net) if time_windows.present(inst) else None
    return {'valid': not errors, 'errors': errors, 'computed_cost': total, 'routes': details,
            **({'time_windows': windows} if windows else {}),
            'checker': 'ceres_lab.solvers.fleet.check · 顺序/分趟/指标及连续圆足迹独立复算（Shapely）' + ('；到达时间按跟踪器时间模型复算' if windows else ''),
            'source': 'panel_baseline'}


# ------------------------------------------------------------------ solve --
def assign_trips(trip_times, K):
    """LPT assignment heuristic for the latest finish; not an exact makespan optimizer."""
    load, out = [0.0] * K, [[] for _ in range(K)]
    for i in sorted(range(len(trip_times)), key=lambda i: -trip_times[i]):
        v = min(range(K), key=lambda v: (load[v], v))
        out[v].append(i)
        load[v] += trip_times[i]
    return out


def dispatch_algorithm(inst, algorithm_id, D, demand, params, deadline, route_cap, K, multi, class_of, trace_diagnostics=None, graph=None, margin_s=0.0):
    """Run one fleet algorithm; returns (trips, owner, trip_class, trip_depot, trace)."""
    trace, owner, trip_class, trip_depot, reload_at = [], None, None, None, None
    trips = None
    windowed = time_windows.present(inst)
    if algorithm_id == 'pyvrp.fleet':
        trips, trip_class, trip_depot, trace = metaheuristic('pyvrp', inst, D, demand, params, route_cap)
    elif algorithm_id == 'pyvrp.balanced':
        trips, owner, reload_at, trace = pyvrp_makespan(inst, D, demand, params, K, multi, graph, margin_s)
        trip_class = [class_of[v] for v in owner] if owner is not None else None
        trip_depot = [vehicle_table(inst)[v][3] for v in owner] if owner is not None else None
    elif algorithm_id == 'ortools.fleet' and (inst.get('objective') == 'makespan' or windowed):
        trips, owner, reload_at, trace = ortools_makespan(inst, D, demand, params, K, multi, trace_diagnostics=trace_diagnostics,
                                                          graph=graph if windowed else None, margin_s=margin_s)
        trip_class = [class_of[v] for v in owner] if owner is not None else None
        trip_depot = [v % len(depot_list(inst)) for v in owner] if owner is not None else None
    elif algorithm_id == 'ortools.fleet':
        trips, trip_class, trip_depot, trace = metaheuristic('ortools', inst, D, demand, params, route_cap)
    elif algorithm_id == 'panel.sweep':
        trips = sweep(inst, D, demand, deadline)
    elif algorithm_id == 'panel.cluster_first':
        trips, owner = cluster_first(inst, D, demand, deadline, int(params.get('seed', 42)))
    elif algorithm_id == 'panel.savings_fleet':
        trips = [two_opt(D, [0] + r + [0], deadline)[1:-1] for r in savings(inst, D, demand)]
    else:
        raise ValueError(f'未知算法 {algorithm_id}')
    return trips, owner, trip_class, trip_depot, reload_at, trace


def unreachable_tasks(inst, D, nd):
    """Task labels with no finite path from any depot."""
    return [inst['tasks'][i - nd]['label'] for i in range(nd, nd + len(inst['tasks']))
            if not any(np.isfinite(D[d0, i]) for d0 in range(nd))]


def beyond_reach(inst, D, nd, classes):
    """Tasks no vehicle can reach in time even going straight there from the nearest depot at the fleet's top speed. The
    plan need not be searched for: this proves that the windows, not a solver, are what fails."""
    fastest = max(c['speed_mps'] for c in classes)
    rows = []
    for i, t in enumerate(inst['tasks']):
        window = time_windows.of(t)
        if window and window[1] is not None:
            need = float(min(D[d, nd + i] for d in range(nd))) / fastest
            if need > window[1] + time_windows.TOLERANCE_S:
                rows.append({'task': t['id'], 'label': t['label'], 'need_s': round(need, 1), 'latest_s': window[1]})
    return rows


def _worst_late(chk) -> float | None:
    """How far past its deadline the latest task lands, when lateness is what a plan failed on."""
    late = ((chk or {}).get('time_windows') or {}).get('late') or []
    misses = [-r['slack_s'] for r in late if r['slack_s'] is not None]
    return max(misses) if misses else None


def solve(scenario, inst, algorithm_id, params):
    """Assign, route and check. A task with a time window is judged by the checker's arrival, which also counts what a search
    matrix cannot: leaving the parking spot, turns between legs, waiting where routes meet. So the search runs in rounds, each
    moving the deadlines up by what the checker found, and the budget is shared between them."""
    if not time_windows.present(inst):
        return _solve(scenario, inst, algorithm_id, params)
    if TIME_WINDOW_SUPPORT.get(algorithm_id) == 'unsupported':
        raise ValueError(f'{algorithm_id} 不适用于带时间窗的任务：它不跟踪到达时刻；请用 pyvrp.balanced 或 ortools.fleet')
    if str(params.get('ground_policy', 'ignore')) == 'risk_adjust':
        raise ValueError('时间窗不适用于地面风险限速（ground_policy=risk_adjust）：到达时间模型不含分区限速')
    total, started = float(params.get('time_limit_s', 3)), time.perf_counter()
    graph = travel_graph(inst)
    margin, rounds, best, checking = WINDOW_MARGIN_S, [], None, 0.0
    for share in WINDOW_ROUNDS:
        left = total - (time.perf_counter() - started) - checking     # a round is not finished until its plan is checked
        if best is not None and left < .1:
            break
        search_s = max(.1, min(left, total * share))
        begun = time.perf_counter()
        outcome = _solve(scenario, inst, algorithm_id, {**params, 'time_limit_s': search_s}, graph, margin)
        checking = max(checking, time.perf_counter() - begun - search_s)
        status, chk = outcome[4], outcome[1]
        worst = _worst_late(chk)
        late = len(((chk or {}).get('time_windows') or {}).get('late') or [])
        rounds.append({'margin_s': round(margin, 2), 'status': status, 'late_tasks': late, 'worst_late_s': worst})
        if best is None or status == 'feasible' or worst is not None and late < best[0]:
            best = late, outcome
        if status == 'feasible' or worst is None:
            break
        margin += worst * 1.1 + 1.0
    routes, chk, metrics, raw, status, trace = best[1]
    raw = {**raw, 'time_window_rounds': rounds}
    if status == 'no_solution' and _worst_late(chk) is not None:
        raw['reason'] = f"没有找到全部按时的方案：{time_windows.late_text(chk['time_windows'])}"
    return routes, chk, metrics, raw, status, trace


def _solve(scenario, inst, algorithm_id, params, windows_graph=None, margin_s=0.0):
    t0 = time.perf_counter()
    from .. import ground_risk
    ground_policy, risk_weight, ground = ground_risk.resolve_policy(inst, params)
    ground_policy_active = ground_policy == 'risk_adjust'
    graph = windows_graph or (travel_graph(inst, ground['zones'], risk_weight) if ground_policy_active
                              else travel_graph(inst))
    D = graph.D
    n = len(inst['tasks'])
    depots = depot_list(inst)
    nd = len(depots)
    unreachable = unreachable_tasks(inst, D, nd)
    if unreachable:
        raise ValueError(f'有任务点与所有车库不连通（被障碍{"或作物行" if graph.travel == "rows" else ""}隔断）：{unreachable[:6]}')
    demand = [0] * nd + [t['demand'] for t in inst['tasks']]
    K = inst['vehicles']['count']
    classes = fleet_classes(inst)
    hetero = len(classes) > 1
    multi_depot = nd > 1
    vtable = vehicle_table(inst)
    class_of = [ci for vi, ci, _cls, _di in vtable]
    depot_of = [di for vi, _ci, _cls, di in vtable]
    multi = inst['vehicles'].get('trips', 'multi') == 'multi'
    fleet_cap = sum(c['count'] * c['capacity'] for c in classes)
    if not multi and sum(demand) > fleet_cap:
        return [], None, {'fleet_capacity': fleet_cap, 'total_demand': sum(demand)}, \
            {'reason': f'总需求 {sum(demand)} 超过车队单趟总容量 {fleet_cap}'
                       + ('（按各类别台数 × 容量合计）' if hetero else f'（{K} × {classes[0]["capacity"]}）')
                       + '；可允许多趟往返或增加车辆/容量'}, 'infeasible', []
    unreachable_in_time = beyond_reach(inst, D, nd, classes)
    if unreachable_in_time:
        named = '、'.join(f"任务 {r['label']} 从车库直达至少 {r['need_s']:g} s，晚于截止 {r['latest_s']:g} s" for r in unreachable_in_time[:6])
        return [], None, {}, {'reason': named + (f" 等 {len(unreachable_in_time)} 个" if len(unreachable_in_time) > 6 else ''),
                              'time_window_unreachable': unreachable_in_time}, 'infeasible', []
    if hetero and algorithm_id in ('panel.sweep', 'panel.cluster_first', 'panel.savings_fleet'):
        raise ValueError('构造基线（sweep / cluster_first / savings）只支持同构车队；异构车队请用 pyvrp.fleet 或 ortools.fleet')
    if multi_depot and algorithm_id.startswith('panel.'):
        raise ValueError('构造基线（sweep / cluster_first / savings）只支持单车库；多车库请用 pyvrp.fleet 或 ortools.fleet')
    deadline = time.perf_counter() + float(params.get('time_limit_s', 3))
    route_cap = n if multi else K
    trace_diagnostics = {}
    trips, owner, trip_class, trip_depot, reload_at, trace = dispatch_algorithm(
        inst, algorithm_id, D, demand, params, deadline, route_cap, K, multi, class_of, trace_diagnostics=trace_diagnostics, graph=graph, margin_s=margin_s)
    if not trips:
        return [], None, {}, {'reason': '求解器没有返回可行方案', 'trace_diagnostics': trace_diagnostics}, 'no_solution', trace
    reload_at = reload_at if reload_at is not None else [None] * len(trips)
    if trip_class is None:
        trip_class = [0] * len(trips)          # constructive baselines run on homogeneous fleets only
    if trip_depot is None:
        trip_depot = [0] * len(trips)          # makespan keeps the (single) home depot
    trip_len = [float(sum(D[a, b] for a, b in zip([trip_depot[i]] + r + [trip_depot[i]], r + [trip_depot[i]])))
                for i, r in enumerate(trips)]
    trip_time = [L / classes[ci]['speed_mps'] + sum(inst['tasks'][i - nd].get('service_s', 0) for i in r)
                 for L, r, ci in zip(trip_len, trips, trip_class)]
    if owner is not None:
        plan = [[i for i, o in enumerate(owner) if o == v] for v in range(K)]
    elif hetero or multi_depot:
        plan = [[] for _ in range(K)]
        depot_of = [di for _vi, _ci, _cls, di in vehicle_table(inst)]
        keys = list(dict.fromkeys(zip(class_of, depot_of)))
        vehicles_of = {key: [vi for vi in range(K) if (class_of[vi], depot_of[vi]) == key] for key in keys}
        if multi:
            for key in keys:
                mine = [i for i, k in enumerate(zip(trip_class, trip_depot)) if k == key]
                buckets = assign_trips([trip_time[i] for i in mine], max(1, len(vehicles_of[key])))
                for vi, bucket in zip(vehicles_of[key] + [None] * len(buckets), buckets):
                    if vi is not None:
                        plan[vi] = [mine[i] for i in bucket]
        else:
            free = {key: list(reversed(vehicles_of[key])) for key in keys}
            for i, key in enumerate(zip(trip_class, trip_depot)):
                if not free.get(key):
                    break                        # more trips of this class/depot than vehicles: the checker reports it
                plan[free[key].pop()].append(i)
    elif multi or len(trips) <= K:
        plan = assign_trips(trip_time, K) if multi else [[i] for i in range(len(trips))]
    else:
        plan = [[i] for i in range(len(trips))]  # more single trips than vehicles: the checker reports it
    names = inst['vehicles'].get('names') or []
    table = {vi: cls for vi, _ci, cls, _di in vtable}
    def build_routes(corner_shift=0.0, use_ompl=False):
        routes = []
        for vi, mine in enumerate(plan):
          if not mine:
              continue
          cls = table.get(vi) or classes[0]
          home = depot_of[vi] if multi_depot else 0
          boundaries = [home if reload_at[i] is None else reload_at[i] for i in mine]
          # between-trip supply stops; the mission always ends back at the home depot
          stops = [home] + [s for i, bnd in zip(mine, boundaries) for s in trips[i] + [bnd]] + [home]
          waypoints, service_waypoints = [], []
          for a, b in zip(stops, stops[1:]):
              seg = graph.path(a, b)
              waypoints += seg if not waypoints else seg[1:]
              if nd <= b < nd + n:                    # services sit on task nodes only
                  service_waypoints.append(len(waypoints) - 1)
          turn = float(cls.get('min_turn_radius_m') or 0)
          if turn > 0 and use_ompl:
            clean = [stops[0]] + [b for a, b in zip(stops, stops[1:]) if b != a]      # zero-length legs are unplannable
            stops_xy = [list(graph.points[s])[:2] for s in clean]
            pts = ompl_dubins_fallback(inst, stops_xy, turn, budget_s=3.0)
            if pts is None:
                return None
            task_xys = [list(graph.points[s])[:2] for s in clean if nd <= s < nd + n]
            service_positions = [min(range(len(pts)), key=lambda i: math.dist(pts[i], xy)) for xy in task_xys]
          elif turn > 0:
              pts, marks = dubins_realize(relax_corners(waypoints, service_waypoints, corner_shift * turn), turn)
              service_positions = [marks[wi] for wi in service_waypoints]
          else:
              pts, service_positions = waypoints, service_waypoints
          length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
          service_total = sum(inst['tasks'][s - nd]['service_s'] for i in mine for s in trips[i])
          route = {'vehicle': vi, 'vehicle_name': names[vi] if vi < len(names) else f'车{vi + 1}', 'color': COLORS[vi % len(COLORS)],
                   'nodes': None, 'sequence': [inst['tasks'][s - nd]['id'] for i in mine for s in trips[i]],
                   'trips': [[inst['tasks'][s - nd]['id'] for s in trips[i]] for i in mine],
                   'services': [{'task': inst['tasks'][s - nd]['id'], 'position': pos, 'stop': list(graph.points[s])}
                                for s, pos in zip([s for i in mine for s in trips[i]], service_positions)],
                   'polyline': pts, 'cost': length,
                   'load': max(sum(demand[s] for s in trips[i]) for i in mine),
                   'duration_s': length / cls['speed_mps'] + service_total}
          if hetero:
              route.update(fleet_class=cls['name'], capacity=cls['capacity'],
                           speed_mps=cls['speed_mps'], robot_radius=cls['robot_radius'])
          if turn > 0:
              route.update(min_turn_radius_m=turn, curvature_radius_m=round(min_curvature_radius(pts), 4))
          if multi_depot:
              route.update(depot=home, depot_xy=[float(depots[home][0]), float(depots[home][1])])
          if any(bnd != home for bnd in boundaries[:-1]):
              route.update(reload_stops=[[float(graph.points[bnd][0]), float(graph.points[bnd][1])]
                                         for bnd in boundaries[:-1]])
          if ground is not None:
              # exposure stats are evaluation evidence for every policy; speed caps
              # execute only when the policy actually used the declared map
              ground_risk.attach_route_ground(route, pts, ground, ground_policy_active, cls['speed_mps'], risk_weight)
          routes.append(route)
        return routes

    any_curvature = any(float(c.get('min_turn_radius_m') or 0) > 0 for c in classes)
    attempts, chk = [], None
    # analytic realisation first (fast, deterministic); the sampling fallback (OMPL RRT-Connect on
    # DubinsStateSpace) can weave compound manoeuvres through gaps the waypoint scheme cannot.
    ladder = ([('analytic', 0.0), ('analytic', 0.6), ('analytic', 1.2), ('ompl_rrt_connect', None)]
              if any_curvature else [('analytic', 0.0)])
    for planner, shift in ladder:
        routes = build_routes(0.0 if shift is None else shift, use_ompl=planner != 'analytic')
        if routes is None:
            attempts.append({'planner': planner, 'corner_shift_m': shift, 'result': 'planner found no path'})
            continue
        chk = check(inst, routes, params)
        attempts.append({'planner': planner, 'corner_shift_m': shift,
                         'valid': chk['valid'], 'length_m': round(sum(r['cost'] for r in routes), 3)})
        if chk['valid']:
            break
    if not routes or chk is None:
        routes = routes or []
        chk = check(inst, routes, params)
    if not routes:
        return [], chk, {'total_distance_m': 0.0}, {'reason': '候选方案未通过独立检查，不能作为可行方案执行',
                                                    'curvature_realization_attempts': attempts}, 'no_solution', trace
    lengths = [r['cost'] for r in routes]
    euclid = sum(float(graph.euclid[a, b]) for i, r in enumerate(trips)
                 for a, b in zip([trip_depot[i]] + r, r + [trip_depot[i]]))
    trip_capacity = sum(classes[ci]['capacity'] for ci in trip_class)
    metrics = {'total_distance_m': sum(lengths), 'makespan_s': max(r['duration_s'] for r in routes),
               'longest_route_m': max(lengths), 'vehicles_used': len(routes), 'fleet_size': K, 'trips': len(trips),
               'balance_cv': float(np.std(lengths) / np.mean(lengths)) if len(lengths) > 1 and np.mean(lengths) else 0.0,
               'capacity_utilization': sum(demand) / trip_capacity if trip_capacity else 1.0,
               'detour_ratio': sum(lengths) / euclid if euclid else 1.0,
               'graph_nodes': len(graph.nodes), 'graph_build_s': graph.build_s, 'objective_mode': inst.get('objective', 'total_distance'),
               'travel': graph.travel,
               'curvature_constrained_vehicles': sum(1 for r in routes if r.get('min_turn_radius_m')),
               'per_vehicle': [{'vehicle': r['vehicle_name'],
                                **({'fleet_class': r['fleet_class']} if hetero else {}),
                                **({'min_turn_radius_m': r['min_turn_radius_m']} if r.get('min_turn_radius_m') else {}),
                                'color': r['color'], 'tasks': len(r['sequence']), 'trips': len(r['trips']),
                                'load': r['load'], 'length_m': r['cost'], 'duration_s': r['duration_s']} for r in routes]}
    windows = chk.get('time_windows')
    if windows and windows.get('evaluated'):
        metrics.update(time_window_tasks=windows['total'], time_window_on_time=windows['on_time'], time_window_worst_slack_s=windows['worst_slack_s'])
    if ground:
        metrics.update(ground_risk_total_cost=sum(r['risk_cost'] for r in routes), ground_policy=ground_policy,
                       ground_risk_weight=risk_weight, search_objective='risk_adjusted_metres' if ground_policy_active else 'geometric_metres')
    if graph.travel == 'rows':
        metrics.update(lanes=graph.info['lanes'], service_offset_mean_m=graph.info['service_offset_mean_m'],
                       service_offset_max_m=graph.info['service_offset_max_m'])
    model = ('沿作物行间通道、地头与机耕路的通行图最短路：作物行不可穿越，任务使用邻近通道停车访问代理；未验证作业具可达或农业作业质量'
             if graph.travel == 'rows' else '机器人半径膨胀障碍上的可视图最短路（候选图上的最短路；不保证连续空间全局最短）')
    raw = {'trips': trips, 'vehicle_plan': plan, 'distance_model': model, 'travel_network': graph.info or None,
           'curvature_realization_attempts': attempts if len(attempts) > 1 else None,
           'search_scale': 'PyVRP / OR-Tools 用厘米取整距离搜索，结果按米复算', 'planning_s': time.perf_counter() - t0,
           'trip_assignment': ('OR-Tools 直接为各车安排往返（车库卸载点），时间窗作用在时间维度上'
                               if algorithm_id == 'ortools.fleet' and windows
                               else 'OR-Tools 直接为各车安排往返（车库卸载点），时间维度跨度代价最小化最晚完成'
                               if algorithm_id == 'ortools.fleet' and inst.get('objective') == 'makespan'
                               else 'PyVRP 在同一搜索里安排回库重载和各趟归属，班次上限二分收紧，用时按跟踪器实际行驶估计' if algorithm_id == 'pyvrp.balanced'
                               else '多趟：先求各趟路线，再按最长处理时间优先分配到各车' if multi else '单趟：每车一条路线')}
    if trace_diagnostics:
        raw['trace_diagnostics'] = trace_diagnostics
    raw['model_contract'] = {
        'schema': 'urp.fleet-model/2', 'frame': 'local planar metres',
        'geometry': {'footprint': 'circumscribed circle', 'radius_m': inst['robot_radius'],
                     'path': ('piecewise Dubins shortest paths between carried-heading waypoints, curvature bounded by each '
                              "class's min_turn_radius_m (Dubins 1957; OMPL DubinsStateSpace); forward motion only, no "
                              'Reeds–Shepp reversing; assignment optimised on straight-line graph distances, realised '
                              'curved lengths are reported as executed metrics'
                              if any(float(c.get('min_turn_radius_m') or 0) > 0 for c in classes)
                              else 'piecewise linear shortest paths on constructed graph; no curvature optimization'),
                     'curvature_bounded': any(float(c.get('min_turn_radius_m') or 0) > 0 for c in classes)},
        'fleet': {'type': 'heterogeneous' if hetero else 'homogeneous',
                  **({'depots': [{'index': i, 'xy': [float(d[0]), float(d[1])]} for i, d in enumerate(depots)],
                      'depot_assignment': 'vehicles round-robin over depots in table order; start and finish at home; optional supply stops in makespan mode',
                      'unsupported': 'constructive baselines reject multi-depot instances'}
                     if multi_depot else {}),
                  **({'classes': [{'name': c['name'], 'count': c['count'], 'capacity': c['capacity'],
                                   'speed_mps': c['speed_mps'], 'robot_radius': c['robot_radius'], 'payload': c['payload']}
                                  for c in classes],
                      'note': '通行图与障碍膨胀按车队最大足迹半径；独立检查器按各车自身足迹复算净空'}
                     if hetero else {}),
                  'capacity_unit': 'abstract demand units, not kg',
                  'reload': 'instantaneous at home or declared supply points in OR-Tools makespan; distance mode uses home only; no handling duration or payload dynamics'},
        'task': {'success': 'visit assigned access location and dwell; no tool, crop interaction or work-quality model',
                 'access': 'nearest lane/headland proxy' if graph.travel == 'rows' else 'task coordinate',
                 'physical_reach_verified': False, 'max_geometric_offset_m': max(getattr(graph, 'offsets', [0]) or [0])},
        'time': {'model': 'distance/speed + requested service time', 'acceleration': False,
                 'turns': False, 'parking': False, 'traffic_conflicts': False,
                 'scope': 'constant-speed planning proxy, not physically predicted completion time',
                 **({'time_windows': {'bounds': 'arrival at the task, seconds after the vehicles are released', 'early_arrival': 'waits at the task',
                                      'search': 'tw_early / tw_late on follower drive time' if algorithm_id == 'pyvrp.balanced' else 'time dimension on follower drive time',
                                      'judged_by': 'checker arrival: follower time model, parking, turns, waits where routes meet, every vehicle moving at once',
                                      'deadline_margin_s': round(margin_s, 2)}} if windows else {})},
        'optimization': {'reported_objective': inst.get('objective', 'total_distance'),
                         'search': 'declared ground-risk equivalent metres' if ground_policy_active else 'distance on follower drive time within task windows' if windows and inst.get('objective') != 'makespan' else 'distance under a bisected shift limit on follower drive time' if algorithm_id == 'pyvrp.balanced' else 'weighted distance and route-span surrogate' if algorithm_id == 'ortools.fleet' and inst.get('objective') == 'makespan'
                         else 'distance routing then trip allocation',
                         'trip_allocation': 'PyVRP multi-trip routes' if algorithm_id == 'pyvrp.balanced' else 'OR-Tools assignment' if owner is not None else 'LPT heuristic' if multi else 'one trip per vehicle',
                         'optimality_proved': False}}
    if not chk['valid']:
        raw['rejected_routes'] = routes
        raw['reason'] = '候选方案未通过独立检查，不能作为可行方案执行'
        return [], chk, metrics, raw, 'no_solution', trace
    return routes, chk, metrics, raw, 'feasible', trace


def objective_value(inst, metrics):
    return metrics['makespan_s'] if inst.get('objective') == 'makespan' else metrics['total_distance_m']
