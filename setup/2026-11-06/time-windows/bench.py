#!/usr/bin/env python3
"""Deadlines against recorded arrivals: is a plan the checker calls on time really on time in MuJoCo?

Each case is one sentence through the workbench's map generator. A first solve with no windows gives that plan's latest
arrival; every task then gets the same window at a multiple of it, so each case has a loose, a tight and an impossible
deadline whatever its size. A plan that passes the checker is executed with all vehicles moving at once, and its recorded
arrivals are judged against the same windows. Only local CPU is used; nothing here starts ROS.

Usage: src/lab/.venv/bin/python setup/2026-11-06/time-windows/bench.py --out /new/absolute/dir [--seeds 3] [--jobs 6]
"""
import argparse
import json
import statistics
import sys
import tempfile
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src' / 'lab'))

CASES = {
    'free-2': '在 70×50 米的菜地里，2 台车访问 8 个点，单车容量 6',
    'rows-2': '在 60×40 米的果园里，2 台车访问 6 个采样点，单车容量 4',
    'pond-3': '在 90×60 米的果园里有一个池塘，3 台车访问 9 个点，单车容量 4',
    'orchard-3': '在 200×120 米的果园里，3 台机器人给 40 个点采样，每台最多装 10 个样本，避开两个池塘和一栋房子',
    'field-6': '在 150×100 米的菜地里，6 台车访问 36 个点，单车容量 8',
}
# (name, earliest as a share of the unconstrained latest arrival, deadline as a share of it)
WINDOWS = [('loose', 0, 1.3), ('as_planned', 0, 1.0), ('tight', 0, .8), ('very_tight', 0, .6), ('impossible', 0, .25), ('earliest', .25, 1.3)]
ALGORITHMS = ('pyvrp.balanced', 'ortools.fleet')


def one(job):
    name, text, seed, algorithm, (label, early, late) = job
    warnings.simplefilter('ignore')
    from urp_lab import compose, field_coordination, fieldcheck, mujoco_field, physics, time_windows
    from urp_lab.scenarios import validate_instance
    from urp_lab.solvers import fleet
    row = {'case': name, 'seed': seed, 'algorithm': algorithm, 'window': label}
    try:
        scenario = compose.compose(text, seed)
        base = validate_instance('fleet_routing', scenario['instance'])
        params = {'time_limit_s': 2, 'seed': seed}
        routes = fleet.solve(scenario, base, 'pyvrp.balanced', params)[0]
        latest = max(s['arrive_s'] for v in field_coordination.arrivals(base, routes)['vehicles'] for s in v['stops'])
        window = [round(latest * early, 1), round(latest * late, 1)]
        inst = validate_instance('fleet_routing', {**base, 'tasks': [{**t, 'time_window': window} for t in base['tasks']]})
        begun = time.monotonic()
        routes, check, metrics, raw, status, _ = fleet.solve(scenario, inst, algorithm, params)
        row.update(unconstrained_latest_s=latest, time_window=window, tasks=len(inst['tasks']), vehicles=inst['vehicles']['count'], status=status,
                   solve_s=round(time.monotonic() - begun, 2), rounds=raw.get('time_window_rounds'), reason=raw.get('reason'))
        plan_record = (check or {}).get('time_windows') or {}
        row['plan'] = {k: plan_record.get(k) for k in ('model', 'total', 'on_time', 'all_on_time', 'worst_slack_s')}
        row['plan']['late'] = [r['label'] for r in plan_record.get('late') or []]
        if status != 'feasible':
            return {**row, 'state': 'no_plan'}
        run = {'problem_class': 'fleet_routing', 'state': 'done', 'status': status, 'routes': routes, 'check': check, 'instance': inst, 'params': params,
               'run_id': f'{name}-{seed}', 'scenario_id': scenario['id'], 'algorithm': {'name': algorithm}}
        with tempfile.TemporaryDirectory() as tmp:
            plan = physics.plan_from_run(run, Path(tmp))
        plan = field_coordination.apply(plan)
        if plan['traffic'].get('coordination_refused'):
            return {**row, 'state': 'refused', 'refused': plan['traffic']['coordination_refused']}
        result = mujoco_field.execute(plan)
        errors, _ = fieldcheck.track_check(plan, result)
        recorded = time_windows.execution(plan, result)
        gaps = [r['arrival_s'] - r['planned_arrival_s'] for r in recorded['tasks'] if r['arrival_s'] is not None and 'planned_arrival_s' in r]
        row['recorded'] = {'state': result['state'], 'valid': not errors and not result['contacts'] and result['state'] == 'succeeded', 'errors': (result['errors'] + errors)[:3],
                           'total': recorded['total'], 'on_time': recorded['on_time'], 'all_on_time': recorded['all_on_time'], 'worst_slack_s': recorded['worst_slack_s'],
                           'late': [{'task': r['label'], 'by_s': -r['slack_s'] if r['slack_s'] is not None else None} for r in recorded['late']], 'early_starts': recorded['early_starts'],
                           'arrival_gap_mean_s': round(statistics.fmean(gaps), 2) if gaps else None, 'arrival_gap_max_s': round(max(gaps), 2) if gaps else None,
                           'arrival_gap_min_s': round(min(gaps), 2) if gaps else None}
        return {**row, 'state': 'ok'}
    except Exception as exc:  # noqa: BLE001 - a failed case is a result
        return {**row, 'state': 'error', 'error': f'{type(exc).__name__}: {exc}'[:300]}


