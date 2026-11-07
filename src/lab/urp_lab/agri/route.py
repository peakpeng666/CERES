"""Exact geometry for a plan: work segments, headland turns, transfers and refill stops."""
from __future__ import annotations

import heapq
import math

import numpy as np

from . import turns
from .model import Field


class Unbuildable(Exception):
    def __init__(self, arc, reason):
        super().__init__(reason)
        self.arc = arc
        self.reason = reason


def _length(pts) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _heading_change(pts) -> float:
    total = 0.
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        h1 = math.atan2(b[1] - a[1], b[0] - a[0])
        h2 = math.atan2(c[1] - b[1], c[0] - b[0])
        total += abs(math.remainder(h2 - h1, 2 * math.pi))
    return total


def _seg(kind, pts, F: Field, **extra) -> dict:
    raw = [[round(float(x), 5), round(float(y), 5)] for x, y in pts]
    # Sub-centimetre samples add nothing and turn rounding into false curvature.
    pts = [raw[0]]
    for p in raw[1:-1]:
        if math.dist(p, pts[-1]) >= .01:
            pts.append(p)
    if len(raw) > 1:
        if len(pts) > 1 and math.dist(raw[-1], pts[-1]) < .01:
            pts.pop()
        pts.append(raw[-1])
    length = _length(pts)
    rig = F.rig
    v = {'work': rig['speed_work_mps'], 'turn': rig['speed_turn_mps'], 'transfer': rig['speed_transfer_mps']}[kind]
    seconds = length / v
    if F.radius <= 0 and kind != 'work':
        seconds += _heading_change(pts) / rig['pivot_rate_radps']
    return {'kind': kind, 'polyline': pts, 'length_m': round(length, 4), 'duration_s': round(seconds, 4), **extra}


def _pose(xy, ang):
    return (xy[0], xy[1], ang)


def turn_geometry(F: Field, a: int, b: int) -> list[dict]:
    qa, qb = F.oriented[a], F.oriented[b]
    kind = F.turn_kind[a, b]
    end, start = qa['end'], qb['start']
    ha = (math.cos(qa['heading']), math.sin(qa['heading']))
    ahead = (end[0] - start[0]) * ha[0] + (end[1] - start[1]) * ha[1]
    e1, e2 = max(0., -ahead), max(0., ahead)
    p0 = [end[0] + ha[0] * e1, end[1] + ha[1] * e1]
    p1 = [start[0] + ha[0] * e2, start[1] + ha[1] * e2]
    lead = [end, p0] if e1 > 1e-6 else [end]
    tail = [p1, start] if e2 > 1e-6 else [start]
    r = F.radius
    if kind == 'pivot':
        direct = [end, start]
        if F.inside(direct):
            return [_seg('turn', direct, F, turn='pivot')]
        return [_seg('turn', [*lead, *tail], F, turn='pivot')]
    if kind == 'T':
        legs = turns.reverse_turn(_pose(p0, qa['heading']), _pose(p1, qb['heading']), r)
        reverse = F.pitch < 2 * r
        return [_seg('turn', [*lead, *legs[0]], F, turn='T'),
                _seg('turn', legs[1], F, turn='T', reverse=reverse),
                _seg('turn', [*legs[2], *tail[1:]], F, turn='T')]
    pts = turns.dubins(_pose(p0, qa['heading']), _pose(p1, qb['heading']), r)
    if pts is None:
        raise Unbuildable((a, b), '转弯几何不存在')
    return [_seg('turn', [*lead, *pts[1:-1], *tail], F, turn=kind)]


def _corner_path(F: Field, A, B):
    """Polyline A → corners → B through free space, or None."""
    if F.visible(A, B):
        return [A, B]
    la, lb = F._link(A), F._link(B)
    if not len(F.corners):
        return None
    total = la[:, None] + F.Dc + lb[None, :]
    if not np.isfinite(total).any():
        return None
    i, j = np.unravel_index(int(np.argmin(total)), total.shape)
    chain = [int(j)]
    while chain[-1] != int(i):
        p = F.pred[int(i), chain[-1]]
        if p < 0:
            return None
        chain.append(int(p))
    chain.reverse()
    return [A, *[F.corners[c].tolist() for c in chain], B]


def _thin(F: Field, pts):
    """Drop waypoints that a straight line can skip: fewer corners give curves room to fit."""
    out, i = [pts[0]], 0
    while i < len(pts) - 1:
        j = len(pts) - 1
        while j > i + 1 and not F.visible(pts[i], pts[j]):
            j -= 1
        out.append(pts[j])
        i = j
    return out


