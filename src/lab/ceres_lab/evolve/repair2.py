"""Repair v2 — one more loop-② iteration, aimed at the defects the fillet verdict reveals (round 2, task T5).

Repair v1 replaces only the curvature defects the official checker can see. After the fillet verdict showed that
most remaining non-executable paths fail at corners that cannot be rounded inside their legs (adjacent corners
too close, or a corner next to a turn sample), v2 targets both kinds: official three-point defects and
unfilletable corners. Each target is replaced by the shortest forward Dubins path between poses taken up to
1, 2, 4, 8 turning radii away along non-swath segments (as in v1; swaths are never cut). Targets that cannot be
fixed are remembered by position so the loop terminates. Remaining corners are then rounded (``fillet.fillet``)
and the rounded path, with trimmed swaths re-declared, must pass the unchanged official ``check_path``.
"""
from __future__ import annotations

import math
import time

from ..solvers import f2c_coverage as C
from . import fillet as FL
from . import repair as R

WINDOWS = (1.0, 2.0, 4.0, 8.0)


def _targets(pts, kinds, radius):
    official = set(R._curvature_defects(pts, radius))
    f = FL.fillet(pts, kinds, radius)
    unfilletable = {x['i'] for x in (f.get('reasons') or []) if isinstance(x.get('i'), int) and 0 < x['i'] < len(pts) - 1}
    return sorted(official | unfilletable)


def solve_full_repaired2(instance: dict, params: dict):
    from shapely.geometry import LineString, Polygon
    t0 = time.perf_counter()
    pts, kinds, robot, n_cells = R._native(instance, params)
    if len(pts) < 2:
        return [], dict(valid=False, errors=['Fields2Cover 没有生成路径']), {}, None
    pts, kinds = FL._dedupe(pts, kinds)
    field = Polygon(instance['field'], instance.get('obstacles') or [])
    r = instance['turn_radius']
    repairs = attempts = 0
    unfixable: set = set()
    for _ in range(300):
        targets = [v for v in _targets(pts, kinds, r) if (round(pts[v][0], 3), round(pts[v][1], 3)) not in unfixable]
        if not targets:
            break
        v = targets[0]
        fixed = False
        for factor in WINDOWS:
            a, b = R._window(pts, kinds, v, factor * r)
            if b - a < 2 or a == 0 or b >= len(pts) - 1:
                continue
            attempts += 1
            seg = R._dubins_points(robot, pts[a], R._heading(pts[a - 1], pts[a]), pts[b], R._heading(pts[b], pts[b + 1]))
            if len(seg) < 2 or not field.covers(LineString(seg).buffer(instance['robot_width'] / 2)):
                continue
            pts = pts[:a] + seg + pts[b + 1:]
            kinds = kinds[:a] + ['turn'] * (len(seg) - 1) + kinds[b:]
            pts, kinds = FL._dedupe(pts, kinds)
            repairs += 1
            fixed = True
            break
        if not fixed:
            unfixable.add((round(pts[v][0], 3), round(pts[v][1], 3)))
    f = FL.fillet(pts, kinds, r)
    if f['ok']:
        out_pts, out_kinds, swaths = f['points'], f['kinds'], f['swaths']
    else:
        out_pts, out_kinds = pts, kinds
        swaths = [[pts[i], pts[i + 1]] for i, k in enumerate(kinds) if k == 'swath' and math.dist(pts[i], pts[i + 1]) > 1e-6]
    check, metrics = C.check_path(instance, out_pts, swaths)
    length = sum(math.dist(p, q) for p, q in zip(out_pts, out_pts[1:]))
    metrics = dict(metrics, repairs=repairs, repair_attempts=attempts, unfixable_targets=len(unfixable), rounded=f.get('rounded'),
                   rounding_ok=f['ok'], n_cells=n_cells, planning_s=time.perf_counter() - t0)
    raw = dict(implementation='Fields2Cover 2.1 full pipeline + v2 repair (official defects and unfilletable corners) + rounding; official check_path',
               windows_r=list(WINDOWS), binding=C.available(), kinds=out_kinds)
    if not check['valid']:
        raw['rejected_polyline'] = out_pts
    route = dict(vehicle=0, nodes=None, sequence=[], services=[], polyline=out_pts, cost=length, load=0)
    return ([route] if check['valid'] else []), check, metrics, raw
