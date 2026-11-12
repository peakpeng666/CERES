# CERES 局部 Dubins 倒圆缺陷自愈修复器
# 提取曲率超限孤立窗口，利用前向 Dubins 最短曲线局部重连，交由独立检查器全量复算
from __future__ import annotations

import math
import time

from ..solvers import f2c_coverage as C

WINDOWS = (1.0, 2.0, 4.0, 8.0)        # half-window arc length in multiples of the turning radius


def _native(instance: dict, params: dict):
    """Fields2Cover states exactly as in ``f2c_coverage.solve_full`` (2.1.0 binding)."""
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
        decomp.setSplitAngle(0.5 * math.pi)
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


def _curvature_defects(pts, radius):
    limit = (1 / radius) * (1 + 1e-3)
    out = []
    for i, (a, b, c) in enumerate(zip(pts, pts[1:], pts[2:])):
        prod = math.dist(a, b) * math.dist(b, c) * math.dist(a, c)
        if prod > 1e-12 and 2 * abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / prod > limit:
            out.append(i + 1)
    return out


def _heading(p, q):
    return math.atan2(q[1] - p[1], q[0] - p[0])


def _window(pts, kinds, v, half):
    """Indices (a, b) around vertex v spanning ≥ half arc length per side without entering a swath segment."""
    a, acc = v, 0.0
    while a > 0 and kinds[a - 1] != 'swath' and acc < half:
        acc += math.dist(pts[a - 1], pts[a])
        a -= 1
    b, acc = v, 0.0
    while b < len(pts) - 1 and kinds[b] != 'swath' and acc < half:
        acc += math.dist(pts[b], pts[b + 1])
        b += 1
    return a, b


def _dubins_points(robot, p, h0, q, h1):
    import fields2cover as f2c
    turner = f2c.PP_DubinsCurves()
    turner.setDiscretization(C.STEP)
    turn = turner.createTurn(robot, f2c.Point(*p), h0, f2c.Point(*q), h1)
    pts = [[turn.getState(j).point.getX(), turn.getState(j).point.getY()] for j in range(turn.size())]
    if not pts or math.dist(pts[-1], q) > 1e-6:
        pts.append(list(q))
    return pts


def solve_full_repaired(instance: dict, params: dict):
    """Same outputs as ``f2c_coverage.solve_full`` plus ``metrics['repairs']``; feasibility only via ``check_path``."""
    from shapely.geometry import LineString, Polygon
    t0 = time.perf_counter()
    pts, kinds, robot, n_cells = _native(instance, params)
    if len(pts) < 2:
        return [], dict(valid=False, errors=['Fields2Cover 没有生成路径']), {}, None
    swaths = [[pts[i], pts[i + 1]] for i, k in enumerate(kinds) if k == 'swath' and math.dist(pts[i], pts[i + 1]) > 1e-6]
    field = Polygon(instance['field'], instance.get('obstacles') or [])
    r = instance['turn_radius']
    repairs, attempts = 0, 0
    for _ in range(200):                                    # bounded: each round fixes the first remaining defect
        defects = _curvature_defects(pts, r)
        if not defects:
            break
        v = defects[0]
        fixed = False
        for factor in WINDOWS:
            a, b = _window(pts, kinds, v, factor * r)
            if b - a < 2 or a == 0 or b >= len(pts) - 1:
                continue
            attempts += 1
            h0, h1 = _heading(pts[a - 1], pts[a]), _heading(pts[b], pts[b + 1])
            seg = _dubins_points(robot, pts[a], h0, pts[b], h1)
            if len(seg) < 2 or not field.covers(LineString(seg).buffer(instance['robot_width'] / 2)):
                continue
            pts = pts[:a] + seg + pts[b + 1:]
            kinds = kinds[:a] + ['turn'] * (len(seg) - 1) + kinds[b:]
            repairs += 1
            fixed = True
            break
        if not fixed:
            break
    check, checked_metrics = C.check_path(instance, pts, swaths)
    length = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
    metrics = dict(checked_metrics, repairs=repairs, repair_attempts=attempts, n_cells=n_cells, n_swaths=len(swaths),
                   planning_s=time.perf_counter() - t0, remaining_defects=len(_curvature_defects(pts, r)))
    raw = dict(implementation='Fields2Cover 2.1 full pipeline (as f2c_coverage.solve_full) + local Dubins repair; official check_path',
               windows_r=list(WINDOWS), binding=C.available(), kinds=kinds)
    if not check['valid']:
        raw['rejected_polyline'] = pts
    route = dict(vehicle=0, nodes=None, sequence=[], services=[], polyline=pts, cost=length, load=0)
    return ([route] if check['valid'] else []), check, metrics, raw
