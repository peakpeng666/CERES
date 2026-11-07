"""Work lines: the single service object for row and swath operations.

A work line is a straight segment the rig must traverse with the implement engaged. Crop rows, orchard alleys
and coverage swaths all compile to this: they differ only in how the lines are spaced and what they consume.
"""
from __future__ import annotations

import math

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import unary_union

MIN_LINE_M = 2.0
MAX_LINES = 400


def regions(field, obstacles, headland: float, obstacle_margin: float | None = None):
    """(whole field minus obstacles, working area after the headland strip)."""
    margin = headland if obstacle_margin is None else obstacle_margin
    outer = Polygon(field)
    if not outer.is_valid or outer.area <= 0:
        raise ValueError('田块边界无效')
    holes = [Polygon(o) for o in obstacles or []]
    area = outer.difference(unary_union(holes)) if holes else outer
    inner = outer.buffer(-headland, join_style=2)
    if holes:
        inner = inner.difference(unary_union([h.buffer(margin, join_style=2) for h in holes]))
    if inner.is_empty or inner.area <= 0:
        raise ValueError('地头太宽：扣除地头后没有作业区')
    return area, inner


def generate(field, obstacles, headland: float, angle_deg: float, pitch: float, obstacle_margin: float | None = None) -> list[dict]:
    """Parallel lines at `pitch`, clipped to the working area; a pass split by an obstacle gives two lines of one row."""
    if pitch <= 0:
        raise ValueError('作业间距须大于 0')
    _, inner = regions(field, obstacles, headland, obstacle_margin)
    origin = inner.centroid
    flat = affinity.rotate(inner, -angle_deg, origin=origin)
    x0, y0, x1, y1 = flat.bounds
    span = y1 - y0
    count = max(1, math.ceil(span / pitch - 1e-9))
    if count > MAX_LINES:
        raise ValueError(f'作业行 {count} 条，超过上限 {MAX_LINES}；加大幅宽或缩小田块')
    # Evenly from the first edge; the last pass is pulled back inside, as a real overlap, rather than left short.
    ys = [y0 + pitch / 2 + k * pitch for k in range(count)]
    if ys[-1] > y1 - pitch / 2:
        ys[-1] = max(y1 - pitch / 2, y0 + pitch / 2 if count == 1 else ys[-2])
    lines, cos, sin = [], math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    ox, oy = origin.x, origin.y

    def back(x, y):
        dx, dy = x - ox, y - oy
        return [round(ox + dx * cos - dy * sin, 3), round(oy + dx * sin + dy * cos, 3)]

    for row, y in enumerate(ys):
        cut = LineString([(x0 - 1, y), (x1 + 1, y)]).intersection(flat)
        parts = [cut] if isinstance(cut, LineString) else list(cut.geoms) if isinstance(cut, MultiLineString) else []
        parts = sorted((p for p in parts if isinstance(p, LineString) and p.length >= MIN_LINE_M), key=lambda p: p.coords[0][0])
        for piece, part in enumerate(parts):
            a, b = part.coords[0], part.coords[-1]
            lines.append({'id': len(lines) + 1, 'row': row, 'piece': piece, 'p1': back(*a), 'p2': back(*b), 'length_m': round(part.length, 3)})
    if not lines:
        raise ValueError('作业区内放不下作业行；减小地头或幅宽')
    return lines
