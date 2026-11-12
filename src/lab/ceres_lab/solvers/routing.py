"""Vehicle routing adapters: turn-constrained road networks and Euclidean CVRP (incl. CVRPLIB EUC_2D)."""
from __future__ import annotations

import json
import math
import subprocess
import time
from collections import Counter

from .. import paths

paths.ensure_research_path()
from ceres_research.turn_modes import solver, checker, oracle, evaluate_candidate  # noqa: E402


def nint(x: float) -> int:
    return int(x + 0.5)


def distance_fn(objective: str):
    if objective == 'sum_rounded_euclidean_distance':
        return lambda a, b: nint(math.dist(a, b))
    return math.dist


def assignments(matrix, demands, capacity, engine, params):
    """Client sequences (depot = 0) from PyVRP or OR-Tools on an integer matrix, plus a best-so-far trace."""
    if any(d > capacity for d in demands):
        return None, []
    n = len(demands)
    limit = float(params.get('time_limit_s', 3))
    if engine == 'pyvrp':
        from pyvrp import Model
        from pyvrp.stop import MaxRuntime
        model = Model()
        locations = [model.add_location(i, 0) for i in range(n)]
        depot = model.add_depot(locations[0])
        for i in range(1, n):
            model.add_client(locations[i], delivery=demands[i])
        model.add_vehicle_type(num_available=n - 1, capacity=capacity, start_depot=depot, end_depot=depot)
        for i in range(n):
            row = matrix[i]
            for j in range(n):
                model.add_edge(locations[i], locations[j], distance=int(row[j]))
        result = model.solve(MaxRuntime(limit), seed=int(params.get('seed', 42)), display=False, collect_stats=True)
        trace, elapsed, best = [], 0.0, math.inf
        for dt, datum in zip(result.stats.runtimes, result.stats.data):
            elapsed += dt
            if datum.best_feas and datum.best_cost < best:
                best = datum.best_cost
                trace.append([round(elapsed, 4), best])
        routes = [[a.idx + 1 for a in r if a.is_client()] for r in result.best.routes()] if result.best.is_feasible() else None
        return routes, trace
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    manager = pywrapcp.RoutingIndexManager(n, n - 1, 0)
    routing = pywrapcp.RoutingModel(manager)
    distance = routing.RegisterTransitCallback(lambda i, j: int(matrix[manager.IndexToNode(i)][manager.IndexToNode(j)]))
    routing.SetArcCostEvaluatorOfAllVehicles(distance)
    demand = routing.RegisterUnaryTransitCallback(lambda i: demands[manager.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(demand, 0, [capacity] * (n - 1), True, 'Capacity')
    trace, t0 = [], time.perf_counter()

    def on_solution():
        value = routing.CostVar().Value()
        if not trace or value < trace[-1][1]:
            trace.append([round(time.perf_counter() - t0, 4), value])
    routing.AddAtSolutionCallback(on_solution)
    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.FromMilliseconds(max(1, round(limit * 1000)))
    solution = routing.SolveWithParameters(p)
    if solution is None:
        return None, trace
    routes = []
    for v in range(n - 1):
        idx, route = routing.Start(v), []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node:
                route.append(node)
            idx = solution.Value(routing.NextVar(idx))
        if route:
            routes.append(route)
    return routes, trace


def js_baseline(instance, algorithm):
    """Project construction baselines (src/frontend/core/routing/cvrp-solver.js) executed with Node."""
    script = ("const S=require(process.argv[1]);let d='';process.stdin.on('data',c=>d+=c);"
              "process.stdin.on('end',()=>{const r=S.solve(JSON.parse(d),process.argv[2]);"
              "process.stdout.write(JSON.stringify({routes:r.routes,work:r.work}))});")
    # The project baselines construct on unrounded distances; the caller re-scores with the instance's own rule.
    payload = {k: instance[k] for k in ('coordinates', 'demands', 'capacity')}
    payload.update(depot=0, fleet='unlimited_identical_vehicles', objective='sum_unrounded_euclidean_distance')
    proc = subprocess.run([*(paths.node_command() or ['node']), '-e', script, str(paths.FRONTEND_ROUTING / 'cvrp-solver.js'), algorithm],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise RuntimeError('项目 JS 基线执行失败：' + proc.stderr.strip()[-400:])
    out = json.loads(proc.stdout)
    return [[c for c in r if c] for r in out['routes'] if len(r) > 2], out.get('work')


def run_turn(instance, algorithm_id, params, limits):
    n = len(instance['customers'])
    if n > limits.get('max_tasks', 200):
        raise ValueError('超过该算法的任务数上限')
    if instance.get('fleet_limit') is not None:
        raise ValueError('此适配器尚不支持 fleet_limit')
    trace = []
    if algorithm_id.startswith('urp.'):
        raw = oracle.solve(instance) if algorithm_id == 'urp.raw_oracle' else \
            solver.solve(instance, 'reset' if algorithm_id == 'urp.reset_dp' else 'mode')[0]
        status = raw['status']
    else:
        tab = solver.preprocess(instance, 'reset')
        matrix = [[0] + tab['start']] + [[tab['end'][i]] + [0 if i == j else tab['conn'][i][j] for j in range(n)] for i in range(n)]
        matrix = [[int(c) if math.isfinite(c) else 10 ** 9 for c in row] for row in matrix]
        seqs, trace = assignments(matrix, [0] + [c['demand'] for c in instance['customers']], instance['capacity'],
                                  algorithm_id.split('.')[0], params)
        ev = evaluate_candidate.evaluate(instance, {'service_sequences': [[instance['customers'][i - 1]['id'] for i in r] for r in seqs]}) if seqs else {}
        raw = ev.get('solution', {'status': 'no_solution', 'cost': None, 'routes': []})
        status = 'feasible' if ev.get('accepted') else 'no_solution'
    check = checker.check(instance, raw) if raw['routes'] else None
    if check:
        check.update(checker='src/research/src/ceres_research/turn_modes/checker.py', source='project')
    tasks = {c['id']: c for c in instance['customers']}
    routes = [dict(vehicle=i, nodes=r['nodes'], sequence=[s['customer'] for s in r['services']],
                   services=[dict(task=s['customer'], position=s['position']) for s in r['services']],
                   polyline=[instance.get('coordinates', {}).get(str(v)) for v in r['nodes']], cost=r['cost'],
                   load=sum(tasks[s['customer']]['demand'] for s in r['services']), mode_chain=r.get('mode_chain'))
              for i, r in enumerate(raw['routes'])]
    for r in routes:
        if any(p is None for p in r['polyline']):
            r['polyline'] = []
    penalties = dict((tuple(t[:3]), t[3]) for t in instance['turn_penalties'])
    extra = {'forbidden_turn_violations': sum('forbidden_turn' in e for e in (check or {}).get('errors', [])),
             'turn_penalty_total': sum(penalties.get(t, 0) for r in routes for t in zip(r['nodes'], r['nodes'][1:], r['nodes'][2:]))}
    return routes, check, raw['cost'], status, extra, raw, trace


def run_cvrp(instance, algorithm_id, params):
    pts, dem, cap = instance['coordinates'], instance['demands'], instance['capacity']
    rounded = instance.get('objective') == 'sum_rounded_euclidean_distance'
    dist = distance_fn(instance.get('objective'))
    trace, work, scale = [], None, 1 if rounded else 1000
    if algorithm_id.startswith('urp.js.'):
        seqs, work = js_baseline(instance, 'parallel_savings' if algorithm_id == 'urp.js.savings' else 'nearest_neighbor')
    else:
        matrix = [[nint(math.dist(a, b)) if rounded else round(math.dist(a, b) * 1000) for b in pts] for a in pts]
        seqs, trace = assignments(matrix, dem, cap, algorithm_id.split('.')[0], params)
        trace = [[t, v / scale] for t, v in trace]
    routes = []
    for i, seq in enumerate(seqs or []):
        nodes = [0] + seq + [0]
        routes.append(dict(vehicle=i, nodes=nodes, sequence=seq, services=[dict(task=t, position=j + 1) for j, t in enumerate(seq)],
                           polyline=[pts[j] for j in nodes], cost=sum(dist(pts[a], pts[b]) for a, b in zip(nodes, nodes[1:])),
                           load=sum(dem[j] for j in seq)))
    objective = sum(r['cost'] for r in routes) if routes else None
    errors = []
    if Counter(t for r in routes for t in r['sequence']) != Counter(range(1, len(pts))):
        errors.append('exact_once_service')
    if any(sum(dem[j] for j in r['nodes']) > cap for r in routes):
        errors.append('capacity')
    rule = 'TSPLIB EUC_2D 取整距离' if rounded else '未取整欧氏距离'
    check = dict(valid=not errors, errors=errors, computed_cost=objective,
                 checker=f'ceres_lab.solvers.routing · 按原始坐标与需求复算（{rule}）', source='panel_baseline') if routes else None
    status = 'feasible' if routes else ('infeasible' if max(dem) > cap else 'no_solution')
    raw = {'assignment': seqs, 'search_cost_scale': scale, 'objective_rule': rule, 'construction_work': work}
    return routes, check, objective, status, {}, raw, trace
