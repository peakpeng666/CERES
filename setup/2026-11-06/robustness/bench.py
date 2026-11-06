#!/usr/bin/env python3
"""How the plans of the coordination benchmark hold up under seeded physical perturbations, in MuJoCo.

``plans`` solves the cases of ../coordination/bench.py for makespan and freezes the plans. ``run`` executes the studies on
the frozen plans, appends one line per run and continues where it stopped. Only local CPU is used; nothing here starts ROS.

Studies (every run of one seed index meets the same draws, whatever the plan or the intensity):
  ideal      every plan once at intensity 0
  ranking    every plan, all at once, all factors, intensity 1 / 1.5, and 0.5 on the three smallest cases
  friction   best plans, both traffic modes, ground friction fixed on a grid
  factor     best plans, all at once, one factor at a time
  intensity  best plans, all at once, all factors, intensity 1.25 / 1.75 / 2
  serial     best plans, one at a time, all factors: failures that have nothing to do with the passing order
  regret     best plans, all at once, the order compiled again from the drawn working times
  load       best plans of the small cases, fixed load, ideal servo against bounded wheel torque

Usage: taskset -c 8-23 src/lab/.venv/bin/python setup/2026-11-06/robustness/bench.py plans --out /new/absolute/dir
       taskset -c 8-23 src/lab/.venv/bin/python setup/2026-11-06/robustness/bench.py run --out /same/dir [--workers 16] [--studies a,b] [--cases a,b]
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import multiprocessing
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault('OMP_NUM_THREADS', '1')        # one run is one thread; before NumPy starts its pool
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src' / 'lab'))

ALGORITHMS = ('pyvrp.fleet', 'ortools.fleet', 'pyvrp.balanced')
SEEDS = {'free-2': 6, 'rows-2': 6, 'pond-3': 6, 'field-6': 6, 'orchard-3': 3, 'wheat-4': 3}     # the two slowest cases take half the seeds
FIRST_SEED = 42
RANKING_LEVELS = {'free-2': (.5, 1., 1.5), 'rows-2': (.5, 1., 1.5), 'pond-3': (.5, 1., 1.5), 'field-6': (1., 1.5), 'orchard-3': (1., 1.5), 'wheat-4': (1., 1.5)}
FACTOR_CASES = ('free-2', 'rows-2', 'pond-3', 'field-6')      # field-6 has the most meetings; the two longest cases would double the cost
FACTOR_SEEDS = 3
LOAD_CASES = ('free-2', 'rows-2', 'pond-3')
DRAWS = 16
BATCH_SEED = 2026
FRICTION_GRID = (.5, .4, .3, .25, .2, .18, .16, .14, .12, .1, .08, .06, .05)
LOADS_KG = (0., 10., 20., 30., 40.)
COST = {'free-2': 1, 'rows-2': 1, 'pond-3': 2, 'field-6': 9, 'orchard-3': 12, 'wheat-4': 22}    # relative run time, for packing
STUDIES = ('ideal', 'ranking', 'friction', 'factor', 'intensity', 'serial', 'regret', 'load')
CODE = ('src/lab/urp_lab/mujoco_field.py', 'src/lab/urp_lab/field_traffic.py', 'src/lab/urp_lab/field_coordination.py', 'src/lab/urp_lab/field_tracking.py',
        'src/lab/urp_lab/physics_stats.py', 'src/lab/urp_lab/physics_worker.py', 'src/lab/urp_lab/fieldcheck.py', 'src/lab/urp_lab/field_physics.py')
OUT: Path = Path('.')


def coordination_cases() -> dict:
    spec = importlib.util.spec_from_file_location('coordination_bench', ROOT / 'setup/2026-11-06/coordination/bench.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CASES


def freeze(job):
    """One solver record as a plan on disk: what ``physics.plan_from_run`` gives the engine, with its compiled order."""
    name, text, seed, algorithm = job
    from urp_lab import compose, field_coordination, fieldworld
    from urp_lab.scenarios import validate_instance
    from urp_lab.solvers import fleet
    key = f'{name}-{seed}-{algorithm}'
    row = {'key': key, 'case': name, 'seed': seed, 'algorithm': algorithm}
    try:
        scenario = compose.compose(text, seed)
        inst = validate_instance('fleet_routing', scenario['instance'])
        inst['objective'] = 'makespan'
        routes, check, metrics, *_ = fleet.solve(scenario, inst, algorithm, {'time_limit_s': 2, 'seed': seed})
        if not check['valid']:
            return {**row, 'state': 'no_valid_plan', 'errors': check['errors'][:2]}
        with tempfile.TemporaryDirectory() as tmp:
            world = fieldworld.build(inst, routes, Path(tmp), 'robustness', 4.0, None, None, inherit_ground=False)
        for v, r in zip(world['vehicles'], routes):
            v['planned_length_m'], v['planned_duration_s'] = r.get('cost'), r.get('duration_s')
        plan = {**world, 'run_id': key, 'scenario_id': f'{name}-{seed}', 'algorithm': algorithm,
                'planned': {'makespan_s': max(r['duration_s'] for r in routes), 'total_distance_m': check['computed_cost']}}
        coordinated = field_coordination.apply(plan)
        traffic = coordinated['traffic']
        estimate = field_coordination.estimate(inst, routes) or {}
        with gzip.open(OUT / 'plans' / f'{key}.json.gz', 'wt', encoding='utf8') as f:
            json.dump({**plan, 'coordinated_traffic': traffic}, f, ensure_ascii=False)
        stops = [len(v['tasks']) for v in plan['vehicles']]
        return {**row, 'state': 'ok', 'vehicles': len(plan['vehicles']), 'tasks': len(inst['tasks']), 'travel': plan['travel'], 'stops': stops,
                'predicted_s': estimate.get('predicted_makespan_s'), 'compiled_predicted_s': traffic.get('predicted_makespan_s'), 'planned_s': plan['planned']['makespan_s'],
                'total_distance_m': check['computed_cost'], 'meetings': traffic.get('meetings'), 'boxes': traffic.get('boxes'), 'refused': traffic.get('coordination_refused'),
                'gate_count': sum(len(g) for g in (traffic.get('gates') or {}).values()), 'stop_m': traffic.get('stop_m'), 'clearance_m': traffic.get('clearance_m'),
                'routes_sha256': hashlib.sha256(json.dumps([v['path'] for v in plan['vehicles']]).encode()).hexdigest()}
    except Exception as exc:  # noqa: BLE001 - a failed case is a result
        return {**row, 'state': 'error', 'error': f'{type(exc).__name__}: {exc}'[:300]}


def plans_stage(workers: int):
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / 'plans').mkdir()
    cases = coordination_cases()
    jobs = [(c, cases[c], FIRST_SEED + s, a) for c, seeds in SEEDS.items() for s in range(seeds) for a in ALGORITHMS]
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(freeze, jobs))
    seen: dict = {}
    for row in rows:
        if row['state'] == 'ok':
            same = seen.setdefault((row['case'], row['seed'], row['routes_sha256']), row['key'])
            row['duplicate_of'] = None if same == row['key'] else same     # two algorithms can return one plan; it runs once
        print(row['key'], row['state'], row.get('vehicles'), 'pred', row.get('predicted_s'), 'meetings', row.get('meetings'), row.get('refused') or row.get('error') or '', flush=True)
    (OUT / 'plans.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf8')


def scenarios(index: list[dict]) -> dict:
    """Runnable, distinct plans of each scenario, in the order the workbench ranks them."""
    groups: dict = {}
    for row in index:
        if row['state'] == 'ok' and not row['duplicate_of'] and not row['refused'] and row['predicted_s'] is not None:
            groups.setdefault((row['case'], row['seed']), []).append(row)
    return {k: sorted(v, key=lambda r: (r['predicted_s'], r['key'])) for k, v in groups.items()}


def seed_of(index: int) -> int:
    from urp_lab.physics import sample_seed
    return sample_seed(BATCH_SEED, index)


def job_list(index: list[dict], chosen: tuple[str, ...], cases: set[str]) -> list[dict]:
    from urp_lab.mujoco_field import FACTORS
    groups = {k: v for k, v in scenarios(index).items() if k[0] in cases}
    every = [p for plans in groups.values() for p in plans]
    best = [plans[0] for (case, seed), plans in groups.items() if case in FACTOR_CASES and seed < FIRST_SEED + FACTOR_SEEDS]
    small = [p for p in best if p['case'] in LOAD_CASES]
    all_factors = list(FACTORS)

    def draws(study, plan, traffic, factors, intensity, **more):
        return [{'study': study, 'plan': plan['key'], 'case': plan['case'], 'traffic': traffic, 'factors': factors, 'intensity': intensity, 'index': i, 'seed': seed_of(i), **more}
                for i in range(DRAWS)]

    def one(study, plan, traffic, **more):
        return {'study': study, 'plan': plan['key'], 'case': plan['case'], 'traffic': traffic, 'factors': [], 'intensity': 0., 'index': 0, 'seed': seed_of(0), **more}

    jobs: dict[str, list[dict]] = {s: [] for s in STUDIES}
    jobs['ideal'] = [one('ideal', p, 'coordinated') for p in every] + [one('ideal', p, 'serial') for p in best]
    jobs['ranking'] = [j for p in every for k in RANKING_LEVELS[p['case']] for j in draws('ranking', p, 'coordinated', all_factors, k)]
    jobs['friction'] = [one('friction', p, t, friction=mu) for p in best for t in ('coordinated', 'serial') for mu in FRICTION_GRID]
    jobs['factor'] = [j for p in best for f, levels in (('ground_friction', (1., 1.5, 2.)), ('payload', (1., 2.)), ('wheel_radius', (1., 2.)), ('service_time', (1., 2.)))
                      for k in levels for j in draws('factor', p, 'coordinated', [f], k)]
    jobs['intensity'] = [j for p in best for k in (1.25, 1.75, 2.) for j in draws('intensity', p, 'coordinated', all_factors, k)]
    jobs['serial'] = [j for p in best for k in (1., 1.5, 2.) for j in draws('serial', p, 'serial', all_factors, k)]
    jobs['regret'] = [j for p in best for k in (1., 2.) for j in draws('regret', p, 'coordinated', all_factors, k)]
    jobs['load'] = [one('load', p, 'coordinated', load_kg=load, drive=drive) for p in small for drive in ('velocity_servo', 'torque_limited') for load in LOADS_KG]
    ordered: list[dict] = []
    for study in chosen:          # the slowest first inside a study, so the last jobs do not leave cores idle
        ordered += sorted(jobs[study], key=lambda j: (-COST[j['case']], j['plan']))
    return ordered


def job_id(job: dict) -> str:
    return '|'.join(str(job.get(k)) for k in ('study', 'plan', 'traffic', 'factors', 'intensity', 'index', 'friction', 'load_kg', 'drive'))


_plans: dict = {}


def frozen(key: str, traffic: str) -> dict:
    if key not in _plans:
        if len(_plans) > 6:
            _plans.clear()
        with gzip.open(OUT / 'plans' / f'{key}.json.gz', 'rt', encoding='utf8') as f:
            _plans[key] = json.load(f)
    stored = _plans[key]
    plan = {k: v for k, v in stored.items() if k != 'coordinated_traffic'}
    return {**plan, 'traffic': stored['coordinated_traffic']} if traffic == 'coordinated' else plan


def execute_fixed(plan: dict) -> dict:
    from urp_lab import mujoco_field, physics_stats, physics_worker
    result = mujoco_field.execute(plan)
    result['check'] = physics_worker.check(plan, result)
    return physics_stats.sample_row(plan, result)


def loaded(plan: dict, load_kg: float, drive: str) -> dict:
    from urp_lab.field_physics import box_inertia
    physics = {**plan['physics'], 'actuator_mode': drive}
    body = physics['body']
    mass = body['mass_kg'] + load_kg
    actual = {'body': {**body, 'mass_kg': mass, 'inertia_kgm2': box_inertia(mass, body['size_m'])}, 'total_mass_kg': physics['total_mass_kg'] + load_kg}
    return {**plan, 'physics': physics, 'vehicles': [{**v, 'actual': actual} for v in plan['vehicles']]}


def gates_changed(before: dict, after: dict) -> int:
    """Gates of the fixed order that the order compiled from the drawn times no longer has."""
    def flat(traffic):
        return {(name, g['s'], tuple(map(tuple, g['after']))) for name, gates in (traffic.get('gates') or {}).items() for g in gates}
    return len(flat(before) ^ flat(after))


def work(job: dict) -> dict:
    from urp_lab import field_coordination, mujoco_field, physics_worker
    started = time.monotonic()
    try:
        plan = frozen(job['plan'], job['traffic'])
        extra = {}
        if job['study'] == 'friction':
            row = execute_fixed({**plan, 'physics': {**plan['physics'], 'ground_friction': job['friction']}})
        elif job['study'] == 'load':
            row = execute_fixed(loaded(plan, job['load_kg'], job['drive']))
        elif job['study'] == 'regret':
            actual = mujoco_field.perturb(plan, job['seed'], job['intensity'], job['factors'])
            reordered = field_coordination.apply(actual)
            if reordered['traffic'].get('coordination_refused'):
                return {**job, 'state': 'refused', 'valid': False, 'reason': 'no_order', 'errors': [reordered['traffic']['coordination_refused']]}
            extra = {'gates_changed': gates_changed(plan['traffic'], reordered['traffic']), 'gates_total': sum(len(g) for g in (plan['traffic'].get('gates') or {}).values()),
                     'reordered_predicted_s': reordered['traffic'].get('predicted_makespan_s')}
            row = execute_fixed(reordered)
        elif job['factors']:
            row = physics_worker.sample(plan, job['seed'], job['intensity'], job['factors'])
        else:
            row = execute_fixed(plan)
        drawn = row.pop('perturbation', None) or {}
        row.pop('model_sha256', None)
        vehicles = list((drawn.get('vehicles') or {}).values())
        radius = plan['physics']['wheel']['radius_m']
        row['errors'] = (row.get('errors') or [])[:2]
        return {**job, **row, **extra, 'friction': job.get('friction', drawn.get('ground_friction', plan['physics']['ground_friction'])),
                'payload_mean_kg': round(sum(v['payload_kg'] for v in vehicles) / len(vehicles), 3) if vehicles else 0.,
                'wheel_error_max': round(max((abs(r / radius - 1) for v in vehicles for r in v['wheel_radius_m'].values()), default=0.), 5),
                'service_total_s': round(sum((drawn.get('service_s') or {}).values()), 2)}
    except Exception as exc:  # noqa: BLE001 - a failed run is a result, and the study goes on
        return {**job, 'state': 'error', 'valid': False, 'reason': 'harness', 'errors': [f'{type(exc).__name__}: {exc}'[:300]], 'wall_s': round(time.monotonic() - started, 1)}


def manifest(workers: int, chosen: tuple[str, ...]) -> dict:
    import platform
    import subprocess
    import mujoco
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {'base_commit': head, 'code_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in CODE}, 'python': platform.python_version(), 'mujoco': mujoco.__version__,
            'host_cpus': sorted(os.sched_getaffinity(0)), 'workers': workers, 'studies': list(chosen), 'batch_seed': BATCH_SEED, 'draws': DRAWS, 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def run_stage(workers: int, chosen: tuple[str, ...], cases: set[str]):
    # Everything a run needs is imported here, before the pool forks, so an edit to the sources cannot reach a running study.
    from urp_lab import field_coordination, fieldcheck, mujoco_field, physics, physics_stats, physics_worker  # noqa: F401
    import mujoco  # noqa: F401
    index = json.loads((OUT / 'plans.json').read_text(encoding='utf8'))
    jobs = job_list(index, chosen, cases)
    path = OUT / 'runs.jsonl'
    done = set()
    if path.exists():
        done = {job_id(json.loads(line)) for line in path.read_text(encoding='utf8').splitlines() if line.strip()}
    todo = [j for j in jobs if job_id(j) not in done]
    (OUT / f"manifest-{time.strftime('%Y%m%dT%H%M%S')}.json").write_text(json.dumps({**manifest(workers, chosen), 'jobs': len(jobs), 'jobs_left': len(todo)}, ensure_ascii=False, indent=1), encoding='utf8')
    print(f'{len(jobs)} jobs, {len(todo)} left', flush=True)
    started, count = time.monotonic(), 0
    with path.open('a', encoding='utf8') as out, ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context('fork')) as pool:
        for future in as_completed([pool.submit(work, j) for j in todo]):
            row = future.result()
            out.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            out.flush()
            count += 1
            if count % 200 == 0 or count == len(todo):
                print(f'{count}/{len(todo)} {time.monotonic() - started:.0f} s', row['study'], row['plan'], row.get('intensity'), row.get('state'), flush=True)


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('stage', choices=('plans', 'run'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=len(os.sched_getaffinity(0)))
    parser.add_argument('--studies', default=','.join(STUDIES))
    parser.add_argument('--cases', default=','.join(SEEDS))
    args = parser.parse_args()
    OUT = args.out
    if args.stage == 'plans':
        plans_stage(args.workers)
    else:
        run_stage(args.workers, tuple(args.studies.split(',')), set(args.cases.split(',')))


if __name__ == '__main__':
    sys.exit(main())
