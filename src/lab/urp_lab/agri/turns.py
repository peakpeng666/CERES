"""Headland manoeuvres between parallel work lines.

Lengths follow Bochtis & Vougioukas (2008): P(d) = d·w + (π − 2) r when d·w ≥ 2r, otherwise the U (loop) turn
U(d) = r (3π − 4 asin((2r + d·w) / 4r)). Geometry comes from Dubins paths, which reduce to the same curves, so
the tests can hold the closed form and the sampled path against each other. A T (reverse) turn is
kept for rigs that may reverse when the loop does not fit.
"""
from __future__ import annotations

import math

TAU = 2 * math.pi
SAMPLE_M = .25


def turn_kind(pitch: float, radius: float) -> str:
    if radius <= 0:
        return 'pivot'
    return 'P' if pitch >= 2 * radius else 'U'


def forward_length(pitch: float, radius: float) -> float:
    if radius <= 0:
        return pitch
    if pitch >= 2 * radius:
        return pitch + (math.pi - 2) * radius
    return radius * (3 * math.pi - 4 * math.asin((2 * radius + pitch) / (4 * radius)))


def reverse_length(pitch: float, radius: float) -> float:
    return math.pi * radius + abs(2 * radius - pitch)


def forward_depth(pitch: float, radius: float) -> float:
    if radius <= 0:
        return 0.0
    if pitch >= 2 * radius:
        return radius
    return radius + math.sqrt(max(0., 4 * radius ** 2 - (pitch / 2 + radius) ** 2))


def _mod(a):
    return a % TAU


def _words(alpha, beta, d):
    sa, sb, ca, cb = math.sin(alpha), math.sin(beta), math.cos(alpha), math.cos(beta)
    cab = math.cos(alpha - beta)
    out = []
    p2 = 2 + d * d - 2 * cab + 2 * d * (sa - sb)
    if p2 >= 0:
        t = math.atan2(cb - ca, d + sa - sb)
        out.append(('LSL', _mod(t - alpha), math.sqrt(p2), _mod(beta - t)))
    p2 = 2 + d * d - 2 * cab + 2 * d * (sb - sa)
    if p2 >= 0:
        t = math.atan2(ca - cb, d - sa + sb)
        out.append(('RSR', _mod(alpha - t), math.sqrt(p2), _mod(t - beta)))
    p2 = -2 + d * d + 2 * cab + 2 * d * (sa + sb)
    if p2 >= 0:
        p = math.sqrt(p2)
        t = math.atan2(-ca - cb, d + sa + sb) - math.atan2(-2.0, p)
        out.append(('LSR', _mod(t - alpha), p, _mod(t - beta)))
    p2 = -2 + d * d + 2 * cab - 2 * d * (sa + sb)
    if p2 >= 0:
        p = math.sqrt(p2)
        t = math.atan2(ca + cb, d - sa - sb) - math.atan2(2.0, p)
        out.append(('RSL', _mod(alpha - t), p, _mod(beta - t)))
    x = (6 - d * d + 2 * cab + 2 * d * (sa - sb)) / 8
    if abs(x) <= 1:
        p = _mod(TAU - math.acos(x))
        t = _mod(alpha - math.atan2(ca - cb, d - sa + sb) + p / 2)
        out.append(('RLR', t, p, _mod(alpha - beta - t + p)))
    x = (6 - d * d + 2 * cab + 2 * d * (sb - sa)) / 8
    if abs(x) <= 1:
        p = _mod(TAU - math.acos(x))
        t = _mod(-alpha + math.atan2(-ca + cb, d + sa - sb) + p / 2)
        out.append(('LRL', t, p, _mod(beta - alpha - t + p)))
    return out


def _advance(pose, kind, arc, radius):
    x, y, th = pose
    if kind == 'S':
        return x + arc * radius * math.cos(th), y + arc * radius * math.sin(th), th
    sign = 1 if kind == 'L' else -1
    nt = th + sign * arc
    return (x + sign * radius * (math.sin(nt) - math.sin(th)), y - sign * radius * (math.cos(nt) - math.cos(th)), nt)