def _piecewise(F: Field, pts, h0, h1):
    """Dubins legs between waypoints that carry a heading; each leg is forward-only and bounded by r."""
    r = F.radius
    headings = [h0]
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        u = math.atan2(b[1] - a[1], b[0] - a[0])
        w = math.atan2(c[1] - b[1], c[0] - b[0])
        headings.append(u + math.remainder(w - u, 2 * math.pi) / 2)
    headings.append(h1)
    out = [list(pts[0])]
    for p, q, hp, hq in zip(pts, pts[1:], headings, headings[1:]):
        leg = turns.dubins((*p, hp), (*q, hq), r)
        if leg is None:
            return None
        out.extend(leg[1:])
    return out


def _relaxed(pts, r):
    """Merge waypoints closer than the turning radius: a curve cannot honour both anyway."""
    out = [pts[0]]
    for p in pts[1:-1]:
        if math.dist(p, out[-1]) >= 1.5 * r and math.dist(p, pts[-1]) >= 1.5 * r:
            out.append(p)
    out.append(pts[-1])
    return out


def _valid(F: Field, pts) -> bool:
    return pts is not None and len(pts) >= 2 and (F.inside(pts) or F.crop_crossing)


def _curved(F: Field, start, end, h0, h1):
    """Forward curve from start to end, trying the shortest construction first. Free ends take the path's own heading."""
    r = F.radius
    best = None
    for step in (r, r / 2, .3):
        head = [list(start)]
        tail = [list(end)]
        A, B = list(start), list(end)
        if h0 is not None:
            A = [start[0] + math.cos(h0) * step, start[1] + math.sin(h0) * step]
            head.append(A)
        if h1 is not None:
            B = [end[0] - math.cos(h1) * step, end[1] - math.sin(h1) * step]
            tail.insert(0, B)
        mid = _corner_path(F, A, B)
        if mid is None:
            continue
        mid = _thin(F, mid)
        pts = [*head[:-1], *mid, *tail[1:]]
        pts = [p for i, p in enumerate(pts) if i == 0 or math.dist(p, pts[i - 1]) > 1e-6]
        if len(pts) < 2:
            continue
        s0 = h0 if h0 is not None else math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])
        s1 = h1 if h1 is not None else math.atan2(pts[-1][1] - pts[-2][1], pts[-1][0] - pts[-2][0])
        loose = _relaxed(pts, r)
        options = [turns.dubins((*start, s0), (*end, s1), r), turns.fillet(pts, r), turns.fillet(loose, r), _piecewise(F, pts, s0, s1), _piecewise(F, loose, s0, s1)]
        for cand in options:
            if _valid(F, cand):
                length = _length(cand)
                if best is None or length < best[0]:
                    best = (length, cand)
        if best is not None:
            return best[1]
    if h0 is not None and h1 is not None or h0 is not None or h1 is not None:
        s0 = h0 if h0 is not None else math.atan2(end[1] - start[1], end[0] - start[0])
        s1 = h1 if h1 is not None else math.atan2(end[1] - start[1], end[0] - start[0])
        return _lattice(F, (*start, s0), (*end, s1), free_start=h0 is None, free_goal=h1 is None)
    return None


