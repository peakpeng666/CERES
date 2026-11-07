"""Transition model between work lines, stations and the depot, for one rig on one field.

Node ids: oriented line o = 2·i + d (d = 0 p1→p2, 1 p2→p1). The solver searches on these estimates; `realise`
then builds the exact geometry of the arcs a plan uses, and an arc that cannot be built is forbidden and solved again.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.sparse.csgraph import shortest_path
from shapely import contains_xy
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

from . import turns, worklines

INF = float('inf')
EDGE_EPS = .03


class Field:
    def __init__(self, inst: dict):
        self.inst = inst
        self.rig = inst['rig']
        self.lines = inst['lines']
        self.n = len(self.lines)
        self.radius = float(self.rig['min_turn_radius_m'])
        self.clear = self.rig['clearance_width_m'] / 2
        outer = Polygon(inst['field'])
        holes = [Polygon(o) for o in inst.get('obstacles') or []]
        area = outer.difference(unary_union(holes)) if holes else outer
        self.area = area
        _, self.work = worklines.regions(inst['field'], inst.get('obstacles'), inst['headland_m'])
        free = area if inst.get('overfly') else area.buffer(-self.clear, join_style=2)
        open_free = free
        self.crop_crossing = False
        if inst.get('crop_protected'):
            free = free.difference(self.work.buffer(-EDGE_EPS, join_style=2))
            # Headland pockets around an obstacle inside the crop have no way out except through the crop:
            # a transfer may then cross it; the checker reports those metres instead of hiding them.
            if free.geom_type != 'Polygon':
                free, self.crop_crossing = open_free, True
        self.free = free
        self._free_prep = prep(free.buffer(1e-6))
        theta = math.radians(inst.get('angle_deg') or 0)
        self.h = (math.cos(theta), math.sin(theta))
        self.nrm = (-self.h[1], self.h[0])
        self.oriented = []
        for line in self.lines:
            a, b = line['p1'], line['p2']
            ang = math.atan2(b[1] - a[1], b[0] - a[0])
            self.oriented.append({'line': line['id'], 'start': a, 'end': b, 'heading': ang})
            self.oriented.append({'line': line['id'], 'start': b, 'end': a, 'heading': ang + math.pi})
        self.pitch = float(inst['pitch_m'])
        self.work_m = np.array([x['length_m'] for x in self.lines for _ in (0, 1)])
        self.demand = np.array([x.get('demand', 0.) for x in self.lines for _ in (0, 1)])
        self._graph()
        self._turn_matrix()
        self.forbidden: set[tuple[int, int]] = set()
        self.forbidden_nodes: set[int] = set()

    # free-space graph for headland transfers
    def _graph(self):
        # Corners sit towards the middle of the headland, not on its edge: a curved rig needs room on both sides.
        band = max(.15, min(self.radius, .45 * (self.inst['headland_m'] - self.clear)))
        inner = self.free.buffer(-band, join_style=2)
        if inner.is_empty:
            inner = self.free.buffer(-.15, join_style=2)
        polys = [inner] if inner.geom_type == 'Polygon' else list(getattr(inner, 'geoms', []))
        nudged = []
        for g in polys:
            if g.geom_type != 'Polygon':
                continue
            for ring in [g.exterior, *g.interiors]:
                nudged.extend(list(ring.simplify(.2).coords)[:-1])
        self.corners = np.array(nudged, dtype=float).reshape(-1, 2)
        m = len(self.corners)
        W = np.full((m, m), np.inf)
        for i in range(m):
            for j in range(i + 1, m):
                if self.visible(self.corners[i], self.corners[j]):
                    W[i, j] = W[j, i] = math.dist(self.corners[i], self.corners[j])
        self.Wc = W
        graph = np.where(np.isfinite(W), W, 0.)
        self.Dc, self.pred = shortest_path(graph, method='D', directed=False, return_predecessors=True)

    def visible(self, a, b) -> bool:
        if math.dist(a, b) < 1e-9:
            return True
        return self._free_prep.covers(LineString([a, b]))

    def inside(self, pts) -> bool:
        return self._free_prep.covers(LineString(pts)) if len(pts) > 1 else self._free_prep.covers(Point(pts[0]))

    def _link(self, xy) -> np.ndarray:
        d = np.full(len(self.corners), np.inf)
        for i, c in enumerate(self.corners):
            if self.visible(xy, c):
                d[i] = math.dist(xy, c)
        return d

    def _out_point(self, o: int, entering: bool):
        """A point beyond the line end, in the headland, where a transfer joins or leaves the line."""
        q = self.oriented[o]
        xy, ang = (q['start'], q['heading'] + math.pi) if entering else (q['end'], q['heading'])
        step = max(self.radius, .5)
        return [xy[0] + math.cos(ang) * step, xy[1] + math.sin(ang) * step]

    def _turn_matrix(self):
        """Forward turns between parallel lines: closed-form length, level offset, and room in the headland."""
        n2 = 2 * self.n
        along = np.zeros(n2); across = np.zeros(n2)
        along_s = np.zeros(n2); across_s = np.zeros(n2)
        sign = np.zeros(n2)
        for o, q in enumerate(self.oriented):
            ex, ey = q['end']; sx, sy = q['start']
            along[o] = ex * self.h[0] + ey * self.h[1]
            across[o] = ex * self.nrm[0] + ey * self.nrm[1]
            along_s[o] = sx * self.h[0] + sy * self.h[1]
            across_s[o] = sx * self.nrm[0] + sy * self.nrm[1]
            sign[o] = 1 if o % 2 == 0 else -1
        self.room = np.array([self._room(o) for o in range(n2)])
        pitch = np.abs(across[:, None] - across_s[None, :])
        level = np.abs(along[:, None] - along_s[None, :])
        opposite = sign[:, None] != sign[None, :]
        r = self.radius
        length = np.full((n2, n2), INF)
        kind = np.full((n2, n2), '', dtype=object)
        depth = np.zeros((n2, n2))
        ii, jj = np.nonzero(opposite)
        reverse_ok = self.rig.get('reverse') and r > 0
        for i, j in zip(ii, jj):
            if i // 2 == j // 2:
                continue
            d = float(pitch[i, j])
            s = float(level[i, j])
            # the rig must get past the further of the two ends before it can turn
            ahead = (along[i] - along_s[j]) * sign[i]
            room = min(self.room[i] - max(0., -ahead), self.room[j ^ 1] - max(0., ahead))
            need = turns.forward_depth(d, r)
            if r <= 0:
                length[i, j], kind[i, j] = d + s, 'pivot'
            elif need <= room:
                length[i, j], kind[i, j] = turns.forward_length(d, r) + s, turns.turn_kind(d, r)
            elif reverse_ok and r <= room:
                length[i, j], kind[i, j] = turns.reverse_length(d, r) + s, 'T'
            depth[i, j] = need
        self.turn_len, self.turn_kind, self.turn_depth = length, kind, depth
        # Transfers around the headland, used when no direct turn exists.
        links_in = [self._link(self._out_point(o, True)) for o in range(n2)]
        links_out = [self._link(self._out_point(o, False)) for o in range(n2)]
        self.L_in, self.L_out = np.array(links_in), np.array(links_out)
        C = self.Dc
        base = np.min(self.L_out[:, :, None] + C[None, :, :], axis=1) if len(self.corners) else np.full((n2, 0), INF)
        transfer = np.min(base[:, None, :] + self.L_in[None, :, :], axis=2) if len(self.corners) else np.full((n2, n2), INF)
        allowance = 2 * (max(self.radius, .5)) + (math.pi - 2) * self.radius
        self.transfer_len = transfer + allowance
        for i in range(n2):
            self.transfer_len[i, i ^ 1] = INF
            self.transfer_len[i, i] = INF
        self.stations = [s['xy'] for s in self.inst['stations']]
        self.depot = self.inst['depot']
        self.pts = [self.depot] + self.stations
        self.P_link = [self._link(p) for p in self.pts]
        self.to_line = np.array([self._point_to(lk, self.L_in) for lk in self.P_link]) + max(self.radius, .5)
        self.from_line = np.array([self._point_to(lk, self.L_out) for lk in self.P_link]) + max(self.radius, .5)
        v_t, v_x = self.rig['speed_turn_mps'], self.rig['speed_transfer_mps']
        pivot_s = (math.pi / self.rig['pivot_rate_radps']) if self.radius <= 0 else 0.
        self.cost = np.minimum(self.turn_len, self.transfer_len)
        self.is_turn = self.turn_len <= self.transfer_len
        self.t_cost = np.where(self.is_turn, self.turn_len / v_t + pivot_s, self.transfer_len / v_x)
        t_extra = np.where(self.turn_kind == 'T', 8.0, 0.)
        self.t_cost = self.t_cost + t_extra

    def _point_to(self, link, L):
        """Shortest free-space distance from a point to each line's headland point."""
        base = np.min(link[:, None] + self.Dc, axis=0)
        return np.min(base[None, :] + L, axis=1)

    def _room(self, o: int) -> float:
        """Free distance ahead of a line end, along its heading: what a turn may use."""
        q = self.oriented[o]
        x, y = q['end']
        ang = q['heading']
        far = (x + math.cos(ang) * 1000, y + math.sin(ang) * 1000)
        ray = LineString([(x, y), far])
        hit = ray.intersection(self.area.boundary)
        d = 1000.
        if not hit.is_empty:
            d = Point(x, y).distance(hit)
        return max(0., d - self.clear)
