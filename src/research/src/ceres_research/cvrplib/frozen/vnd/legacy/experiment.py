"""Auditable, standard-library-only fixed destroy/repair experiment.

No generated code, model calls, adaptive selection, third-party execution,
old instances, or solver-reported-only acceptance. See frozen PLAN.json.
"""
import argparse
import copy
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import signal
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor'))
from urp_cvrp.generate import generate_instance
from urp_cvrp.instance import from_dict
from urp_cvrp.checker import check_solution


class StopBudget(RuntimeError):
    pass


class Budget:
    def __init__(self, deltas=100000, cpu=2.0, wall=5.0):
        self.limit = deltas
        self.cpu_limit = cpu
        self.wall_limit = wall
        self.cpu_start = time.process_time()
        self.wall_start = time.perf_counter()
        self.deltas = 0
        self.by_kind = {'savings': 0, 'removal': 0, 'insertion': 0}
        self.matrix_distances = 0
        self.objective_edges = 0
        self.checks = 0

    def check(self):
        self.checks += 1
        if time.process_time() - self.cpu_start >= self.cpu_limit:
            raise StopBudget('cpu_budget')
        if time.perf_counter() - self.wall_start >= self.wall_limit:
            raise StopBudget('wall_budget')

    def delta(self, kind):
        self.check()
        if self.deltas >= self.limit:
            raise StopBudget('delta_budget')
        # This call occurs immediately before the arithmetic expression.
        self.deltas += 1
        self.by_kind[kind] += 1

    def snapshot(self):
        return {'deltas': self.deltas, 'delta_by_kind': dict(self.by_kind),
                'matrix_distances': self.matrix_distances,
                'objective_edges': self.objective_edges, 'budget_checks': self.checks,
                'solver_cpu_seconds': time.process_time() - self.cpu_start,
                'solver_wall_seconds': time.perf_counter() - self.wall_start}


def matrix_for(instance, budget):
    matrix = []
    for a in instance.coordinates:
        row = []
        for b in instance.coordinates:
            budget.check()
            budget.matrix_distances += 1
            row.append(math.hypot(a[0] - b[0], a[1] - b[1]))
        matrix.append(row)
    return matrix


def cost_of(routes, matrix, budget):
    lengths = []
    for route in routes:
        for a, b in zip([0] + route, route + [0]):
            budget.check()
            budget.objective_edges += 1
            lengths.append(matrix[a][b])
    return math.fsum(lengths)


def savings(instance, matrix, budget):
    """Same merge/tie semantics as vendored parallel savings; add delta ledger."""
    n = instance.n_customers
    routes = {i: [i] for i in range(1, n + 1)}
    owner = {i: i for i in routes}
    loads = {i: instance.demands[i] for i in routes}
    candidates = []
    for i in range(1, n + 1):
        for j in range(i + 1, n + 1):
            budget.delta('savings')
            candidates.append((-(matrix[0][i] + matrix[0][j] - matrix[i][j]), i, j))
    candidates.sort()
    for negative_saving, i, j in candidates:
        budget.check()
        if negative_saving >= 0:
            continue
        left_id, right_id = owner[i], owner[j]
        if left_id == right_id or loads[left_id] + loads[right_id] > instance.capacity:
            continue
        left, right = routes[left_id], routes[right_id]
        if i not in (left[0], left[-1]) or j not in (right[0], right[-1]):
            continue
        if left[-1] != i:
            left = list(reversed(left))
        if right[0] != j:
            right = list(reversed(right))
        merged = left + right
        routes[left_id] = merged
        loads[left_id] += loads.pop(right_id)
        del routes[right_id]
        for node in merged:
            owner[node] = left_id
    return [routes[key] for key in sorted(routes)]


def derived_seed(seed, kind, iteration):
    return int.from_bytes(hashlib.sha256(f'{seed}/{kind}/{iteration}'.encode()).digest()[:8], 'big')


def destroy(routes, name, q, rng, matrix, budget):
    partial = [route[:] for route in routes]
    if name == 'random':
        removed = rng.sample(sorted(node for route in partial for node in route), q)
        selected = set(removed)
        partial = [[node for node in route if node not in selected] for route in partial]
    elif name == 'worst':
        removed = []
        for _ in range(q):
            options = []
            for ri, route in enumerate(partial):
                for pi, node in enumerate(route):
                    left, right = (route[pi - 1] if pi else 0), (route[pi + 1] if pi + 1 < len(route) else 0)
                    budget.delta('removal')
                    gain = matrix[left][node] + matrix[node][right] - matrix[left][right]
                    options.append((-gain, node, ri, pi))
            _, node, ri, pi = min(options)
            removed.append(node)
            partial[ri].pop(pi)
    else:
        raise ValueError('unknown destroy operator')
    return [route for route in partial if route], removed


