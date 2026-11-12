"""Candidate library members for the admission study (round 2, task T4) — research code, not registered algorithms.

Three families of candidates with deliberately different strengths, so that admission rules can disagree:

- ``evolve.f2c_repair_w``: repair v1 (local Dubins replacement of curvature defects) with other half-window sets
  (``params['windows']`` in multiples of r) — a parameter variant of an existing member;
- ``evolve.f2c_split``: Fields2Cover boustrophedon decomposition with a different split angle
  (``params['split_angle_pi']`` × π instead of 0.5 π) — a parameterised configuration;
- ``evolve.f2c_fillet``: the full pipeline followed by corner rounding (``fillet.fillet``); the rounded path is
  returned when rounding succeeds, otherwise the native path — an executability-oriented post-processor.

All three replay the same Fields2Cover 2.1 calls as ``f2c_coverage.solve_full`` and leave validity to the
unchanged official ``check_path``. Outputs use the ``solve_full`` tuple (routes, check, metrics, raw).
"""
from __future__ import annotations

import math
import time

from ..solvers import f2c_coverage as C
from . import repair as R


def _native(instance: dict, params: dict, split_angle: float = 0.5 * math.pi):
    import fields2cover as f2c

    def ring(points):
        pts = list(points) + [points[0]]
        return f2c.LinearRing(f2c.VectorPoint([f2c.Point(float(x), float(y)) for x, y in pts]))
    cell = f2c.Cell(ring(instance['field']))
    for hole in instance.get('obstacles') or []:
        cell.addRing(ring(hole))
    cells = f2c.Cells(cell)
    robot = f2c.Robot(instance['robot_width'], instance['swath_width'])
    robot.setMinTurningRadius(instance['turn_radius'])
    hl = f2c.HG_Const_gen()
    width = instance['headland_width']
    mid_hl = hl.generateHeadlands(cells, width / 2)
    inner = hl.generateHeadlands(cells, width)
    decomposition = params.get('decomposition', 'boustrophedon')
    if C.DECOMPOSITIONS.get(decomposition):
        decomp = getattr(f2c, C.DECOMPOSITIONS[decomposition])()
        decomp.setSplitAngle(split_angle)
        inner = decomp.decompose(inner)
    objective = getattr(f2c, C.SWATH_OBJECTIVES[params.get('swath_objective', 'n_swath')])()
    swaths = f2c.SG_BruteForce().generateBestSwaths(objective, robot.getCovWidth(), inner)
    route = f2c.RP_RoutePlannerBase().genRoute(mid_hl, swaths)
    turner = getattr(f2c, C.TURN_MODELS[params.get('turn_model', 'dubins')])()
    turner.setDiscretization(C.STEP)
    path = f2c.PP_PathPlanning().planPath(robot, route, turner)
    states = [path.getState(i) for i in range(path.size())]
    pts = [[s.point.getX(), s.point.getY()] for s in states]
    end = path.atEnd()
    pts.append([end.getX(), end.getY()])
    kinds = [C.SECTION.get(int(s.type), 'other') for s in states]
    return pts, kinds, robot, inner.size()


def _swaths(pts, kinds):
    return [[pts[i], pts[i + 1]] for i, k in enumerate(kinds) if k == 'swath' and math.dist(pts[i], pts[i + 1]) > 1e-6]


def _finish(instance, pts, kinds, t0, extra: dict, impl: str, swaths=None):
    check, metrics = C.check_path(instance, pts, swaths if swaths is not None else _swaths(pts, kinds))
    length = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
    metrics = dict(metrics, planning_s=time.perf_counter() - t0, **extra)
    raw = dict(implementation=impl, binding=C.available(), kinds=kinds)
    if not check['valid']:
        raw['rejected_polyline'] = pts
    route = dict(vehicle=0, nodes=None, sequence=[], services=[], polyline=pts, cost=length, load=0)
    return ([route] if check['valid'] else []), check, metrics, raw


def solve_split(instance: dict, params: dict):
    t0 = time.perf_counter()
    angle = float(params.get('split_angle_pi', 0.5)) * math.pi
    pts, kinds, _robot, n_cells = _native(instance, params, angle)
    if len(pts) < 2:
        return [], dict(valid=False, errors=['Fields2Cover 没有生成路径']), {}, None
    return _finish(instance, pts, kinds, t0, {'n_cells': n_cells, 'split_angle_rad': angle},
                   f'Fields2Cover 2.1 full pipeline, boustrophedon split angle {angle:.4f} rad; official check_path')


