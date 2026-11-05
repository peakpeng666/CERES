"""Multi-vehicle task allocation and routing on 2-D maps with obstacles (problem class `fleet_routing`).

Distances are shortest paths on the constructed travel graph (`vehicles.travel`):
- `free`: anywhere clear of obstacles — a visibility graph over the robot-inflated obstacles (exact for
  the generated visibility graph);
- `rows`: along the lanes between crop rows, entering and leaving them at the headland or a farm road,
  never across rows (`urp_lab.lanes`); each task is served from the nearest point of that network.
All-pairs Dijkstra runs between the depot and the service positions. Solvers decide assignment and order;
routes are realised along those shortest paths and re-checked against the map independently.
"""
from __future__ import annotations

import math
import time
from collections import Counter, defaultdict

import numpy as np

from .. import lanes
from ..lanes import free_space
from ..path_geometry import arc_lengths, locate
from .fleet_common import depot_list, fleet_classes, vehicle_table  # fleet shape helpers (2026-10-17 拆分)
from .fleet_curvature import (  # curvature realisation stack (2026-10-17 拆分)
    _circumradius, dubins_realize, min_curvature_radius, ompl_dubins_fallback, relax_corners)
from .fleet_engines import metaheuristic, ortools_makespan  # PyVRP / OR-Tools engines (2026-10-17 拆分)

COLORS = ['#007aff', '#ff6b00', '#34a853', '#a142f4', '#e8336d', '#00a6a6', '#c49000', '#5856d6', '#8e5a2b', '#2f7d32',
          '#d81b60', '#1e88e5']