def route_options(node, routes, loads, instance, matrix, budget):
    """Best feasible insertion per existing route plus fresh singleton route."""
    options = []
    for ri, route in enumerate(routes):
        if loads[ri] + instance.demands[node] > instance.capacity:
            continue
        best = None
        for pi in range(len(route) + 1):
            left = route[pi - 1] if pi else 0
            right = route[pi] if pi < len(route) else 0
            budget.delta('insertion')
            delta = matrix[left][node] + matrix[node][right] - matrix[left][right]
            option = (delta, ri, pi)
            if best is None or option < best:
                best = option
        options.append(best)
    if instance.demands[node] <= instance.capacity:
        budget.delta('insertion')
        options.append((matrix[0][node] + matrix[node][0], len(routes), 0))
    return sorted(options)


def repair(partial, removed, name, instance, matrix, budget):
    routes = [route[:] for route in partial]
    loads = [sum(instance.demands[node] for node in route) for route in routes]
    remaining = set(removed)
    while remaining:
        choices = []
        for node in sorted(remaining):
            options = route_options(node, routes, loads, instance, matrix, budget)
            if not options:
                raise RuntimeError('no_feasible_insertion')
            delta, ri, pi = options[0]
            if name == 'cheapest':
                key = (delta, node, ri, pi)
            elif name == 'regret2':
                regret = options[1][0] - delta if len(options) > 1 else math.inf
                key = (-regret, delta, node, ri, pi)
            else:
                raise ValueError('unknown repair operator')
            choices.append((key, node, ri, pi))
        _, node, ri, pi = min(choices)
        if ri == len(routes):
            routes.append([node])
            loads.append(instance.demands[node])
        else:
            routes[ri].insert(pi, node)
            loads[ri] += instance.demands[node]
        if loads[ri] > instance.capacity:
            raise RuntimeError('internal_capacity_violation')
        remaining.remove(node)
    return routes


def output_for(routes, cost):
    return {'routes': [[0] + route + [0] for route in routes], 'reported_cost': cost}


def run_one(instance, pair, seed, iterations, cfg):
    budget = Budget(cfg['delta_limit'], cfg['cpu_limit_seconds'], cfg['wall_limit_seconds'])
    incumbent = baseline = None
    accepted = completed = started = 0
    trace = []
    stop_reason, error = 'iteration_budget', None
    def on_wall(signum, frame):
        raise StopBudget('hard_wall_budget')
    def on_cpu(signum, frame):
        raise StopBudget('hard_cpu_budget')
    old_wall = signal.signal(signal.SIGALRM, on_wall)
    old_cpu = signal.signal(signal.SIGPROF, on_cpu)
    signal.setitimer(signal.ITIMER_REAL, cfg['wall_limit_seconds'])
    signal.setitimer(signal.ITIMER_PROF, cfg['cpu_limit_seconds'])
    try:
        matrix = matrix_for(instance, budget)
        routes = savings(instance, matrix, budget)
        cost = cost_of(routes, matrix, budget)
        incumbent = output_for(routes, cost)
        baseline = copy.deepcopy(incumbent)
        for iteration in range(iterations):
            budget.check()
            started += 1
            lo = min(instance.n_customers, max(2, math.ceil(.1 * instance.n_customers)))
            hi = min(instance.n_customers, max(lo, math.ceil(.3 * instance.n_customers)))
            q = random.Random(derived_seed(seed, 'q', iteration)).randint(lo, hi)
            rng = random.Random(derived_seed(seed, 'removal', iteration))
            partial, removed = destroy(routes, pair[0], q, rng, matrix, budget)
            candidate = repair(partial, removed, pair[1], instance, matrix, budget)
            candidate_cost = cost_of(candidate, matrix, budget)
            completed += 1
            is_accepted = candidate_cost < cost - 1e-9
            if is_accepted:
                routes, cost = candidate, candidate_cost
                incumbent = output_for(routes, cost)
                accepted += 1
            trace.append({'iteration': iteration, 'q': q, 'candidate_cost': candidate_cost,
                          'accepted': is_accepted, 'incumbent_cost': cost, 'deltas': budget.deltas})
    except StopBudget as exc:
        stop_reason = str(exc)
    except Exception as exc:
        stop_reason = 'algorithm_failure'
        error = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGALRM, old_wall)
        signal.signal(signal.SIGPROF, old_cpu)
    usage = budget.snapshot()
    cstart, wstart = time.process_time(), time.perf_counter()
    checked = check_solution(instance, incumbent)
    baseline_checked = check_solution(instance, baseline)
    checker_cpu, checker_wall = time.process_time() - cstart, time.perf_counter() - wstart
    improvement = None
    if checked.accepted and baseline_checked.accepted:
        improvement = (baseline_checked.recomputed_cost - checked.recomputed_cost) / baseline_checked.recomputed_cost
    return {'pair': '_'.join(pair), 'destroy': pair[0], 'repair': pair[1], 'search_seed': seed,
            'instance_id': instance.instance_id, 'instance_sha256': instance.sha256,
            'family': instance.family, 'n': instance.n_customers, 'stop_reason': stop_reason,
            'error': error, 'iterations_started': started, 'iterations_completed': completed,
            'accepted_improvements': accepted, 'improvement_fraction': improvement,
            'output': incumbent, 'baseline': baseline, 'check': checked.to_dict(),
            'baseline_check': baseline_checked.to_dict(), 'trace': trace, **usage,
            'checker_cpu_seconds': checker_cpu, 'checker_wall_seconds': checker_wall}


