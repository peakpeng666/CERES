"""Dynamic task streams: planning-level discrete-event simulation and learning when to re-optimise (P3 prototype).

Tasks of a stream are released over time inside a real field. Vehicles move at constant speed along the
workbench's travel graph (shortest paths around obstacles, or along crop-row lanes); a vehicle that has left
for its next stop is committed to it ("lock committed"). At every release the dispatcher either inserts the new
task at its cheapest position (milliseconds) or re-optimises all uncommitted tasks with PyVRP from the
vehicles' committed stops (open routes, fixed budget and seed). The research question is the value of
computation: when does a re-optimisation pay off enough to justify it?

Objective: total travel distance of all vehicles until every released task is served (an energy proxy);
mean and 95th-percentile response time (service start − release) are reported alongside. This is a planning
simulation, not a Gazebo execution; it uses the constant-speed proxy and fixed service times.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

SCALE = 100          # PyVRP works on integer centimetres


@dataclass
class Stream:
    key: str
    D: np.ndarray                 # metres, node 0 = depot, nodes 1..n = tasks
    release: np.ndarray           # release time (s) of each task node (index aligned with D, release[0] = 0)
    vehicles: int
    speed: float
    service_s: float
    field: str = ''
    xy: np.ndarray | None = None  # node coordinates (features only)


@dataclass
class Vehicle:
    node: int = 0
    t_free: float = 0.0           # time the vehicle has finished service at `node`
    route: list = field(default_factory=list)


def make_stream(field_id: str, seed: int, n_tasks: int = 40, base_share: float = 0.25, horizon_s: float = 3600.0,
                vehicles: int = 3, speed: float = 1.0, service_s: float = 30.0, travel: str = 'free') -> Stream:
    """Tasks from the workbench generator inside a development field; a quarter released at t = 0, the rest by a
    Poisson process over the horizon (uniform order statistics)."""
    from ..solvers.fleet import travel_graph
    from . import instances as I
    _sc, inst = I.fleet_instance(field_id, n_tasks, 'uniform', travel, 'total_distance', seed, False)
    g = travel_graph(inst)
    D = np.asarray(g.D, dtype=float)
    if not np.isfinite(D).all():
        raise ValueError('travel graph has unreachable tasks')
    rng = np.random.default_rng(seed)
    n = len(inst['tasks'])
    n_base = max(1, int(round(base_share * n)))
    times = np.concatenate([np.zeros(n_base), np.sort(rng.uniform(0, horizon_s, n - n_base))])
    order = rng.permutation(n)                   # which task is released when
    release = np.zeros(n + 1)
    release[1 + order] = times
    xy = np.array([inst['depot']] + [t['xy'] for t in inst['tasks']], dtype=float)
    return Stream(key=f'{field_id}|s{seed}|n{n}|{travel}', D=D, release=release, vehicles=vehicles, speed=speed,
                  service_s=service_s, field=field_id, xy=xy)


# ------------------------------------------------------------------ planning primitives

def route_cost(D, start: int, route: list[int]) -> float:
    cost, cur = 0.0, start
    for j in route:
        cost += D[cur, j]
        cur = j
    return cost


def plan_cost(D, fleet: list[Vehicle]) -> float:
    return sum(route_cost(D, v.node, v.route) for v in fleet)


def cheapest_insertion(D, fleet: list[Vehicle], task: int) -> tuple[int, int, float]:
    """(vehicle, position, added distance) of the cheapest open-route insertion after the committed stop."""
    best = (0, 0, math.inf)
    for k, v in enumerate(fleet):
        seq = [v.node] + v.route
        for pos in range(len(v.route) + 1):
            a = seq[pos]
            if pos < len(v.route):
                b = seq[pos + 1]
                delta = D[a, task] + D[task, b] - D[a, b]
            else:
                delta = D[a, task]
            if delta < best[2] - 1e-12:
                best = (k, pos, delta)
    return best


def reoptimise(D, fleet: list[Vehicle], budget_s: float, seed: int) -> tuple[list[list[int]], float]:
    """PyVRP on all uncommitted tasks: each vehicle starts at its committed stop, routes are open (free return)."""
    from pyvrp import Model
    from pyvrp.stop import MaxRuntime
    tasks = sorted({j for v in fleet for j in v.route})
    if not tasks:
        return [[] for _ in fleet], 0.0
    t0 = time.perf_counter()
    model = Model()
    starts = [model.add_location(0, 0) for _ in fleet]
    end = model.add_location(0, 0)
    starts_d = [model.add_depot(loc) for loc in starts]
    end_d = model.add_depot(end)
    clients = [model.add_location(0, 0) for _ in tasks]
    for loc in clients:
        model.add_client(loc, delivery=0)
    for k, d in enumerate(starts_d):
        model.add_vehicle_type(num_available=1, capacity=0, start_depot=d, end_depot=end_d)
    nodes = [v.node for v in fleet]
    M = np.rint(D * SCALE).astype(np.int64)
    big = int(M.max() * (len(tasks) + 2) + 1)
    for a, na in enumerate(nodes):
        for c, nc in zip(clients, tasks):
            model.add_edge(starts[a], c, distance=int(M[na, nc]))
            model.add_edge(c, starts[a], distance=big)
        model.add_edge(starts[a], end, distance=0)
        for b in range(len(nodes)):
            if a != b:
                model.add_edge(starts[a], starts[b], distance=big)
        model.add_edge(end, starts[a], distance=big)
    for ci, (c, nc) in enumerate(zip(clients, tasks)):
        model.add_edge(c, end, distance=0)
        model.add_edge(end, c, distance=big)
        for cj, (c2, nc2) in enumerate(zip(clients, tasks)):
            if ci != cj:
                model.add_edge(c, c2, distance=int(M[nc, nc2]))
    result = model.solve(MaxRuntime(budget_s), seed=seed, display=False)
    if not result.best.is_feasible():
        raise RuntimeError('PyVRP returned an infeasible open-route solution')
    routes = [[] for _ in fleet]
    for r in result.best.routes():
        routes[int(r.vehicle_type())] = [tasks[a.idx] for a in r if a.is_client()]
    return routes, time.perf_counter() - t0


# ------------------------------------------------------------------ simulation

def features(stream: Stream, fleet: list[Vehicle], t: float, task: int, ins_delta: float, since_reopt: int,
             inserted_since: int) -> dict:
    pending = [j for v in fleet for j in v.route]
    scale = float(np.sqrt(np.ptp(stream.xy[:, 0]) * np.ptp(stream.xy[:, 1]) + 1e-9))
    cost = plan_cost(stream.D, fleet)
    spread = float(np.std(stream.xy[pending], axis=0).mean() / scale) if len(pending) > 1 else 0.0
    loads = [len(v.route) for v in fleet]
    return {'pending': float(len(pending)), 'pending_per_vehicle': len(pending) / len(fleet),
            'load_imbalance': (max(loads) - min(loads)) / max(1.0, np.mean(loads)) if loads else 0.0,
            'insertion_delta_rel': ins_delta / max(cost, 1e-9), 'insertion_delta_scaled': ins_delta / scale,
            'since_reopt': float(since_reopt), 'inserted_since_reopt': float(inserted_since),
            'inserted_share': inserted_since / max(1.0, len(pending)), 'spread': spread, 'time_share': t / max(1.0, stream.release.max())}


FEATURES = ('pending', 'pending_per_vehicle', 'load_imbalance', 'insertion_delta_rel', 'insertion_delta_scaled',
            'since_reopt', 'inserted_since_reopt', 'inserted_share', 'spread', 'time_share')


def simulate(stream: Stream, policy, budget_s: float = 0.5, seed: int = 42, label: bool = False) -> dict:
    """Run one stream. ``policy(state) -> 'insert' | 'reopt'``; ``label`` also evaluates re-optimisation at every
    release (counterfactual one-step gain) without applying it unless the policy chooses it."""
    D, v_ms = stream.D, stream.speed
    n = len(stream.release) - 1
    fleet = [Vehicle() for _ in range(stream.vehicles)]
    service_start = np.full(n + 1, np.nan)
    travelled = 0.0
    decisions, cpu = [], 0.0
    since_reopt, inserted_since = 0, 0
    order = sorted(range(1, n + 1), key=lambda j: (stream.release[j], j))
    pending_release = [j for j in order]

    def advance(until: float):
        nonlocal travelled
        for v in fleet:
            while v.route and v.t_free <= until:
                nxt = v.route.pop(0)                         # departing commits the leg
                leg = D[v.node, nxt]
                travelled += leg
                arrive = v.t_free + leg / v_ms
                service_start[nxt] = max(arrive, stream.release[nxt])
                v.t_free = service_start[nxt] + stream.service_s
                v.node = nxt
            if not v.route and v.t_free < until and math.isfinite(until):
                v.t_free = until                             # idle until a new assignment can start

    i = 0
    while i < len(pending_release):
        t = stream.release[pending_release[i]]
        batch = []
        while i < len(pending_release) and stream.release[pending_release[i]] == t:
            batch.append(pending_release[i])
            i += 1
        advance(t)
        for task in batch:
            k, pos, delta = cheapest_insertion(D, fleet, task)
            state = features(stream, fleet, t, task, delta, since_reopt, inserted_since)
            before = plan_cost(D, fleet)
            fleet[k].route.insert(pos, task)
            ins_cost = before + delta
            if getattr(policy, 'rollout', False) and t > 0:
                routes, dt = reoptimise(D, fleet, budget_s, seed)
                cpu += dt
                re_cost = sum(route_cost(D, v.node, r) for v, r in zip(fleet, routes))
                released = [j for j in range(1, n + 1) if stream.release[j] <= t]
                action, est = policy.choose(D, fleet, routes, released, stream.release, t)
                decisions.append({'t': float(t), 'task': int(task), 'features': state, 'action': action, 'insert_cost': ins_cost,
                                  'reopt_cost': re_cost, 'reopt_s': dt, 'rollout_estimate': est})
                if action == 'reopt':
                    for v, r in zip(fleet, routes):
                        v.route = r
                    since_reopt, inserted_since = 0, 0
                else:
                    since_reopt += 1
                    inserted_since += 1
                continue
            action = policy(state) if t > 0 else 'insert'
            rec = {'t': float(t), 'task': int(task), 'features': state, 'action': action, 'insert_cost': ins_cost}
            if action == 'reopt' or (label and t > 0):
                routes, dt = reoptimise(D, fleet, budget_s, seed)
                cpu += dt
                re_cost = sum(route_cost(D, v.node, r) for v, r in zip(fleet, routes))
                rec.update({'reopt_cost': re_cost, 'gain_rel': (ins_cost - re_cost) / max(ins_cost, 1e-9), 'reopt_s': dt})
                if action == 'reopt' and re_cost < ins_cost - 1e-9:
                    for v, r in zip(fleet, routes):
                        v.route = r
                if action == 'reopt':
                    since_reopt, inserted_since = 0, 0
            if action != 'reopt':
                since_reopt += 1
                inserted_since += 1
            decisions.append(rec)
        if t == 0:                                           # every policy starts from the same optimised static plan
            routes, dt = reoptimise(D, fleet, budget_s, seed)
            cpu += dt
            if sum(route_cost(D, v.node, r) for v, r in zip(fleet, routes)) < plan_cost(D, fleet) - 1e-9:
                for v, r in zip(fleet, routes):
                    v.route = r
            since_reopt, inserted_since = 0, 0
    advance(math.inf)
    if np.isnan(service_start[1:]).any():
        raise AssertionError('some released tasks were never served')
    response = service_start[1:] - stream.release[1:]
    return {'stream': stream.key, 'field': stream.field, 'distance_m': travelled, 'mean_response_s': float(response.mean()),
            'p95_response_s': float(np.percentile(response, 95)), 'makespan_s': float(max(v.t_free for v in fleet)),
            'reopts': sum(1 for d in decisions if d['action'] == 'reopt'), 'solver_s': cpu, 'decisions': decisions}


# ------------------------------------------------------------------ policies

def always(action: str):
    return lambda state: action


def periodic(k: int):
    return lambda state: 'reopt' if state['since_reopt'] + 1 >= k else 'insert'


class GainModel:
    """Ridge regression of the one-step relative gain of re-optimisation on decision features (log-target free)."""

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha

    def fit(self, X: np.ndarray, y: np.ndarray):
        self.mu, sd = X.mean(axis=0), X.std(axis=0)
        self.sd = np.where(sd > 1e-12, sd, 1.0)
        Z = np.hstack([np.ones((len(X), 1)), (X - self.mu) / self.sd])
        reg = self.alpha * np.eye(Z.shape[1])
        reg[0, 0] = 0.0
        self.w = np.linalg.solve(Z.T @ Z + reg, Z.T @ y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        Z = np.hstack([np.ones((len(X), 1)), (X - self.mu) / self.sd])
        return Z @ self.w


def learned(model: GainModel, threshold: float):
    def policy(state):
        x = np.array([[state[k] for k in FEATURES]])
        return 'reopt' if float(model.predict(x)[0]) > threshold else 'insert'
    return policy


def pilot(fields: list[str], seeds=(7,), travels=('free', 'rows'), n_tasks: int = 40, vehicles: int = 3,
          budget_s: float = 0.3) -> dict:
    """Exploratory comparison of insert / periodic(3) / always-reopt on development fields (no learning)."""
    rows, failures = [], []
    for fid in fields:
        for seed in seeds:
            for travel in travels:
                try:
                    s = make_stream(fid, seed=seed, n_tasks=n_tasks, vehicles=vehicles, travel=travel)
                except Exception as exc:  # noqa: BLE001 - recorded, not hidden
                    failures.append({'field': fid, 'seed': seed, 'travel': travel, 'error': f'{type(exc).__name__}: {exc}'[:300]})
                    continue
                out = {}
                for name, pol in (('insert', always('insert')), ('periodic3', periodic(3)), ('reopt', always('reopt'))):
                    out[name] = simulate(s, pol, budget_s=budget_s, seed=42, label=(name == 'insert'))
                gains = [d['gain_rel'] for d in out['insert']['decisions'] if 'gain_rel' in d]
                rows.append({'field': fid, 'seed': seed, 'travel': travel, 'tasks': len(s.release) - 1,
                             **{f'{k}_{m}': out[k][m] for k in out for m in ('distance_m', 'mean_response_s', 'p95_response_s', 'reopts', 'solver_s')},
                             'one_step_gain_median': float(np.median(gains)) if gains else None,
                             'one_step_gain_p90': float(np.percentile(gains, 90)) if gains else None,
                             'one_step_gain_max': float(np.max(gains)) if gains else None,
                             'one_step_positive_share': float(np.mean(np.array(gains) > 1e-6)) if gains else None})
    return {'protocol': {'tasks': n_tasks, 'vehicles': vehicles, 'speed_mps': 1.0, 'service_s': 30.0, 'horizon_s': 3600.0,
                         'base_share': 0.25, 'budget_s': budget_s, 'solver_seed': 42, 'seeds': list(seeds), 'travels': list(travels)},
            'rows': rows, 'failures': failures}



class Rollout:
    """Choose insert vs re-optimised plan by sampling futures from the past only (no peeking).

    The future arrival count over the remaining shift is Poisson with the rate observed so far; future task
    locations are drawn with replacement from tasks already released; each sampled future is inserted into a copy
    of each candidate plan by cheapest insertion (vehicle motion ignored) and the plan with the lower mean final
    route cost is kept. The solver is always called, so its CPU equals always-re-optimise; what differs is
    whether its plan is used.
    """
    rollout = True

    def __init__(self, samples: int = 3, seed: int = 0):
        self.samples = samples
        self.rng = np.random.default_rng(seed)

    def choose(self, D, fleet, routes_re, released, release, t):
        horizon_end = float(release.max())
        dynamic_released = [j for j in released if release[j] > 0]
        rate = len(dynamic_released) / max(t, 1e-9)
        remaining = max(0.0, horizon_end - t)
        plans = {'insert': [list(v.route) for v in fleet], 'reopt': [list(r) for r in routes_re]}
        totals = {k: 0.0 for k in plans}
        for _ in range(self.samples):
            n_future = int(self.rng.poisson(rate * remaining))
            future = list(self.rng.choice(released, size=n_future, replace=True)) if released and n_future else []
            for name, routes in plans.items():
                trial = [Vehicle(node=v.node, t_free=v.t_free, route=list(r)) for v, r in zip(fleet, routes)]
                for task in future:
                    k, pos, _d = cheapest_insertion(D, trial, int(task))
                    trial[k].route.insert(pos, int(task))
                totals[name] += plan_cost(D, trial) / self.samples
        action = 'reopt' if totals['reopt'] < totals['insert'] - 1e-9 else 'insert'
        return action, {k: float(v) for k, v in totals.items()}


def study_stream(field: str, rate_label: str, horizon_s: float, vehicles: int, seed: int, budget_s: float = 0.3) -> dict:
    """All policies on one stream (same stream, same solver seed)."""
    s = make_stream(field, seed=seed, n_tasks=40, vehicles=vehicles, travel='free', horizon_s=horizon_s)
    out = {'field': field, 'rate': rate_label, 'horizon_s': horizon_s, 'vehicles': vehicles, 'seed': seed, 'tasks': len(s.release) - 1}
    for name, pol in (('insert', always('insert')), ('periodic3', periodic(3)), ('reopt', always('reopt')),
                      ('rollout', Rollout(samples=3, seed=seed))):
        r = simulate(s, pol, budget_s=budget_s, seed=42)
        out[name] = {k: r[k] for k in ('distance_m', 'mean_response_s', 'p95_response_s', 'makespan_s', 'reopts', 'solver_s')}
        if name == 'rollout':
            out[name]['reopt_chosen'] = sum(1 for d in r['decisions'] if d['action'] == 'reopt')
    return out


RATES = {'sparse': 3600.0, 'medium': 1200.0, 'dense': 400.0}     # 30 dynamic arrivals over the horizon (s)


def _study_job(args):
    import os as _os
    field, rate, horizon, k, seed, cores = args
    if cores and hasattr(_os, 'sched_setaffinity'):
        _os.sched_setaffinity(0, set(cores))
    try:
        return study_stream(field, rate, horizon, k, seed)
    except Exception as exc:  # noqa: BLE001 - recorded, never hidden
        return {'field': field, 'rate': rate, 'horizon_s': horizon, 'vehicles': k, 'seed': seed, 'error': f'{type(exc).__name__}: {exc}'[:300]}


def run_study(out_dir, fields: list[str], vehicles=(2, 4), seed: int = 7, workers: int = 4, cores=None) -> dict:
    """Resumable study over fields × arrival rates × fleet sizes; one JSON per stream under ``streams/``."""
    import json as _json
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from pathlib import Path as _P
    out_dir = _P(out_dir)
    (out_dir / 'streams').mkdir(parents=True, exist_ok=True)
    todo = []
    for f in fields:
        for rate, horizon in RATES.items():
            for k in vehicles:
                key = f'{f}__{rate}__k{k}__s{seed}'
                if not (out_dir / 'streams' / f'{key}.json').exists():
                    todo.append((key, (f, rate, horizon, k, seed, cores)))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_study_job, a): key for key, a in todo}
        for fu in as_completed(futs):
            res = fu.result()
            (out_dir / 'streams' / f'{futs[fu]}.json').write_text(_json.dumps(res, ensure_ascii=False, default=float), encoding='utf-8')
    return summarise_study(out_dir)


def summarise_study(out_dir) -> dict:
    import json as _json
    from pathlib import Path as _P
    from scipy import stats as _stats
    rows = [_json.loads(p.read_text(encoding='utf-8')) for p in sorted((_P(out_dir) / 'streams').glob('*.json'))]
    ok = [r for r in rows if 'error' not in r]
    summary = {'streams': len(rows), 'errors': [r for r in rows if 'error' in r], 'cells': {}}
    rng = np.random.default_rng(20261024)
    for rate in RATES:
        for k in sorted({r['vehicles'] for r in ok}):
            cell = [r for r in ok if r['rate'] == rate and r['vehicles'] == k]
            if not cell:
                continue
            res = {'n': len(cell)}
            for pol in ('periodic3', 'reopt', 'rollout'):
                for metric in ('distance_m', 'mean_response_s', 'makespan_s'):
                    d = np.array([(r[pol][metric] - r['insert'][metric]) / r['insert'][metric] for r in cell])
                    nz = d[np.abs(d) > 1e-12]
                    p = float(_stats.wilcoxon(nz).pvalue) if len(nz) >= 10 else None
                    boots = [rng.choice(d, len(d), replace=True).mean() for _ in range(2000)]
                    res[f'{pol}_vs_insert_{metric}'] = {'mean_rel': float(d.mean()), 'ci95': [float(x) for x in np.percentile(boots, [2.5, 97.5])],
                                                        'p_two_sided': p}
            res['rollout_reopt_share'] = float(np.mean([r['rollout']['reopt_chosen'] / max(1, r['tasks']) for r in cell]))
            summary['cells'][f'{rate}@k{k}'] = res
    (_P(out_dir) / 'summary.json').write_text(_json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    return {c: {m: round(v['mean_rel'], 3) for m, v in r.items() if isinstance(v, dict)} for c, r in summary['cells'].items()}