def _lattice(F: Field, q0, q1, *, free_start=False, free_goal=False, limit=30000):
    """Last resort for tight headlands: A* over forward arcs of radius r on a heading lattice."""
    r = F.radius
    bins = 16
    dth = 2 * math.pi / bins
    step = r * dth
    cell = step * .75

    def key(x, y, b):
        return int(x // cell), int(y // cell), b

    def bin_of(a):
        return int(round(a / dth)) % bins

    start_bins = range(bins) if free_start else [bin_of(q0[2])]
    goal_bin = bin_of(q1[2])
    heap = []
    seen = {}
    for b in start_bins:
        heapq.heappush(heap, (math.dist(q0[:2], q1[:2]), 0., q0[0], q0[1], b, None))
    expanded = 0
    while heap and expanded < limit:
        _, g, x, y, b, parent = heapq.heappop(heap)
        k = key(x, y, b)
        if k in seen:
            continue
        node = (x, y, b, parent)
        seen[k] = node
        expanded += 1
        if math.dist((x, y), q1[:2]) < 6 * r and (free_goal or abs(((b - goal_bin + bins // 2) % bins) - bins // 2) <= bins):
            tail = turns.dubins((x, y, b * dth), (q1[0], q1[1], q1[2]), r)
            if tail is not None and F.inside(tail):
                chain = []
                n = node
                while n is not None:
                    chain.append(n)
                    n = n[3]
                chain.reverse()
                pts = [[q0[0], q0[1]]]
                pose = (q0[0], q0[1], chain[0][2] * dth)
                if not free_start and abs(math.remainder(pose[2] - q0[2], 2 * math.pi)) > 1e-9:
                    bridge = turns.dubins(q0, (*chain[1][:2], chain[1][2] * dth), r) if len(chain) > 1 else None
                    if bridge is None:
                        return None
                    pts = bridge
                    chain = chain[1:]
                for a, c in zip(chain, chain[1:]):
                    leg = turns.dubins((a[0], a[1], a[2] * dth), (c[0], c[1], c[2] * dth), r)
                    if leg is None:
                        return None
                    pts.extend(leg[1:])
                pts.extend(tail[1:])
                return pts if F.inside(pts) else None
        for turn in (0, 1, -1):
            nb = (b + turn) % bins
            th = b * dth
            if turn == 0:
                nx, ny = x + step * math.cos(th), y + step * math.sin(th)
            else:
                cx, cy = x - turn * r * math.sin(th), y + turn * r * math.cos(th)
                th2 = th + turn * dth
                nx, ny = cx + turn * r * math.sin(th2), cy - turn * r * math.cos(th2)
            if key(nx, ny, nb) in seen or not F.inside([[x, y], [nx, ny]]):
                continue
            ng = g + step
            heapq.heappush(heap, (ng + math.dist((nx, ny), q1[:2]), ng, nx, ny, nb, node))
    return None


def transfer(F: Field, start, end, *, start_heading=None, end_heading=None, arc=None) -> list[dict]:
    """Headland transfer. A curve leaves and joins a line tangentially; a zero-radius rig just turns on the spot."""
    if F.radius <= 0:
        pts = _corner_path(F, list(start), list(end))
        if pts is None:
            raise Unbuildable(arc, '地头内没有通路')
        return [_seg('transfer', pts, F)]
    pts = _curved(F, list(start), list(end), start_heading, end_heading)
    if pts is None:
        raise Unbuildable(arc, f'半径 {F.radius:g} m 的车辆在地头内转不过去')
    return [_seg('transfer', pts, F)]


def link(F: Field, a: int, b: int) -> list[dict]:
    if a in F.forbidden_nodes:
        raise Unbuildable((a, b), '不可达')
    if F.is_turn[a, b] and F.turn_kind[a, b]:
        segs = turn_geometry(F, a, b)
        pts = [p for s in segs for p in s['polyline']]
        if F.inside(pts) or F.turn_kind[a, b] == 'pivot' and F.crop_crossing:
            return segs
        if F.turn_kind[a, b] != 'pivot':
            # A closed-form turn can still clip an irregular edge; a transfer may yet exist.
            F.is_turn[a, b] = False
    qa, qb = F.oriented[a], F.oriented[b]
    return transfer(F, qa['end'], qb['start'], start_heading=qa['heading'], end_heading=qb['heading'], arc=(a, b))


def route(F: Field, order: list[int], cuts: list[int], index: int) -> dict:
    """One machine: depot → trips separated by refill stops → depot."""
    segs: list[dict] = []
    depot = F.depot
    station = F.inst['stations'][0] if F.inst['stations'] else None
    bounds = [0, *cuts, len(order)]
    trips = [order[a:b] for a, b in zip(bounds, bounds[1:])]
    for t, trip in enumerate(trips):
        first = F.oriented[trip[0]]
        origin = depot if t == 0 else station['xy']
        segs += transfer(F, origin, first['start'], end_heading=first['heading'], arc=(-1, trip[0]))
        for x, o in enumerate(trip):
            q = F.oriented[o]
            segs.append(_seg('work', [q['start'], q['end']], F, line=q['line'], direction=o % 2))
            if x + 1 < len(trip):
                segs += link(F, o, trip[x + 1])
        last = F.oriented[trip[-1]]
        goal = depot if t == len(trips) - 1 else station['xy']
        segs += transfer(F, last['end'], goal, start_heading=last['heading'], arc=(trip[-1], -1))
        if t < len(trips) - 1:
            segs.append({'kind': 'service', 'polyline': [list(goal)], 'length_m': 0., 'duration_s': float(station.get('service_s') or 0), 'station': station['id']})
    polyline = []
    for s in segs:
        for p in s['polyline']:
            if not polyline or polyline[-1] != p:
                polyline.append(p)
    work = sum(s['length_m'] for s in segs if s['kind'] == 'work')
    total = sum(s['length_m'] for s in segs)
    return {'vehicle': index, 'vehicle_name': f"机组 {index + 1}", 'polyline': polyline, 'segments': segs, 'cost': round(total, 3),
            'work_m': round(work, 3), 'nonwork_m': round(total - work, 3), 'duration_s': round(sum(s['duration_s'] for s in segs), 3),
            'sequence': [F.oriented[o]['line'] for o in order], 'directions': [o % 2 for o in order],
            'trips': [[F.oriented[o]['line'] for o in t] for t in trips], 'refills': len(trips) - 1}