# ------------------------------------------------------------------- fleet --
class Graph:
    """Visibility graph between depot/tasks and obstacle/boundary corners, with all-pairs shortest paths."""

    travel = 'free'
    info: dict = {}

    def __init__(self, inst, ground_zones=None, risk_weight=0.0):
        import shapely
        from shapely.geometry import Point, Polygon
        from shapely.prepared import prep
        t0 = time.perf_counter()
        self.free, self.boundary, _ = free_space(inst)
        self.points = ([tuple(d) for d in depot_list(inst)] + [tuple(t['xy']) for t in inst['tasks']]
                       + [tuple(p) for p in inst.get('unload_points') or []])
        r = inst['robot_radius']
        from ..ground_risk import normalize_ground
        _gnd = normalize_ground(inst) if inst.get('ground') else None
        mu_ref = _gnd['mu_reference'] if _gnd else 1.0
        self.ground = _gnd
        corners = []
        for o in inst.get('obstacles') or []:
            ring = Polygon(o['polygon']).buffer(r + 0.05, quad_segs=2, join_style=2).simplify(0.05)
            corners += list(ring.exterior.coords)[:-1]
        # Known-map ground zones contribute inflated corner nodes so a detour around a
        # low-friction patch is representable in the graph. Zones stay traversable —
        # their edges just carry the risk weight; obstacles remain hard exclusions.
        for z in ground_zones or []:
            x0, y0, x1, y1 = z['rect']
            # Detour corners sit outside the footprint-aware risk band so a detour is
            # actually risk-free at wheel level, not just at centre-line level.
            m = r + 0.5  # 0.5 m steering clearance beyond the footprint band
            corners += [(x0 - m, y0 - m), (x1 + m, y0 - m), (x1 + m, y1 + m), (x0 - m, y1 + m)]
        inner = Polygon(inst['boundary']).buffer(-(r + 0.05), join_style=2)
        if not inner.is_empty and inner.geom_type == 'Polygon' and inner.area < inner.convex_hull.area - 1e-6:
            corners += [p for p in list(inner.exterior.coords)[:-1]]
        pfree = prep(self.free.buffer(1e-6))
        corners = [c for c in corners if pfree.contains(Point(c))]
        self.nodes = self.points + corners
        n = len(self.nodes)
        P = np.array(self.nodes, dtype=float)
        W = np.full((n, n), np.inf)
        simple = not inst.get('obstacles') and self.boundary.equals(self.boundary.convex_hull)
        # Match the independent fleet checker's metre tolerance. The polygonal
        # round buffer is only a fast broad-phase filter, not a clearance proof.
        clearance = r - 1e-6
        obstacle_polygons = [Polygon(o['polygon']) for o in inst.get('obstacles') or []]
        boundary_ring = self.boundary.boundary
        if simple:
            # In a convex obstacle-free boundary, the disc-clear centre domain
            # is convex: safe endpoints suffice, retaining the cheap fast path.
            node_points = shapely.points(P)
            safe_nodes = (shapely.covers(self.boundary, node_points)
                          & (shapely.distance(node_points, boundary_ring) >= clearance))
        np.fill_diagonal(W, 0.0)
        ii, jj = np.triu_indices(n, 1)
        # Batch GEOS predicates instead of constructing/calling a Python geometry
        # for every pair. Bound each batch to avoid quadratic temporary geometries.
        for start in range(0, len(ii), 8192):
            i, j = ii[start:start + 8192], jj[start:start + 8192]
            if simple:
                visible = safe_nodes[i] & safe_nodes[j]
                i, j = i[visible], j[visible]
            else:
                segments = shapely.linestrings(np.stack((P[i], P[j]), axis=1))
                # Co-located service nodes are genuine zero-length edges; use
                # points rather than invalid degenerate LineStrings in GEOS.
                same = np.all(P[i] == P[j], axis=1)
                segments[same] = shapely.points(P[i[same]])
                visible = shapely.covers(pfree.context, segments)
                i, j, segments = i[visible], j[visible], segments[visible]
                visible = (shapely.covers(self.boundary, segments)
                           & (shapely.distance(segments, boundary_ring) >= clearance))
                # Keep GEOS operations batched without an edges × obstacles
                # temporary array. Check original polygons, not inscribed arcs.
                for obstacle in obstacle_polygons:
                    visible &= shapely.distance(segments, obstacle) >= clearance
                i, j = i[visible], j[visible]
            W[i, j] = W[j, i] = np.hypot(*(P[i] - P[j]).T)
        if ground_zones and risk_weight > 0:
            # Known-map ground risk: solver weight = geometric length × (1 + w·μ-deficit
            # fraction). Units stay metres (equivalent metres); geometric D is recomputed
            # downstream for reporting. Detours arise through pred from these weights.
            from ..ground_risk import risk_factor
            from shapely.geometry import LineString
            geo = W.copy()
            for i in range(n):
                for j in range(i + 1, n):
                    if not math.isfinite(W[i, j]) or W[i, j] < 1e-9:
                        continue
                    f = 1.0 + risk_weight * risk_factor((self.nodes[i], self.nodes[j]), ground_zones, mu_ref)
                    W[i, j] = W[j, i] = geo[i, j] * f
            self.geo = geo
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra
        # Explicit sparse zero entries are real edges between co-located tasks/depot.
        # Dense-to-sparse conversion drops them and invents positive detours.
        ii, jj = np.where(np.isfinite(W) & ~np.eye(n, dtype=bool))
        mat = csr_matrix((W[ii, jj], (ii, jj)), shape=(n, n))
        k = len(self.points)
        self.dist, self.pred = dijkstra(mat, directed=False, indices=list(range(k)), return_predecessors=True)
        self.D = self.dist[:, :k]
        self.build_s = time.perf_counter() - t0
        self.euclid = np.hypot(*(P[:k, None, :] - P[None, :k, :]).transpose(2, 0, 1))

    def path(self, a: int, b: int) -> list[list[float]]:
        if a == b:
            return [list(self.points[a])]
        seq, cur = [b], b
        while cur != a:
            cur = int(self.pred[a, cur])
            if cur < 0:
                raise ValueError('两点之间不连通（被障碍隔断）')
            seq.append(cur)
        return [list(self.nodes[i]) for i in reversed(seq)]

    def route_polyline(self, stops: list[int]) -> list[list[float]]:
        pts = []
        for a, b in zip(stops, stops[1:]):
            seg = self.path(a, b)
            pts += seg if not pts else seg[1:]
        return pts