def dump_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def generate_data(cfg):
    entries = []
    for stage, specs in [('smoke', cfg['smoke_instances']), ('formal', cfg['instances'])]:
        for index, spec in enumerate(specs):
            instance = generate_instance(spec['n'], spec['family'], spec['seed'], cfg['capacity'])
            path = ROOT / 'data' / (stage + '-' + str(index) + '.json')
            dump_json(path, instance.to_dict())
            entries.append({'stage': stage, 'index': index, **spec, 'file': str(path.relative_to(ROOT)),
                            'instance_id': instance.instance_id, 'canonical_sha256': instance.sha256,
                            'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    dump_json(ROOT / 'data' / 'manifest.json', {'namespace': cfg['namespace'], 'split': 'dev', 'entries': entries})
    return entries


def verify_frozen(cfg):
    freeze = json.loads((ROOT / 'FREEZE.json').read_text())
    if hashlib.sha256((ROOT / 'PLAN.json').read_bytes()).hexdigest() != freeze['plan_sha256']:
        raise RuntimeError('frozen_plan_changed')
    for item in json.loads((ROOT / 'SOURCE_MANIFEST.json').read_text())['sources']:
        if 'copy' in item and hashlib.sha256((ROOT / item['copy']).read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('vendor_changed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=['smoke', 'formal', 'all'], default='all')
    args = parser.parse_args()
    cfg = json.loads((ROOT / 'PLAN.json').read_text())
    verify_frozen(cfg)
    entries = generate_data(cfg)
    affinity_before = sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None
    selected_cpu = affinity_before[-1] if affinity_before else None
    if selected_cpu is not None:
        os.sched_setaffinity(0, {selected_cpu})
    dump_json(ROOT / 'ENVIRONMENT.json', {'python': sys.version, 'stdlib_only': True,
              'venv_prefix': sys.prefix, 'base_prefix': sys.base_prefix, 'pid': os.getpid(),
              'affinity_before': affinity_before, 'selected_cpu': selected_cpu,
              'start_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    for stage in (['smoke', 'formal'] if args.stage == 'all' else [args.stage]):
        out = ROOT / 'results' / (stage + '.jsonl')
        if out.exists():
            raise RuntimeError('Refuse to rerun/overwrite existing stage results: ' + str(out))
        iterations = cfg['smoke_iterations'] if stage == 'smoke' else cfg['iterations']
        seeds = cfg['smoke_search_seeds'] if stage == 'smoke' else cfg['search_seeds']
        cumulative_cpu = 0.0
        count = 0
        with out.open('x') as handle:
            for entry in [e for e in entries if e['stage'] == stage]:
                instance = from_dict(json.loads((ROOT / entry['file']).read_text()))
                group = []
                for si, seed in enumerate(seeds):
                    shift = (entry['index'] + si) % 4
                    order = cfg['pairs'][shift:] + cfg['pairs'][:shift]
                    for pair in order:
                        if stage == 'formal' and cumulative_cpu + cfg['cpu_limit_seconds'] > cfg['formal_cumulative_cpu_limit_seconds']:
                            record = {'stage': stage, 'instance_id': instance.instance_id, 'pair': '_'.join(pair),
                                      'search_seed': seed, 'stop_reason': 'cumulative_cpu_guard_skipped'}
                        else:
                            record = run_one(instance, pair, seed, iterations, cfg)
                            cumulative_cpu += record['solver_cpu_seconds']
                        count += 1
                        record.update({'stage': stage, 'run_index': count, 'instance_index': entry['index'],
                                       'replicate': entry['replicate'], 'search_index': si,
                                       'cumulative_solver_cpu_seconds': cumulative_cpu,
                                       'finished_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
                        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
                        handle.flush()
                        os.fsync(handle.fileno())
                        group.append(record)
                summary = {'stage': stage, 'instance_index': entry['index'], 'runs': count,
                           'cumulative_solver_cpu_seconds': round(cumulative_cpu, 4),
                           'feasible': sum(r.get('check', {}).get('accepted', False) for r in group),
                           'pair_mean_improvement_pct': {p: round(100 * sum(r['improvement_fraction'] for r in group if r['pair'] == p)/len(seeds), 4)
                            for p in ['_'.join(p) for p in cfg['pairs']]}}
                print(json.dumps(summary), flush=True)
        dump_json(ROOT / 'results' / (stage + '_completion.json'), {'stage': stage, 'run_count': count,
                  'cumulative_solver_cpu_seconds': cumulative_cpu,
                  'completed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})


if __name__ == '__main__':
    main()
