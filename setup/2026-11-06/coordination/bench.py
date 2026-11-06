#!/usr/bin/env python3
"""Serial against coordinated execution of the same frozen plans, in MuJoCo.

Each case is one sentence through the workbench's map generator and the fleet solvers. The plan is then executed twice with
identical physics: one vehicle at a time, and all at once through the temporal plan graph. Every run goes through the
same independent checks as a Gazebo record. Only local CPU is used; nothing here starts ROS.

With --objective makespan --algorithms a,b --no-serial the same harness compares solvers by measured coordinated time.

Usage: src/lab/.venv/bin/python setup/2026-11-06/coordination/bench.py --out /new/absolute/dir [--seeds 3] [--jobs 8]
"""
import argparse
import json
import statistics
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src' / 'lab'))

CASES = {
    'free-2': '在 70×50 米的菜地里，2 台车访问 8 个点，单车容量 6',
    'rows-2': '在 60×40 米的果园里，2 台车访问 6 个采样点，单车容量 4',
    'pond-3': '在 90×60 米的果园里有一个池塘，3 台车访问 9 个点，单车容量 4',
    'orchard-3': '在 200×120 米的果园里，3 台机器人给 40 个点采样，每台最多装 10 个样本，避开两个池塘和一栋房子',
    'wheat-4': '5 公顷麦田分成 6 块，4 辆无人车巡检 60 个巡检点，有 3 棵大树和一条水渠',
    'field-6': '在 150×100 米的菜地里，6 台车访问 36 个点，单车容量 8',
}
ALGORITHM = 'pyvrp.fleet'


def one(job):
    name, text, seed, algorithm, objective, serial = job
    from urp_lab import compose, field_coordination, fieldcheck, fieldworld, mujoco_field
    from urp_lab.field_traffic import separation
    from urp_lab.scenarios import validate_instance
    from urp_lab.solvers import fleet
    row = {'case': name, 'seed': seed, 'algorithm': algorithm}
    try:
        scenario = compose.compose(text, seed)
        inst = validate_instance('fleet_routing', scenario['instance'])
        if objective:
            inst['objective'] = objective
        routes, check, metrics, *_ = fleet.solve(scenario, inst, algorithm, {'time_limit_s': 2, 'seed': seed})
        row.update(total_distance_m=metrics.get('total_distance_m'), constant_speed_makespan_s=metrics.get('makespan_s'))
        if not check['valid']:
            return {**row, 'state': 'no_valid_plan', 'errors': check['errors'][:2]}
        with tempfile.TemporaryDirectory() as tmp:
            world = fieldworld.build(inst, routes, Path(tmp), 'bench', 4.0, None, None, inherit_ground=False)
        for v, r in zip(world['vehicles'], routes):
            v['planned_length_m'], v['planned_duration_s'] = r.get('cost'), r.get('duration_s')
        plan = {**world, 'run_id': f'{name}-{seed}', 'planned': {'makespan_s': max(r['duration_s'] for r in routes), 'total_distance_m': check['computed_cost']}}
        row.update(vehicles=len(plan['vehicles']), tasks=len(inst['tasks']), travel=plan['travel'], planned_s=plan['planned']['makespan_s'])
        coordinated = field_coordination.apply(plan)
        traffic = coordinated['traffic']
        row.update(refused=traffic.get('coordination_refused'), meetings=traffic.get('meetings'), boxes=traffic.get('boxes'),
                   predicted_s=traffic.get('predicted_makespan_s'), compile_s=traffic.get('compile_s'))
        for label, p in (('serial', plan), ('coordinated', coordinated)):
            if (label == 'coordinated' and row['refused']) or (label == 'serial' and not serial):
                continue
            t = time.monotonic()
            r = mujoco_field.execute(p)
            errors, _ = fieldcheck.track_check(p, r)
            gap = None
            if len(p['vehicles']) > 1 and r['state'] == 'succeeded':
                s = separation({v['name']: r['trails'][v['name']] for v in p['vehicles']}, p['robot_radius_m'])
                gap = min(x['min_footprint_gap_m'] for x in s['pairs'])
                if not s['valid']:
                    errors.append('车距')
            m = r['metrics']
            row[label] = {'state': r['state'], 'valid': not errors and not r['contacts'] and r['state'] == 'succeeded', 'errors': (r['errors'] + errors)[:3],
                          'makespan_s': m['makespan_s'], 'distance_m': m['total_distance_m'], 'energy_wh': m['energy_wh'], 'cross_track_max_m': m['cross_track_max_m'],
                          'min_gap_m': gap, 'contacts': len(r['contacts']), 'wall_s': round(time.monotonic() - t, 1),
                          'holds': sum(e['event'] == 'hold' for e in r['traffic']['events']), 'held_s': r['traffic'].get('held_s')}
        return {**row, 'state': 'ok'}
    except Exception as exc:  # noqa: BLE001 - a failed case is a result
        return {**row, 'state': 'error', 'error': f'{type(exc).__name__}: {exc}'[:300]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seeds', type=int, default=3)
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--cases', default=','.join(CASES))
    parser.add_argument('--algorithms', default=ALGORITHM)
    parser.add_argument('--objective')
    parser.add_argument('--no-serial', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    jobs = [(c, CASES[c], 42 + s, a, args.objective, not args.no_serial) for c in args.cases.split(',') for s in range(args.seeds) for a in args.algorithms.split(',')]
    rows = []
    with ProcessPoolExecutor(args.jobs) as pool:
        for row in pool.map(one, jobs):
            rows.append(row)
            a, b = row.get('serial') or {}, row.get('coordinated') or {}
            print(row['case'], row['seed'], row['algorithm'], row['state'], row.get('vehicles'), row.get('travel'), 'plan', row.get('planned_s'), 'pred', row.get('predicted_s'),
                  'serial', a.get('makespan_s'), a.get('valid'), 'coord', b.get('makespan_s'), b.get('valid'), 'gap', b.get('min_gap_m'), 'holds', b.get('holds'),
                  row.get('refused') or row.get('error') or (b.get('errors') or ''), flush=True)
    good = [r for r in rows if (r.get('coordinated') or {}).get('valid')]
    timed = [r for r in good if (r.get('serial') or {}).get('makespan_s')]
    summary = {'cases': len(rows), 'coordinated_valid': len(good), 'serial_valid': sum(bool((r.get('serial') or {}).get('valid')) for r in rows),
               'speedup_median': statistics.median(r['serial']['makespan_s'] / r['coordinated']['makespan_s'] for r in timed) if timed else None,
               'min_gap_m': min(r['coordinated']['min_gap_m'] for r in good) if good else None}
    (args.out / 'rows.json').write_text(json.dumps({'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