def summarize(rows):
    out = {}
    for label, *_ in WINDOWS:
        for algorithm in ALGORITHMS:
            mine = [r for r in rows if r['window'] == label and r['algorithm'] == algorithm]
            ran = [r for r in mine if (r.get('recorded') or {}).get('valid')]
            gaps = [r['recorded']['arrival_gap_max_s'] for r in ran if r['recorded']['arrival_gap_max_s'] is not None]
            out[f'{label}/{algorithm}'] = {
                'cases': len(mine), 'feasible': sum(r.get('status') == 'feasible' for r in mine), 'infeasible_proven': sum(r.get('status') == 'infeasible' for r in mine),
                'no_solution_tasks_named': sum(r.get('status') == 'no_solution' and bool(r['plan']['late']) for r in mine), 'errors': sum(r['state'] == 'error' for r in mine),
                'executed_clean': len(ran), 'recorded_all_on_time': sum(r['recorded']['all_on_time'] for r in ran), 'early_starts': sum(r['recorded']['early_starts'] for r in ran),
                'arrival_gap_max_s': max(gaps) if gaps else None, 'worst_recorded_slack_s': min((r['recorded']['worst_slack_s'] for r in ran), default=None)}
    ran = [r for r in rows if (r.get('recorded') or {}).get('valid')]
    out['all'] = {'cases': len(rows), 'plans_on_time': sum(r.get('status') == 'feasible' for r in rows), 'executed_clean': len(ran),
                  'recorded_all_on_time': sum(r['recorded']['all_on_time'] for r in ran), 'recorded_late_plans': [f"{r['case']}/{r['seed']}/{r['algorithm']}/{r['window']}" for r in ran if not r['recorded']['all_on_time']],
                  'arrival_gap_mean_s': round(statistics.fmean(r['recorded']['arrival_gap_mean_s'] for r in ran if r['recorded']['arrival_gap_mean_s'] is not None), 2) if ran else None,
                  'arrival_gap_max_s': max((r['recorded']['arrival_gap_max_s'] for r in ran if r['recorded']['arrival_gap_max_s'] is not None), default=None),
                  'errors': [r for r in rows if r['state'] == 'error']}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seeds', type=int, default=3)
    parser.add_argument('--jobs', type=int, default=6)
    parser.add_argument('--cases', default=','.join(CASES))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    jobs = [(c, CASES[c], 42 + s, a, w) for c in args.cases.split(',') for s in range(args.seeds) for w in WINDOWS for a in ALGORITHMS]
    rows = []
    with ProcessPoolExecutor(args.jobs) as pool:
        for row in pool.map(one, jobs):
            rows.append(row)
            r = row.get('recorded') or {}
            print(row['case'], row['seed'], row['algorithm'], row['window'], row['state'], row.get('status'), 'plan', row.get('plan', {}).get('on_time'), '/', row.get('plan', {}).get('total'),
                  'recorded', r.get('on_time'), 'gap', r.get('arrival_gap_max_s'), row.get('error') or (r.get('errors') or ''), flush=True)
    summary = summarize(rows)
    (args.out / 'rows.json').write_text(json.dumps({'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf8')
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    sys.exit(main())
