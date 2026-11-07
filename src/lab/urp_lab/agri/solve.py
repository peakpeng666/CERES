"""Entry point for problem class `field_operation`."""
from __future__ import annotations

import time

from . import check as checker
from . import route as routes_mod
from . import search
from .model import Field

COLORS = ('#2f6f4f', '#b5622f', '#3a6ea5', '#8a5a9e', '#a58a2f', '#2f8a8a')
MODEL = {'schema': 'urp.field-operation-model/1', 'service': 'directed work lines, each traversed once with the implement engaged',
         'turns': 'P/U turns per Bochtis & Vougioukas 2008; T turn when reversing is allowed; pivot for zero-radius platforms',
         'refill': 'at declared stations at line ends only; no in-row refill', 'time': 'distance/speed per segment + pivot time + refill service; no acceleration',
         'capacity': 'implement tank or hopper in catalog units', 'not_modelled': ['soil deformation', 'implement dynamics', 'slope', 'wind or spray drift']}


def _orders(plan, F, algorithm_id, params, deadline, trace):
    seed = int(params.get('seed', 42))
    budget_floor = max(1., float(params.get('time_limit_s', 5)) * .3)   # a repair round still needs a full first solution
    iterations = params.get('iterations') or None
    K = plan.K
    if algorithm_id == 'agri.sequential':
        return search.sequential(F, K), {}
    if algorithm_id == 'agri.skip':
        return search.skip_pattern(F, K), {'skip_step': search.skip_step(F)}
    if algorithm_id == 'agri.split_anneal':
        seeds = [search.sequential(F, K), search.skip_pattern(F, K), search.nearest(plan, K)]
        start = min(seeds, key=plan.score)
        orders, count = search.anneal(plan, start, seed, deadline, iterations, trace)
        return orders, {'iterations': count}
    if algorithm_id == 'ortools.field':
        orders = search.ortools_sequence(plan, max(budget_floor, deadline - time.perf_counter()), seed)
        return search.complete(orders, F.n), {}
    if algorithm_id == 'agri.exact':
        result = search.exact_small(plan)
        if not result:
            return [], {}
        orders, value = result
        return orders, {'exact_objective': value}
    raise ValueError(f'未知算法 {algorithm_id}')


def solve(scenario: dict, inst: dict, algorithm_id: str, params: dict):
    t0 = time.perf_counter()
    budget = float(params.get('time_limit_s', 5))
    try:
        F = Field(inst)
    except ValueError as exc:
        return [], {'valid': False, 'errors': [str(exc)]}, {}, {'model_contract': MODEL}, 'no_solution', []
    plan = search.Plan(F)
    model_s = time.perf_counter() - t0
    deadline = t0 + budget
    trace: list = []
    raw = {'model_contract': {**MODEL, 'rig': inst['rig']['name'], 'tier': inst['rig'].get('tier')}, 'model_build_s': round(model_s, 4)}
    rebuilt = 0
    fixed = algorithm_id in ('agri.sequential', 'agri.skip', 'agri.exact')
    orders = None
    for attempt in range(40):
        if attempt and time.perf_counter() > deadline + max(5., 2 * budget):
            return [], {'valid': False, 'errors': ['连接几何反复不可行：' + raw['rejected_arcs'][-1]['reason']]}, {}, raw, 'no_solution', trace
        if orders is None or algorithm_id == 'ortools.field':
            orders, extra = _orders(plan, F, algorithm_id, params, deadline, trace)
            raw.update(extra)
        else:   # keep the incumbent and repair around the arc that could not be built
            orders, count = search.anneal(plan, orders, int(params.get('seed', 42)) + attempt, time.perf_counter() + max(.3, budget * .1), None, trace)
            raw['iterations'] = raw.get('iterations', 0) + count
        if not orders:
            return [], {'valid': False, 'errors': ['求解器没有返回方案']}, {}, raw, 'no_solution', trace
        served = sum(len(o) for o in orders)
        if served != F.n:
            return [], {'valid': False, 'errors': [f'方案只含 {served}/{F.n} 条作业行']}, {}, raw, 'no_solution', trace
        try:
            routes = []
            for k, order in enumerate(orders):
                _, _, cuts = plan.split(order, want_cuts=True)
                route = routes_mod.route(F, order, cuts, k)
                route['color'] = COLORS[k % len(COLORS)]
                routes.append(route)
            break
        except routes_mod.Unbuildable as e:
            rebuilt += 1
            raw.setdefault('rejected_arcs', []).append({'arc': [int(x) for x in e.arc] if e.arc else None, 'reason': e.reason})
            if not e.arc or fixed:
                return [], {'valid': False, 'errors': [e.reason]}, {}, raw, 'no_solution', trace
            F.forbidden.add(tuple(int(x) for x in e.arc))
            plan.refresh()
    else:
        return [], {'valid': False, 'errors': ['连接几何反复不可行：' + raw['rejected_arcs'][-1]['reason']]}, {}, raw, 'no_solution', trace
    chk, metrics = checker.check(inst, routes)
    metrics.update(planning_s=round(time.perf_counter() - t0, 4), rebuilt_arcs=rebuilt, machines=len(routes))
    if not chk['valid']:
        raw['rejected_routes'] = routes
        return [], chk, metrics, raw, 'no_solution', trace
    return routes, chk, metrics, raw, 'feasible', trace


def objective_value(inst: dict, metrics: dict) -> float:
    return metrics['makespan_s'] if inst.get('objective') == 'makespan' else metrics['nonworking_distance_m']