def dubins(q0, q1, radius, step=None):
    """Shortest forward path as sampled points, or None. Candidates are integrated and must land on q1."""
    step = step or max(SAMPLE_M, radius / 12)
    dx, dy = q1[0] - q0[0], q1[1] - q0[1]
    d = math.hypot(dx, dy) / radius
    theta = math.atan2(dy, dx)
    best = None
    for word, t, p, q in sorted(_words(_mod(q0[2] - theta), _mod(q1[2] - theta), d), key=lambda w: sum(w[1:])):
        pose = tuple(q0)
        for kind, arc in zip(word, (t, p, q)):
            pose = _advance(pose, kind, arc, radius)
        if math.hypot(pose[0] - q1[0], pose[1] - q1[1]) < 1e-6 * max(1, radius) and abs(math.remainder(pose[2] - q1[2], TAU)) < 1e-6:
            best = (word, t, p, q)
            break
    if best is None:
        return None
    word, *arcs = best
    pts, pose = [[q0[0], q0[1]]], tuple(q0)
    for kind, arc in zip(word, arcs):
        n = max(1, math.ceil(arc * radius / step))
        for _ in range(n):
            pose = _advance(pose, kind, arc / n, radius)
            pts.append([pose[0], pose[1]])
    pts[-1] = [q1[0], q1[1]]
    return pts


def arc_points(center, radius, a0, a1, step=None):
    step = step or max(SAMPLE_M, radius / 12)
    n = max(1, math.ceil(abs(a1 - a0) * radius / step))
    return [[center[0] + radius * math.cos(a0 + (a1 - a0) * i / n), center[1] + radius * math.sin(a0 + (a1 - a0) * i / n)] for i in range(n + 1)]


def reverse_turn(q0, q1, radius):
    """T turn: forward quarter arc, straight reverse, forward quarter arc. Returns three legs or None."""
    h = (math.cos(q0[2]), math.sin(q0[2]))
    side = (q1[0] - q0[0]) * -h[1] + (q1[1] - q0[1]) * h[0]
    sign = 1 if side >= 0 else -1
    n = (-h[1] * sign, h[0] * sign)                    # towards the next line
    c0 = (q0[0] + n[0] * radius, q0[1] + n[1] * radius)
    a0 = math.atan2(q0[1] - c0[1], q0[0] - c0[0])
    arc0 = arc_points(c0, radius, a0, a0 + sign * math.pi / 2)
    # After a quarter turn the rig faces along n; the second arc starts r before the entry line, level with the first.
    c1 = (q1[0] - n[0] * radius, q1[1] - n[1] * radius)
    b1 = math.atan2(q1[1] - c1[1], q1[0] - c1[0])
    arc1 = arc_points(c1, radius, b1 - sign * math.pi / 2, b1)
    return [arc0, [arc0[-1], arc1[0]], arc1]


def fillet(points, radius, step=None):
    """Round polyline corners to `radius`. None when a segment is too short to hold its tangent lengths."""
    if radius <= 0 or len(points) < 3:
        return [list(p) for p in points]
    out, cursor = [list(points[0])], list(points[0])
    used = [0.0] * (len(points) - 1)
    tangents = []
    for i in range(1, len(points) - 1):
        a, b, c = points[i - 1], points[i], points[i + 1]
        u = (a[0] - b[0], a[1] - b[1])
        w = (c[0] - b[0], c[1] - b[1])
        lu, lw = math.hypot(*u), math.hypot(*w)
        if lu < 1e-9 or lw < 1e-9:
            return None
        cosang = max(-1., min(1., (u[0] * w[0] + u[1] * w[1]) / (lu * lw)))
        phi = math.acos(cosang)
        if phi > math.pi - 1e-6:
            tangents.append(None)
            continue
        if phi < 1e-3:
            return None
        t = radius / math.tan(phi / 2)
        used[i - 1] += t
        used[i] += t
        tangents.append((t, u, w, lu, lw, phi))
    for i, length in enumerate(used):
        if length > math.dist(points[i], points[i + 1]) + 1e-9:
            return None
    for i, spec in enumerate(tangents, 1):
        b = points[i]
        if spec is None:
            out.append(list(b))
            continue
        t, u, w, lu, lw, phi = spec
        p0 = [b[0] + u[0] / lu * t, b[1] + u[1] / lu * t]
        p1 = [b[0] + w[0] / lw * t, b[1] + w[1] / lw * t]
        bis = (u[0] / lu + w[0] / lw, u[1] / lu + w[1] / lw)
        lb = math.hypot(*bis)
        center = (b[0] + bis[0] / lb * radius / math.sin(phi / 2), b[1] + bis[1] / lb * radius / math.sin(phi / 2))
        a0 = math.atan2(p0[1] - center[1], p0[0] - center[0])
        a1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
        da = math.remainder(a1 - a0, TAU)
        out.extend(arc_points(center, radius, a0, a0 + da, step))
    out.append(list(points[-1]))
    return [p for i, p in enumerate(out) if i == 0 or math.dist(p, out[i - 1]) > 1e-9]
