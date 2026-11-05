"""Point-to-point motion planning on occupancy maps: grid search (A*, Dijkstra, Theta*) and OMPL sampling planners."""
from __future__ import annotations

import heapq
import math
import time

import numpy as np

from ..scenarios import maps

GRID_PLANNERS = {
    'grid.astar': ('A*', 'astar', ['hart1968astar']),
    'grid.dijkstra': ('Dijkstra', 'dijkstra', ['dijkstra1959']),
    'grid.theta_star': ('Theta*', 'theta', ['daniel2010thetastar']),
}
OMPL_PLANNERS = {
    'ompl.rrt_connect': ('RRT-Connect', 'RRTConnect', False, ['kuffner2000rrtconnect', 'sucan2012ompl']),
    'ompl.rrt_star': ('RRT*', 'RRTstar', True, ['karaman2011rrtstar', 'sucan2012ompl']),
    'ompl.informed_rrt_star': ('Informed RRT*', 'InformedRRTstar', True, ['gammell2014informed', 'sucan2012ompl']),
    'ompl.bit_star': ('BIT*', 'BITstar', True, ['gammell2015bitstar', 'gammell2020bitstar', 'sucan2012ompl']),
    'ompl.prm_star': ('PRM*', 'PRMstar', True, ['kavraki1996prm', 'karaman2011rrtstar', 'sucan2012ompl']),
    'ompl.fmt': ('FMT*', 'FMT', False, ['janson2015fmt', 'sucan2012ompl']),
    'ompl.kpiece': ('KPIECE', 'KPIECE1', False, ['sucan2009kpiece', 'sucan2012ompl']),
}
SQRT2 = math.sqrt(2.0)


def _grid(world: str, inflation: float, cell: float):
    g = maps.load(world)
    k = max(1, int(round(cell / g.resolution)))
    h, w = g.height // k, g.width // k
    clr = g.clearance[:h * k, :w * k].reshape(h, k, w, k).min(axis=(1, 3))
    return g, k, clr >= inflation, clr


def _to_cell(g, k, x, y):
    r, c = g.rc(x, y)
    return r // k, c // k


def _to_xy(g, k, r, c):
    return (g.origin[0] + (c + 0.5) * k * g.resolution, g.origin[1] + (g.height - (r + 0.5) * k) * g.resolution)


def _line_free(free, a, b) -> bool:
    (r0, c0), (r1, c1) = a, b
    n = int(max(abs(r1 - r0), abs(c1 - c0)) * 2) + 1
    rs = np.rint(np.linspace(r0, r1, n)).astype(int)
    cs = np.rint(np.linspace(c0, c1, n)).astype(int)
    return bool(free[rs, cs].all())


def _search(free, start, goal, mode, deadline):
    h, w = free.shape
    gscore = {start: 0.0}
    parent = {start: start}
    heur = (lambda n: 0.0) if mode == 'dijkstra' else (lambda n: _octile(n, goal))
    heap = [(heur(start), 0.0, start)]
    closed = set()
    expanded = 0
    steps = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0), (-1, -1, SQRT2), (-1, 1, SQRT2), (1, -1, SQRT2), (1, 1, SQRT2)]
    while heap:
        f, gcur, node = heapq.heappop(heap)
        if node in closed:
            continue
        if node == goal:
            return parent, expanded, gcur
        closed.add(node)
        expanded += 1
        if expanded % 4096 == 0 and time.perf_counter() > deadline:
            raise TimeoutError('栅格搜索超过时间预算')
        r, c = node
        for dr, dc, cost in steps:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < h and 0 <= nc < w) or not free[nr, nc] or (nr, nc) in closed:
                continue
            if dr and dc and not (free[r + dr, c] and free[r, c + dc]):
                continue
            nxt = (nr, nc)
            if mode == 'theta':
                p = parent[node]
                if _line_free(free, p, nxt):
                    cand, via = gscore[p] + math.dist(p, nxt), p
                else:
                    cand, via = gcur + cost, node
            else:
                cand, via = gcur + cost, node
            if cand < gscore.get(nxt, math.inf):
                gscore[nxt] = cand
                parent[nxt] = via
                heapq.heappush(heap, (cand + heur(nxt), cand, nxt))
    return None, expanded, None


