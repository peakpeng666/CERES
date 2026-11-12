"""Crop rows, inter-row lanes and the open headland / farm-road area of a field task map.

Every plot is planted with rows along its ``rows_deg`` at the spacing of its crop, leaving an unplanted
strip along the plot edge and a ring around obstacles inside it. A vehicle may drive between rows only
where the gap fits its width (2 × robot radius) plus ``SIDE_M`` on either side; narrow-row crops instead
get a driving track every few rows (one or more row positions left unplanted, two crop rows per bed),
so every crop row is next to a lane. Outside the planted areas — headland, farm roads, the rings around
obstacles — driving is free. A lane is connected to that open area at each end where its centre line,
continued past the rows, reaches it ("entries"); a task is served from the nearest point of a lane or
of the open area.

``Network`` is shared by the fleet planner (travel graph), its independent checker, the Gazebo world
(crop rows with collisions) and the map editor's lane overlay, so they all see the same rows.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union

CROPS = {  # crop → (row spacing m, row width m, height m, rgba)
    '小麦': (1.2, 0.5, 0.35, (0.85, 0.76, 0.38, 1)),
    '玉米': (1.5, 0.6, 0.6, (0.36, 0.62, 0.25, 1)),
    '大豆': (1.2, 0.5, 0.3, (0.42, 0.64, 0.3, 1)),
    '油菜': (1.2, 0.5, 0.35, (0.86, 0.82, 0.3, 1)),
    '水稻': (1.0, 0.45, 0.25, (0.5, 0.72, 0.36, 1)),
    '茶': (1.8, 0.9, 0.6, (0.22, 0.48, 0.24, 1)),
    '茶树': (1.8, 0.9, 0.6, (0.22, 0.48, 0.24, 1)),
    '蔬菜': (1.4, 0.7, 0.3, (0.38, 0.66, 0.3, 1)),
    '番茄': (1.4, 0.6, 0.8, (0.35, 0.6, 0.28, 1)),
    '草莓': (1.1, 0.5, 0.25, (0.4, 0.62, 0.32, 1)),
    '花卉': (1.2, 0.6, 0.4, (0.72, 0.5, 0.62, 1)),
    '苗圃': (1.6, 0.6, 0.6, (0.32, 0.58, 0.28, 1)),
    '葡萄': (2.8, 0.8, 1.2, (0.33, 0.5, 0.25, 1)),
    '猕猴桃': (3.5, 1.2, 0.9, (0.3, 0.52, 0.22, 1)),
    '果树': (4.0, 1.4, 1.0, (0.26, 0.5, 0.22, 1)),
    '苹果': (4.0, 1.5, 1.2, (0.28, 0.52, 0.24, 1)),
    '柑橘': (4.5, 1.8, 1.3, (0.24, 0.48, 0.22, 1)),
}
DEFAULT_CROP = (1.5, 0.6, 0.4, (0.4, 0.63, 0.29, 1))
MAX_ROWS = 160          # per plot; wider plots get proportionally wider spacing
EDGE_M = 0.8            # unplanted strip along plot (and field) edges
ENTRY_M = 0.3           # how far a lane entry sits inside the open area
REACH_M = 6.0           # how far past the rows a lane end looks for open ground
SIDE_M = 0.1            # clearance on each side of the vehicle between two crop rows


def crop_params(crop: str | None) -> tuple[float, float, float, tuple]:
    return CROPS.get(crop or '', DEFAULT_CROP)


def obstacle_margin(radius: float) -> float:
    """Unplanted ring around obstacles inside plots: wide enough to drive around them."""
    return 2 * radius + 1.0


def polygons(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, 'geoms', []) if isinstance(g, Polygon)]


def lines(geom) -> list[LineString]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    out = []
    for g in getattr(geom, 'geoms', []):
        out.extend(lines(g))
    return out


def free_space(inst):
    """Where the robot centre may be: inside the boundary and clear of every obstacle by the robot radius."""
    r = inst['robot_radius']
    boundary = Polygon(inst['boundary'])
    blocked = unary_union([Polygon(o['polygon']).buffer(r, quad_segs=4) for o in inst.get('obstacles') or []]) \
        if inst.get('obstacles') else None
    free = boundary.buffer(-r, join_style=2)
    if blocked is not None and not blocked.is_empty:
        free = free.difference(blocked)
    return free, boundary, blocked


@dataclass
class PlotRows:
    """Row layout of one plot (map metres)."""
    plot: str
    crop: str
    spacing: float           # effective row spacing
    width: float             # crop row width
    height: float
    rgba: tuple
    mode: str                # 'every': a lane between every two rows · 'track': a track every `pitch`
    lane_width: float        # free gap a vehicle drives in
    pitch: float             # distance between neighbouring lanes
    angle: float             # row direction, radians
    planted: object          # area the rows are planted in (shapely)
    rows: list = field(default_factory=list)     # crop row polygons
    lanes: list = field(default_factory=list)    # lane centre lines inside the planted area


def plot_rows(plot: dict, field_poly: Polygon | None, obstacles: list[Polygon], radius: float,
              corridors: list[Polygon] | None = None) -> PlotRows:
    spacing, width, height, rgba = crop_params(plot.get('crop'))
    angle = math.radians(float(plot.get('rows_deg', 0) or 0))
    poly = Polygon(plot['polygon']).buffer(0)
    planted = poly.buffer(-EDGE_M, join_style=2)
    if field_poly is not None and not planted.is_empty:
        planted = planted.intersection(field_poly.buffer(-EDGE_M, join_style=2))
    margin = obstacle_margin(radius)
    near = [o for o in obstacles if o.distance(poly) < margin]
    if near and not planted.is_empty:
        planted = planted.difference(unary_union([o.buffer(margin, quad_segs=6) for o in near]))
    crossing = [c for c in (corridors or []) if c.intersects(poly)]
    if crossing and not planted.is_empty:
        # farm roads / headland corridors stay unplanted, so they belong to the open travel area
        planted = planted.difference(unary_union(crossing))
    planted = unary_union([p for p in polygons(planted) if p.area > 1.0])
    out = PlotRows(str(plot.get('id', '')), str(plot.get('crop') or ''), spacing, width, height, rgba, 'every',
                   spacing - width, spacing, angle, planted)
    if planted.is_empty:
        return out
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    c = poly.centroid
    cx, cy = c.x, c.y      # the row grid is anchored to the plot, so obstacles do not shift it
    pts = np.array([xy for p in polygons(planted) for xy in p.exterior.coords])
    offs = (pts[:, 0] - cx) * nx + (pts[:, 1] - cy) * ny
    along = (pts[:, 0] - cx) * ux + (pts[:, 1] - cy) * uy
    lo, hi = float(offs.min()), float(offs.max())
    a0, a1 = float(along.min()) - 1.0, float(along.max()) + 1.0
    spacing = max(spacing, (hi - lo) / MAX_ROWS)
    out.spacing = spacing
    need = 2 * (radius + SIDE_M)
    if spacing - width >= need - 1e-9:
        skip = 0
        out.mode, out.lane_width, out.pitch = 'every', spacing - width, spacing
    else:  # leave `skip` row positions unplanted after every two rows
        skip = 1
        while (skip + 1) * spacing - width < need - 1e-9:
            skip += 1
        out.mode, out.lane_width, out.pitch = 'track', (skip + 1) * spacing - width, (skip + 2) * spacing
    k0, k1 = math.ceil(lo / spacing - 1e-9), math.floor(hi / spacing + 1e-9)

    def strip(o0, o1):
        return Polygon([(cx + nx * o0 + ux * a0, cy + ny * o0 + uy * a0), (cx + nx * o0 + ux * a1, cy + ny * o0 + uy * a1),
                        (cx + nx * o1 + ux * a1, cy + ny * o1 + uy * a1), (cx + nx * o1 + ux * a0, cy + ny * o1 + uy * a0)])

    def centre(o):
        return LineString([(cx + nx * o + ux * a0, cy + ny * o + uy * a0), (cx + nx * o + ux * a1, cy + ny * o + uy * a1)])
    if skip == 0:
        row_ks = list(range(k0, k1 + 1))
        lane_offsets = [(k + 0.5) * spacing for k in range(k0, k1)]
    else:
        period = skip + 2
        row_ks = [k for k in range(k0, k1 + 1) if (k - k0) % period < 2]
        lane_offsets = [(k + (skip - 1) / 2) * spacing for k in range(k0 + 2, k1 + 1, period)]
    for k in row_ks:
        o = k * spacing
        out.rows.extend(p for p in polygons(strip(o - width / 2, o + width / 2).intersection(planted)) if p.area > 0.05)
    for o in lane_offsets:
        out.lanes.extend(ln for ln in lines(centre(o).intersection(planted)) if ln.length > 0.5)
    return out


@dataclass
class Lane:
    plot: str
    start: tuple             # drivable extent of the lane (entry or dead end) ...
    end: tuple               # ... from start to end along the row direction
    entry_start: bool        # whether start / end are entries into the open area
    entry_end: bool
    line: LineString = None  # start → end

    def __post_init__(self):
        self.line = LineString([self.start, self.end])


class Network:
    """Field travel network of a fleet task map: lanes between crop rows plus the open area around plots."""

    def __init__(self, inst: dict):
        self.radius = r = float(inst['robot_radius'])
        self.free, self.boundary, _ = free_space(inst)
        self.obstacles = obstacles = [Polygon(o['polygon']).buffer(0) for o in inst.get('obstacles') or []]
        self.corridors = [Polygon(c['polygon']).buffer(0) for c in inst.get('corridors') or []]
        self.plots = [plot_rows(p, self.boundary, obstacles, r, self.corridors) for p in inst.get('plots') or []]
        zones = [p.planted.buffer(r) if not p.planted.is_empty else None for p in self.plots]
        self.blocked = unary_union([z for z in zones if z is not None]) if any(z is not None for z in zones) else Polygon()
        self.open = self.free.difference(self.blocked) if not self.blocked.is_empty else self.free
        self._open = self.open.buffer(1e-6)
        shapely.prepare(self._open)
        self.lanes: list[Lane] = []
        self.closed = 0          # lane pieces with no way in (both ends blocked)
        for k, pr in enumerate(self.plots):
            others = [z for j, z in enumerate(zones) if j != k and z is not None]
            other = unary_union(others) if others else None
            ux, uy = math.cos(pr.angle), math.sin(pr.angle)
            for piece in pr.lanes:
                for seg in (lines(piece.difference(other)) if other is not None else [piece]):
                    if seg.length < 0.5:
                        continue
                    a, b = seg.coords[0], seg.coords[-1]
                    if (b[0] - a[0]) * ux + (b[1] - a[1]) * uy < 0:
                        a, b = b, a
                    ea = self._entry(a, (-ux, -uy), other)
                    eb = self._entry(b, (ux, uy), other)
                    if ea is None and eb is None:
                        self.closed += 1
                        continue
                    self.lanes.append(Lane(pr.plot, ea or a, eb or b, ea is not None, eb is not None))
        self._lines = np.array([ln.line for ln in self.lanes], dtype=object)
        self.max_reach = max([3.0] + [p.pitch for p in self.plots])
        self._area = None

    # ------------------------------------------------------------ build --
    def _entry(self, end, direction, other) -> tuple | None:
        """Where the lane's centre line, continued past the rows, first reaches open ground (or None)."""
        ray = LineString([end, (end[0] + direction[0] * REACH_M, end[1] + direction[1] * REACH_M)])
        part = next((ln for ln in lines(ray.intersection(self.free)) if Point(end).distance(ln) < 1e-6), None)
        if part is None:
            return None
        pieces = lines(part.difference(self.blocked)) if not self.blocked.is_empty else [part]
        spans = sorted(sorted((ray.project(Point(ln.coords[0])), ray.project(Point(ln.coords[-1])))) for ln in pieces)
        if not spans:
            return None
        s0, s1 = spans[0]
        e = ray.interpolate(s0 + min(ENTRY_M, 0.5 * (s1 - s0)))
        if other is not None and LineString([end, e]).intersects(other):
            return None
        if not self._open.covers(e):
            return None
        return (float(e.x), float(e.y))

    # ------------------------------------------------------------- query --
    def in_open(self, xy) -> bool:
        return bool(self._open.covers(Point(xy)))

    def visible(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Whether each straight segment a[i] → b[i] stays inside the open area."""
        if not len(a):
            return np.zeros(0, dtype=bool)
        segs = shapely.linestrings(np.stack([np.asarray(a, float), np.asarray(b, float)], axis=1))
        return shapely.covers(self._open, segs)

    def attach(self, xy) -> tuple[tuple, int | None, float]:
        """Service position for a task: the nearest point of a lane (lane index) or of the open area (None)."""
        p = Point(xy)
        if self._open.covers(p):
            return (float(xy[0]), float(xy[1])), None, 0.0
        best = (math.inf, None, None)
        if len(self._lines):
            d = shapely.distance(self._lines, p)
            i = int(np.argmin(d))
            ln = self.lanes[i].line
            q = ln.interpolate(ln.project(p))
            best = (float(d[i]), (q.x, q.y), i)
        inner = self.open.buffer(-0.05)
        if not inner.is_empty:
            q = nearest_points(inner, p)[0]
            dq = q.distance(p)
            if dq < best[0] - 1e-9:
                best = (dq, (q.x, q.y), None)
        dist, stop, lane = best
        if stop is None:
            raise ValueError('地图上没有可通行的地头或行间通道')
        return (float(stop[0]), float(stop[1])), lane, float(dist)

    def hubs(self) -> list[tuple]:
        """Corners of the open area where shortest paths bend: around obstacles, planted areas and a concave boundary."""
        r = self.radius
        corners = []
        for o in self.obstacles:
            corners += list(o.buffer(r + 0.05, quad_segs=2, join_style=2).simplify(0.05).exterior.coords)[:-1]
        for pr in self.plots:
            for part in polygons(pr.planted.buffer(r + 0.05, join_style=2).simplify(0.05)):
                corners += list(part.exterior.coords)[:-1]
        inner = self.boundary.buffer(-(r + 0.05), join_style=2)
        if not inner.is_empty and inner.geom_type == 'Polygon' and inner.area < inner.convex_hull.area - 1e-6:
            corners += list(inner.exterior.coords)[:-1]
        seen, out = set(), []
        for c in corners:
            key = (round(c[0], 3), round(c[1], 3))
            if key not in seen and self._open.covers(Point(c)):
                seen.add(key)
                out.append((float(c[0]), float(c[1])))
        return out

    def travel_area(self):
        """Open area plus a thin corridor along every lane: a route on the network stays inside it."""
        if self._area is None:
            corridors = [ln.line.buffer(0.02) for ln in self.lanes]
            area = unary_union([self.open, *corridors]).buffer(1e-4)
            shapely.prepare(area)
            self._area = area
        return self._area

    def rows(self) -> list[Polygon]:
        return [row for pr in self.plots for row in pr.rows]

    def summary(self) -> dict:
        return {'lane_count': len(self.lanes), 'closed_lanes': self.closed, 'plots': [plot_info(p) for p in self.plots]}


def plot_info(pr: PlotRows) -> dict:
    return {'id': pr.plot, 'crop': pr.crop, 'spacing_m': round(pr.spacing, 3), 'row_width_m': pr.width,
            'mode': pr.mode, 'lane_width_m': round(pr.lane_width, 3), 'pitch_m': round(pr.pitch, 3),
            'rows': len(pr.rows), 'lanes': len(pr.lanes)}


def preview(inst: dict) -> dict:
    """Lane centre lines and per-plot row facts for the map editor's overlay."""
    net = Network(inst)
    return {**net.summary(), 'lanes': [[[round(x, 2), round(y, 2)] for x, y in ln.line.coords] for ln in net.lanes]}