def solve_repair_windows(instance: dict, params: dict):
    """Repair v1 loop with a custom half-window set (multiples of r); same acceptance as ``repair``."""
    from shapely.geometry import LineString, Polygon
    t0 = time.perf_counter()
    windows = tuple(float(w) for w in params.get('windows', R.WINDOWS))
    pts, kinds, robot, n_cells = R._native(instance, params)
    if len(pts) < 2:
        return [], dict(valid=False, errors=['Fields2Cover 没有生成路径']), {}, None
    swaths = _swaths(pts, kinds)
    field = Polygon(instance['field'], instance.get('obstacles') or [])
    r = instance['turn_radius']
    repairs = attempts = 0
    for _ in range(200):
        defects = R._curvature_defects(pts, r)
        if not defects:
            break
        v, fixed = defects[0], False
        for factor in windows:
            a, b = R._window(pts, kinds, v, factor * r)
            if b - a < 2 or a == 0 or b >= len(pts) - 1:
                continue
            attempts += 1
            seg = R._dubins_points(robot, pts[a], R._heading(pts[a - 1], pts[a]), pts[b], R._heading(pts[b], pts[b + 1]))
            if len(seg) < 2 or not field.covers(LineString(seg).buffer(instance['robot_width'] / 2)):
                continue
            pts = pts[:a] + seg + pts[b + 1:]
            kinds = kinds[:a] + ['turn'] * (len(seg) - 1) + kinds[b:]
            repairs, fixed = repairs + 1, True
            break
        if not fixed:
            break
    return _finish(instance, pts, kinds, t0, {'repairs': repairs, 'repair_attempts': attempts, 'n_cells': n_cells,
                                              'windows_r': list(windows), 'remaining_defects': len(R._curvature_defects(pts, r))},
                   f'Fields2Cover 2.1 full pipeline + local Dubins repair, windows {list(windows)} r; official check_path', swaths)


def solve_fillet_output(instance: dict, params: dict):
    """Full pipeline, then corner rounding; the rounded path is returned when rounding succeeds."""
    from . import fillet as FL
    t0 = time.perf_counter()
    pts, kinds, _robot, n_cells = R._native(instance, params)
    if len(pts) < 2:
        return [], dict(valid=False, errors=['Fields2Cover 没有生成路径']), {}, None
    f = FL.fillet(pts, kinds, instance['turn_radius'])
    if f['ok']:
        return _finish(instance, f['points'], f['kinds'], t0, {'rounded': f['rounded'], 'n_cells': n_cells, 'fillet_applied': True,
                                                              'max_deviation_m': f['max_deviation_m']},
                       'Fields2Cover 2.1 full pipeline + corner rounding (radius r, 1 cm kink tolerance); official check_path',
                       f['swaths'])
    return _finish(instance, pts, kinds, t0, {'n_cells': n_cells, 'fillet_applied': False, 'n_unfilletable': f['n_unfilletable']},
                   'Fields2Cover 2.1 full pipeline (rounding impossible, native path); official check_path')


SOLVERS = {'evolve.f2c_split': solve_split, 'evolve.f2c_repair_w': solve_repair_windows, 'evolve.f2c_fillet': solve_fillet_output}
NAMES = {'evolve.f2c_split': '覆盖全流程（改分割角，研究候选）', 'evolve.f2c_repair_w': '覆盖全流程+局部修补（改窗口，研究候选）',
         'evolve.f2c_fillet': '覆盖全流程+倒圆输出（研究候选）'}


def solve(algorithm: str, instance: dict, params: dict) -> dict:
    routes, check, metrics, raw = SOLVERS[algorithm](instance, params)
    return {'status': 'feasible' if routes else 'no_solution',
            'objective': {'value': sum(r['cost'] for r in routes) if routes else None, 'unit': 'm'},
            'routes': routes, 'check': check, 'metrics': metrics, 'raw_result': raw,
            'algorithm': {'id': algorithm, 'name': NAMES[algorithm], 'version': f"{algorithm}+{(raw or {}).get('binding') or 'local'}"}}