def _octile(a, b):
    dr, dc = abs(a[0] - b[0]), abs(a[1] - b[1])
    return (dr + dc) + (SQRT2 - 2) * min(dr, dc)


def plan_grid(world, inst, algorithm_id, params):
    _, mode, _ = GRID_PLANNERS[algorithm_id]
    inflation = inst['robot_radius'] + inst['clearance_margin']
    g, k, free, _ = _grid(world, inflation, float(params.get('grid_cell_m') or 0.09))
    start, goal = _to_cell(g, k, *inst['start'][:2]), _to_cell(g, k, *inst['goal'][:2])
    for name, cell in (('起点', start), ('终点', goal)):
        if not (0 <= cell[0] < free.shape[0] and 0 <= cell[1] < free.shape[1]) or not free[cell]:
            raise ValueError(f'{name}离障碍太近（需 ≥ {inflation:.2f} m 净空）')
    deadline = time.perf_counter() + float(params.get('time_limit_s', 3))
    parent, expanded, cost = _search(free, start, goal, mode, deadline)
    if parent is None:
        return None, {'expanded': expanded, 'grid_cell_m': k * g.resolution}
    cells = [goal]
    while cells[-1] != start:
        cells.append(parent[cells[-1]])
    cells.reverse()
    pts = [list(inst['start'][:2])] + [list(_to_xy(g, k, r, c)) for r, c in cells[1:-1]] + [list(inst['goal'][:2])]
    return pts, {'expanded': expanded, 'grid_cell_m': k * g.resolution, 'grid_cost_cells': cost, 'waypoints': len(cells)}