class LaneGraph(Graph):
    """Travel graph along crop-row lanes, headlands and farm roads (`vehicles.travel = 'rows'`).

    Nodes: the depot, one service position per task (on its nearest lane or in the open area), the lane
    entries and the corners of the open area. Lane edges join consecutive nodes along each lane; edges in
    the open area are straight segments that stay inside it (every pair involving the depot, open-area
    stops or corners; between entries closer than `NEAR_M`)."""

    travel = 'rows'
    NEAR_M = 12.0

    def __init__(self, inst):
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import dijkstra
        from scipy.spatial import cKDTree
        from shapely.geometry import Point
        t0 = time.perf_counter()
        net = self.net = lanes.Network(inst)
        self.free, self.boundary = net.free, net.boundary
        for di, d in enumerate(depot_list(inst)):
            if not net.in_open((float(d[0]), float(d[1]))):
                raise ValueError(f'车库 {di + 1} 在作物行里：按行间通行时车辆从地头或机耕路出发，请把车库移到地头，或改为自由通行')
        for ui, p in enumerate(inst.get('unload_points') or []):
            if not net.in_open((float(p[0]), float(p[1]))):
                raise ValueError(f'补给点 {ui + 1} 在作物行里：请把它移到地头或机耕道')
        nodes, special, on_lane, self.offsets = [tuple(depot_list(inst)[0])], [0], defaultdict(list), []
        for d in depot_list(inst)[1:]:
            special.append(len(nodes))
            nodes.append(tuple(d))
        for t in inst['tasks']:
            stop, lane, d = net.attach(t['xy'])
            if d > net.max_reach:
                raise ValueError(f"任务点 {t.get('label', t['id'])} 离最近的行间通道或地头 {d:.1f} m，按行间通行够不着；"
                                 '请把它移近作物行间，或改为自由通行')
            (on_lane[lane] if lane is not None else special).append(len(nodes))
            nodes.append(stop)
            self.offsets.append(d)
        for p in inst.get('unload_points') or []:
            special.append(len(nodes))
            nodes.append(tuple(p))
        self.points = list(nodes)
        k = len(nodes)
        edges: dict[tuple[int, int], float] = {}

        def link(i, j, w):
            key = (i, j) if i < j else (j, i)
            if i != j and w < edges.get(key, math.inf):
                edges[key] = float(w)
        entries = []
        for li, lane in enumerate(net.lanes):
            chain = [(lane.line.project(Point(nodes[i])), i) for i in on_lane.get(li, [])]
            for flag, xy, at in ((lane.entry_start, lane.start, 0.0), (lane.entry_end, lane.end, lane.line.length)):
                if flag:
                    entries.append(len(nodes))
                    chain.append((at, len(nodes)))
                    nodes.append(xy)
            chain.sort()
            for (s0, i), (s1, j) in zip(chain, chain[1:]):
                link(i, j, s1 - s0)
        corners = net.hubs()
        hubs = list(range(len(nodes), len(nodes) + len(corners)))
        nodes += corners
        P = np.array(nodes, dtype=float)
        pairs = {(min(i, j), max(i, j)) for i in special for j in special + entries + hubs if i != j}
        pairs |= {(i, j) for a, i in enumerate(hubs) for j in hubs[a + 1:]}
        pairs |= {(min(i, j), max(i, j)) for i in entries for j in hubs}
        if len(entries) > 1:
            near = cKDTree(P[entries]).query_pairs(self.NEAR_M)
            pairs |= {(min(entries[a], entries[b]), max(entries[a], entries[b])) for a, b in near}
        if pairs:
            ij = np.array(sorted(pairs))
            seen = net.visible(P[ij[:, 0]], P[ij[:, 1]])
            for (i, j), w in zip(ij[seen], np.hypot(*(P[ij[seen, 0]] - P[ij[seen, 1]]).T)):
                link(int(i), int(j), w)
        self.nodes = nodes
        n = len(nodes)
        if edges:
            ij = np.array(list(edges.keys()))
            mat = coo_matrix((np.array(list(edges.values())), (ij[:, 0], ij[:, 1])), shape=(n, n)).tocsr()
        else:
            mat = coo_matrix((n, n)).tocsr()
        self.dist, self.pred = dijkstra(mat, directed=False, indices=list(range(k)), return_predecessors=True)
        self.D = self.dist[:, :k]
        self.build_s = time.perf_counter() - t0
        Pk = P[:k]
        self.euclid = np.hypot(*(Pk[:, None, :] - Pk[None, :, :]).transpose(2, 0, 1))
        self.info = {'lanes': len(net.lanes), 'closed_lanes': net.closed, 'entries': len(entries), 'corners': len(hubs),
                     'edges': len(edges), 'service_offset_mean_m': float(np.mean(self.offsets)) if self.offsets else 0.0,
                     'service_offset_max_m': float(np.max(self.offsets)) if self.offsets else 0.0,
                     'plots': net.summary()['plots']}

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


