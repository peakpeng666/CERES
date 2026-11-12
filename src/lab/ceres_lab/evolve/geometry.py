"""Small geometric primitives for the feature library: Dubins path length and polygon helpers.

Dubins lengths follow the six-word closed forms of Shkel & Lumelsky (2001) as implemented in A. Walker's
widely used ``dubins.c``; ``tests/test_evolve_features.py`` checks them against analytic U-turns and, when the
binding is installed, against Fields2Cover's own Dubins turns.
"""
from __future__ import annotations

import math

TWO_PI = 2.0 * math.pi


def _mod2pi(a: float) -> float:
    return a - TWO_PI * math.floor(a / TWO_PI)


def _words(alpha: float, beta: float, d: float):
    sa, sb, ca, cb = math.sin(alpha), math.sin(beta), math.cos(alpha), math.cos(beta)
    c_ab = math.cos(alpha - beta)
    out = {}
    p_sq = 2 + d * d - 2 * c_ab + 2 * d * (sa - sb)                       # LSL
    if p_sq >= 0:
        tmp = math.atan2(cb - ca, d + sa - sb)
        out['LSL'] = (_mod2pi(tmp - alpha), math.sqrt(p_sq), _mod2pi(beta - tmp))
    p_sq = 2 + d * d - 2 * c_ab + 2 * d * (sb - sa)                       # RSR
    if p_sq >= 0:
        tmp = math.atan2(ca - cb, d - sa + sb)
        out['RSR'] = (_mod2pi(alpha - tmp), math.sqrt(p_sq), _mod2pi(tmp - beta))
    p_sq = -2 + d * d + 2 * c_ab + 2 * d * (sa + sb)                      # LSR
    if p_sq >= 0:
        p = math.sqrt(p_sq)
        tmp = math.atan2(-ca - cb, d + sa + sb) - math.atan2(-2.0, p)
        out['LSR'] = (_mod2pi(tmp - alpha), p, _mod2pi(tmp - _mod2pi(beta)))
    p_sq = -2 + d * d + 2 * c_ab - 2 * d * (sa + sb)                      # RSL
    if p_sq >= 0:
        p = math.sqrt(p_sq)
        tmp = math.atan2(ca + cb, d - sa - sb) - math.atan2(2.0, p)
        out['RSL'] = (_mod2pi(alpha - tmp), p, _mod2pi(beta - tmp))
    tmp = (6.0 - d * d + 2 * c_ab + 2 * d * (sa - sb)) / 8.0             # RLR
    if abs(tmp) <= 1:
        phi = math.atan2(ca - cb, d - sa + sb)
        p = _mod2pi(TWO_PI - math.acos(tmp))
        t = _mod2pi(alpha - phi + _mod2pi(p / 2.0))
        out['RLR'] = (t, p, _mod2pi(alpha - beta - t + _mod2pi(p)))
    tmp = (6.0 - d * d + 2 * c_ab + 2 * d * (sb - sa)) / 8.0             # LRL
    if abs(tmp) <= 1:
        phi = math.atan2(ca - cb, d + sa - sb)
        p = _mod2pi(TWO_PI - math.acos(tmp))
        t = _mod2pi(-alpha - phi + p / 2.0)
        out['LRL'] = (t, p, _mod2pi(_mod2pi(beta) - alpha - t + _mod2pi(p)))
    return out


def dubins_length(q0, q1, radius: float) -> float:
    """Shortest forward-only path length between poses (x, y, heading) with a minimum turning radius."""
    if radius <= 0:
        raise ValueError('turning radius must be positive')
    dx, dy = q1[0] - q0[0], q1[1] - q0[1]
    d = math.hypot(dx, dy) / radius
    theta = _mod2pi(math.atan2(dy, dx)) if d > 0 else 0.0
    alpha, beta = _mod2pi(q0[2] - theta), _mod2pi(q1[2] - theta)
    words = _words(alpha, beta, d)
    if not words:
        raise ArithmeticError('no Dubins word is admissible')
    return radius * min(sum(w) for w in words.values())


def uturn_length(offset: float, radius: float) -> float:
    """Headland turn between parallel swaths `offset` apart with reversed heading (forward-only)."""
    return dubins_length((0.0, 0.0, 0.0), (0.0, offset, math.pi), radius)


def oriented_ring(polygon):
    """Exterior coordinates (without the closing point), counter-clockwise."""
    from shapely.geometry.polygon import orient
    return list(orient(polygon, sign=1.0).exterior.coords)[:-1]


def reflex_count(polygon) -> int:
    """Number of reflex (interior angle > 180°) vertices of the exterior ring."""
    pts = oriented_ring(polygon)
    n, count = len(pts), 0
    for i in range(n):
        (ax, ay), (bx, by), (cx, cy) = pts[i - 1], pts[i], pts[(i + 1) % n]
        if (bx - ax) * (cy - by) - (by - ay) * (cx - bx) < -1e-12:
            count += 1
    return count


def edge_moments(polygon, k: int) -> float:
    """|Σ l·e^{ikθ}| / Σ l over exterior edges: k=2 orientation coherence, k=4 rectilinearity (rotation invariant)."""
    pts = oriented_ring(polygon)
    re = im = total = 0.0
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        length = math.hypot(bx - ax, by - ay)
        if length <= 0:
            continue
        angle = math.atan2(by - ay, bx - ax)
        re += length * math.cos(k * angle)
        im += length * math.sin(k * angle)
        total += length
    return math.hypot(re, im) / total if total else 0.0


def largest(geom):
    """Largest polygon of a (multi)polygon result, or None when empty."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == 'Polygon':
        return geom
    polys = [g for g in getattr(geom, 'geoms', []) if g.geom_type == 'Polygon' and not g.is_empty]
    return max(polys, key=lambda g: g.area) if polys else None


def components(geom) -> int:
    if geom is None or geom.is_empty:
        return 0
    if geom.geom_type == 'Polygon':
        return 1
    return sum(1 for g in getattr(geom, 'geoms', []) if g.geom_type == 'Polygon' and g.area > 0)


def mrr_sides(polygon) -> tuple[float, float]:
    """Long and short side of the minimum-area rotated rectangle."""
    rect = polygon.minimum_rotated_rectangle
    if rect.geom_type != 'Polygon':
        return (rect.length, 0.0)
    pts = list(rect.exterior.coords)[:4]
    a = math.dist(pts[0], pts[1])
    b = math.dist(pts[1], pts[2])
    return (max(a, b), min(a, b))
