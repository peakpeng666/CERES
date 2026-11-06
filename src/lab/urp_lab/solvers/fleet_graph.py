"""Obstacle-clear and crop-row travel graphs shared by fleet solvers."""
from __future__ import annotations

import math
import time
from collections import defaultdict

import numpy as np

from .. import lanes
from ..lanes import free_space
from .fleet_common import depot_list


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