def check(inst, routes, params=None):
    """Recompute task order, actual depot reloads, capacity, metrics and swept-radius clearance.

    Sequence/trip/service metadata are cross-checked against one another and the ordered geometry;
    none of them alone is a witness that a task was served. Invalid candidates remain diagnostics.
    Per-vehicle capacity, speed and footprint come from the route's fleet class (`vehicles.fleet`,
    synthesised as one class for legacy homogeneous instances).
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
    return {'valid': not errors, 'errors': errors, 'computed_cost': total, 'routes': details,
            'checker': 'urp_lab.solvers.fleet.check · 顺序/分趟/指标及连续圆足迹独立复算（Shapely）',
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


def dispatch_algorithm(inst, algorithm_id, D, demand, params, deadline, route_cap, K, multi, class_of, trace_diagnostics=None):
    """Run one fleet algorithm; returns (trips, owner, trip_class, trip_depot, trace)."""
    trace, owner, trip_class, trip_depot, reload_at = [], None, None, None, None
    trips = None
    if algorithm_id == 'pyvrp.fleet':
        trips, trip_class, trip_depot, trace = metaheuristic('pyvrp', inst, D, demand, params, route_cap)
    elif algorithm_id == 'ortools.fleet' and inst.get('objective') == 'makespan':
        trips, owner, reload_at, trace = ortools_makespan(inst, D, demand, params, K, multi, trace_diagnostics=trace_diagnostics)
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


def solve(scenario, inst, algorithm_id, params):
    t0 = time.perf_counter()
    from .. import ground_risk
    ground_policy, risk_weight, ground = ground_risk.resolve_policy(inst, params)
    ground_policy_active = ground_policy == 'risk_adjust'
    graph = (travel_graph(inst, ground['zones'], risk_weight) if ground_policy_active
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
    if hetero and algorithm_id in ('panel.sweep', 'panel.cluster_first', 'panel.savings_fleet'):
        raise ValueError('构造基线（sweep / cluster_first / savings）只支持同构车队；异构车队请用 pyvrp.fleet 或 ortools.fleet')
    if multi_depot and algorithm_id.startswith('panel.'):
        raise ValueError('构造基线（sweep / cluster_first / savings）只支持单车库；多车库请用 pyvrp.fleet 或 ortools.fleet')
    deadline = time.perf_counter() + float(params.get('time_limit_s', 3))
    route_cap = n if multi else K
    trace_diagnostics = {}
    trips, owner, trip_class, trip_depot, reload_at, trace = dispatch_algorithm(
        inst, algorithm_id, D, demand, params, deadline, route_cap, K, multi, class_of, trace_diagnostics=trace_diagnostics)
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
    attempts = []
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
           'trip_assignment': ('OR-Tools 直接为各车安排往返（车库卸载点），时间维度跨度代价最小化最晚完成'
                               if algorithm_id == 'ortools.fleet' and inst.get('objective') == 'makespan'
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
                 'scope': 'constant-speed planning proxy, not physically predicted completion time'},
        'optimization': {'reported_objective': inst.get('objective', 'total_distance'),
                         'search': 'declared ground-risk equivalent metres' if ground_policy_active else 'weighted distance and route-span surrogate' if algorithm_id == 'ortools.fleet' and inst.get('objective') == 'makespan'
                         else 'distance routing then trip allocation',
                         'trip_allocation': 'OR-Tools assignment' if owner is not None else 'LPT heuristic' if multi else 'one trip per vehicle',
                         'optimality_proved': False}}
    if not chk['valid']:
        raw['rejected_routes'] = routes
        raw['reason'] = '候选方案未通过独立检查，不能作为可行方案执行'
        return [], chk, metrics, raw, 'no_solution', trace
    return routes, chk, metrics, raw, 'feasible', trace


def objective_value(inst, metrics):
    return metrics['makespan_s'] if inst.get('objective') == 'makespan' else metrics['total_distance_m']