def plan_ompl(world, inst, algorithm_id, params):
    from ompl import base as ob, geometric as og, util as ou
    ou.setLogLevel(ou.LogLevel.LOG_NONE if hasattr(ou.LogLevel, 'LOG_NONE') else ou.LogLevel.LOG_ERROR)
    ou.RNG.setSeed(int(params.get('seed', 42)))  # must precede any sampling in this (fresh worker) process
    _, cls, anytime, _ = OMPL_PLANNERS[algorithm_id]
    model = inst['motion_model']
    if algorithm_id == 'ompl.fmt' and model == 'dubins':
        raise ValueError('FMT* 需要对称距离，Dubins 空间请选 RRT* / BIT* / PRM*')
    g = maps.load(world)
    x0, y0, x1, y1 = g.bounds()
    space = (ob.DubinsStateSpace(inst['turning_radius']) if model == 'dubins'
             else ob.ReedsSheppStateSpace(inst['turning_radius']) if model == 'reeds_shepp' else ob.SE2StateSpace())
    bounds = ob.RealVectorBounds(2)
    bounds.setLow(0, x0); bounds.setHigh(0, x1); bounds.setLow(1, y0); bounds.setHigh(1, y1)
    space.setBounds(bounds)
    ss = og.SimpleSetup(space)
    need = inst['robot_radius'] + inst['clearance_margin']
    # Extra half cell: the exported polyline joins interpolated states with chords, and the map check is conservative.
    guard = need + g.resolution
    ss.setStateValidityChecker(lambda s: g.clearance_at(s.getX(), s.getY()) >= guard)
    si = ss.getSpaceInformation()
    si.setStateValidityCheckingResolution(max(1e-4, (g.resolution / 2) / space.getMaximumExtent()))
    start, goal = space.allocState(), space.allocState()
    start.setXY(*inst['start'][:2]); start.setYaw(_wrap(inst['start'][2]))
    goal.setXY(*inst['goal'][:2]); goal.setYaw(_wrap(inst['goal'][2]))
    for name, st in (('起点', start), ('终点', goal)):
        if g.clearance_at(st.getX(), st.getY()) < guard:
            raise ValueError(f'{name}离障碍太近（需 ≥ {need:.2f} m 净空）')
    ss.setStartAndGoalStates(start, goal, inst['goal_tolerance'])
    ss.setOptimizationObjective(ob.PathLengthOptimizationObjective(si))
    planner = getattr(og, cls)(si)
    if cls == 'FMT' and hasattr(planner, 'setNumSamples'):
        planner.setNumSamples(int(params.get('samples') or 6000))  # one batch; the default 1000 is too sparse for aisle maps
    ss.setPlanner(planner)
    budget = float(params.get('time_limit_s', 3))
    trace, t0 = [], time.perf_counter()
    slices = 20 if anytime else 1
    for i in range(slices):
        ss.solve(budget / slices)
        if ss.haveExactSolutionPath():
            trace.append([round(time.perf_counter() - t0, 4), ss.getSolutionPath().length()])
            if not anytime:
                break
    if not ss.haveExactSolutionPath():
        return None, {'planner': cls, 'state_space': type(space).__name__, 'trace': trace}
    if not anytime and cls != 'FMT' and params.get('simplify', True):
        ss.simplifySolution(min(1.0, budget / 4))
    path = ss.getSolutionPath()
    raw_length = path.length()
    path.interpolate(max(2, int(raw_length / 0.04)))
    pts = [[s.getX(), s.getY()] for s in path.getStates()]
    yaws = [s.getYaw() for s in path.getStates()]
    return pts, {'planner': cls, 'state_space': type(space).__name__, 'trace': trace, 'ompl_length': raw_length,
                 'headings': yaws[:: max(1, len(yaws) // 400)], 'anytime': anytime}


def _wrap(yaw: float) -> float:
    """Wrap into OMPL's SO(2) bounds [-π, π]; rounded poses such as 3.141593 would otherwise be out of bounds."""
    yaw = math.atan2(math.sin(yaw), math.cos(yaw))
    return min(max(yaw, -math.pi), math.pi)


def evaluate(world, inst, pts):
    """Independent metrics for a planned polyline: clearance, curvature, cusps, goal error."""
    g = maps.load(world)
    need = inst['robot_radius'] + inst['clearance_margin']
    clearance = min(maps.segment_clearance(g, a, b)[0] for a, b in zip(pts, pts[1:]))
    curv, turns, cusps, total_turn = [], 0, 0, 0.0
    heading = [math.atan2(b[1] - a[1], b[0] - a[0]) for a, b in zip(pts, pts[1:]) if math.dist(a, b) > 1e-9]
    for a, b in zip(heading, heading[1:]):
        d = abs(math.atan2(math.sin(b - a), math.cos(b - a)))
        total_turn += d
        if d > math.radians(150):
            cusps += 1
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        prod = math.dist(a, b) * math.dist(b, c) * math.dist(a, c)
        if prod > 1e-12:
            curv.append(2 * abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / prod)
    turns = sum(abs(math.atan2(math.sin(b - a), math.cos(b - a))) > math.radians(30) for a, b in zip(heading, heading[1:]))
    length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
    goal_error = math.dist(pts[-1], inst['goal'][:2])
    errors = []
    if clearance < need:
        errors.append(f'路径净空 {clearance:.3f} m 小于机器人半径加安全余量 {need:.3f} m')
    if goal_error > inst['goal_tolerance'] + 1e-6:
        errors.append(f'终点误差 {goal_error:.3f} m 超出容差')
    if inst['motion_model'] != 'holonomic' and curv and max(curv) > (1 / inst['turning_radius']) * 1.05 and cusps == 0:
        errors.append('采样曲率超过最小转弯半径')
    return {'valid': not errors, 'errors': errors, 'computed_cost': length,
            'checker': 'urp_lab.solvers.motion.evaluate · 距离变换净空 + 三点曲率', 'source': 'panel_baseline'}, \
           {'path_length_m': length, 'min_clearance_m': clearance, 'required_clearance_m': need,
            'max_curvature_per_m': max(curv, default=0.0), 'turns': turns, 'cusps': cusps,
            'total_heading_change_rad': total_turn, 'goal_error_m': goal_error}


def solve(scenario, inst, algorithm_id, params):
    world = scenario['frame']['world']
    if algorithm_id in GRID_PLANNERS:
        pts, raw = plan_grid(world, inst, algorithm_id, params)
    else:
        pts, raw = plan_ompl(world, inst, algorithm_id, params)
    if not pts:
        return [], None, {'motion_model': inst['motion_model']}, raw, 'no_solution'
    check, metrics = evaluate(world, inst, pts)
    route = {'vehicle': 0, 'nodes': None, 'sequence': [0], 'services': [{'task': 0, 'position': len(pts) - 1}],
             'polyline': pts, 'cost': metrics['path_length_m'], 'load': 0}
    metrics['motion_model'] = inst['motion_model']
    return [route], check, metrics, raw, 'feasible' if check['valid'] else 'no_solution'
