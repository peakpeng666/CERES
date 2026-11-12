"""Offline portfolio sweep: a frozen plan of (instance, algorithm configuration) jobs, run with process isolation.

Every job runs in a long-lived worker pinned to one CPU core (single-threaded BLAS/OpenMP). The supervisor
assigns one job at a time per worker, enforces a per-job wall-clock cap by killing the worker, and records a
terminal state for every planned job: ``feasible`` / ``no_solution`` (solver returned, independent check decided)
/ ``error`` (exception) / ``timeout`` / ``worker_crash``. Jobs are never retried to pick a better outcome; a
resumed sweep only fills jobs without a record. The plan file is hashed before execution and a resume refuses
a changed plan.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import platform
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .. import paths

THREAD_ENV = {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'NUMEXPR_NUM_THREADS': '1'}
COVERAGE_METRICS = ('cost', 'length', 'working_area_fraction', 'coverage_fraction', 'working_area_m2', 'efficiency',
                    'n_swaths', 'n_cells', 'working_length_m', 'turning_length_m', 'headland_length_m',
                    'non_working_length_m', 'reverse_states', 'footprint_outside_m2', 'max_sampled_curvature_per_m',
                    'repairs', 'repair_attempts', 'unfixable_targets', 'rounded', 'rounding_ok',
                    'planning_s', 'wall_s', 'cpu_s')
FLEET_METRICS = ('cost', 'length', 'n_routes', 'wall_s', 'cpu_s', 'makespan_s', 'total_distance_m', 'trips')


def utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_identity() -> dict:
    """Git commit plus hashes of this package's sources (uncommitted edits are visible through the hashes)."""
    root = paths.REPO
    try:
        commit = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--', 'src/lab'],
                                    capture_output=True, text=True, timeout=20).stdout.strip())
    except Exception:  # noqa: BLE001 - provenance is best effort; the source hashes below still bind the code
        commit, dirty = None, None
    sources = {p.name: sha256_file(p) for p in sorted((paths.LAB / 'ceres_lab' / 'evolve').glob('*.py'))}
    for rel in ('ceres_lab/solvers/__init__.py', 'ceres_lab/solvers/f2c_coverage.py', 'ceres_lab/solvers/fleet.py',
                'ceres_lab/solvers/fleet_engines.py', 'ceres_lab/library/fields.py', 'ceres_lab/compose/generate.py'):
        sources[rel] = sha256_file(paths.LAB / rel)
    return {'git_commit': commit, 'src_lab_dirty': dirty, 'source_sha256': sources}


def environment() -> dict:
    import importlib.metadata as md
    versions = {}
    for dist in ('pyvrp', 'ortools', 'shapely', 'numpy', 'scipy', 'fields2cover', 'pyproj'):
        try:
            versions[dist] = md.version(dist)
        except md.PackageNotFoundError:
            versions[dist] = None
    from ..solvers import f2c_coverage
    return {'python': sys.version.split()[0], 'platform': platform.platform(), 'machine': platform.machine(),
            'cpu_count': os.cpu_count(), 'packages': versions, 'fields2cover': f2c_coverage.available(),
            'thread_env': THREAD_ENV, 'recorded_utc': utc()}


def write_plan(out: Path, plan: dict) -> str:
    out.mkdir(parents=True, exist_ok=False)
    text = json.dumps(plan, ensure_ascii=False, indent=1, sort_keys=True)
    (out / 'plan.json').write_text(text, encoding='utf-8')
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    (out / 'plan.sha256').write_text(digest + '\n', encoding='ascii')
    return digest


# ------------------------------------------------------------------ worker side

_CACHE: dict = {}


def build(job: dict):
    from . import instances as I
    key = json.dumps(job['instance'], sort_keys=True)
    if key not in _CACHE:
        if len(_CACHE) > 64:
            _CACHE.clear()
        spec = job['instance']
        if job['family'] == 'coverage':
            _CACHE[key] = I.coverage_instance(spec['field'], spec['profile'])
        elif job['family'] == 'fleet':
            _CACHE[key] = I.fleet_instance(spec['field'], spec['n_tasks'], spec['distribution'], spec['travel'],
                                           spec['objective'], spec['seed'], spec['multi_trip'])
        else:
            raise ValueError(f"unknown family {job['family']}")
    return _CACHE[key]


def _hash_polylines(routes) -> str | None:
    if not routes:
        return None
    return hashlib.sha256(json.dumps([r.get('polyline') for r in routes], separators=(',', ':')).encode()).hexdigest()


def solve(scenario, inst, algorithm: str, params: dict) -> dict:
    """Dispatch one solve. Registered catalog algorithms go through ``run_algorithm``; ``evolve.*`` research
    candidates execute here and are wrapped into the same result envelope — their validity is decided only by
    the unchanged independent checker inside the candidate itself."""
    if algorithm in ('evolve.f2c_split', 'evolve.f2c_repair_w', 'evolve.f2c_fillet'):
        from . import candidates
        return candidates.solve(algorithm, inst, params)
    if algorithm == 'evolve.f2c_repair':
        from . import repair
        routes, check, metrics, raw = repair.solve_full_repaired(inst, params)
        return {'status': 'feasible' if routes else 'no_solution',
                'objective': {'value': sum(r['cost'] for r in routes) if routes else None, 'unit': 'm'},
                'routes': routes, 'check': check, 'metrics': metrics, 'raw_result': raw,
                'algorithm': {'id': 'evolve.f2c_repair', 'name': '覆盖全流程+局部 Dubins 修补（研究候选）',
                              'version': f"evolve.f2c_repair+{raw.get('binding') or 'local'}"}}
    if algorithm == 'evolve.f2c_repair2':
        from . import repair2
        routes, check, metrics, raw = repair2.solve_full_repaired2(inst, params)
        return {'status': 'feasible' if routes else 'no_solution',
                'objective': {'value': sum(r['cost'] for r in routes) if routes else None, 'unit': 'm'},
                'routes': routes, 'check': check, 'metrics': metrics, 'raw_result': raw,
                'algorithm': {'id': 'evolve.f2c_repair2', 'name': '覆盖全流程+修补v2（研究候选）',
                              'version': f"evolve.f2c_repair2+{raw.get('binding') or 'local'}"}}
    from ..solvers import run_algorithm
    return run_algorithm(scenario, inst, algorithm, params)


def execute(job: dict) -> dict:
    """Run one job in this process and return its record (never raises)."""
    t0, c0 = time.perf_counter(), time.process_time()
    rec = {'job_id': job['job_id'], 'family': job['family'], 'config': job['config'], 'algorithm': job['algorithm'],
           'params': job['params'], 'instance': job['instance'], 'started_utc': utc(), 'pid': os.getpid(),
           'affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None}
    try:
        scenario, inst = build(job)
        result = solve(scenario, inst, job['algorithm'], dict(job['params']))
        metrics = result.get('metrics') or {}
        keep = COVERAGE_METRICS if job['family'] == 'coverage' else FLEET_METRICS
        check = result.get('check') or {}
        rec.update({'status': result['status'], 'objective': (result.get('objective') or {}).get('value'),
                    'objective_unit': (result.get('objective') or {}).get('unit'), 'valid': bool(check.get('valid')),
                    'errors': list(check.get('errors') or [])[:8], 'metrics': {k: metrics.get(k) for k in keep if k in metrics},
                    'algorithm_version': (result.get('algorithm') or {}).get('version'),
                    'routes_sha256': _hash_polylines(result.get('routes')),
                    'rejected_sha256': _hash_polylines([{'polyline': (result.get('raw_result') or {}).get('rejected_polyline')}])
                    if (result.get('raw_result') or {}).get('rejected_polyline') else None})
        if job['family'] == 'fleet':
            rec['routes'] = [{'vehicle': r.get('vehicle'), 'sequence': r.get('sequence'), 'cost': r.get('cost'),
                              'load': r.get('load')} for r in result.get('routes') or []]
    except Exception as exc:  # noqa: BLE001 - every failure becomes a terminal record
        rec.update({'status': 'error', 'objective': None, 'valid': False, 'errors': [f'{type(exc).__name__}: {exc}'[:500]],
                    'traceback_sha256': hashlib.sha256(traceback.format_exc().encode()).hexdigest()})
    rec['worker_wall_s'] = time.perf_counter() - t0
    rec['worker_cpu_s'] = time.process_time() - c0
    rec['finished_utc'] = utc()
    return rec


def _worker(conn, core: int | None):
    os.environ.update(THREAD_ENV)
    if core is not None and hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(0, {core})
    sys.path.insert(0, str(paths.LAB))
    while True:
        job = conn.recv()
        if job is None:
            return
        conn.send(execute(job))


# ------------------------------------------------------------------ supervisor

def _write_record(out: Path, rec: dict):
    path = out / 'records' / f"{rec['job_id']}.json"
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(rec, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    tmp.replace(path)


def run(out: Path, workers: int, cores: list[int] | None = None, log=print) -> dict:
    """Execute (or resume) the frozen plan in ``out``; returns terminal-state counts."""
    out = Path(out)
    plan_text = (out / 'plan.json').read_text(encoding='utf-8')
    if hashlib.sha256(plan_text.encode('utf-8')).hexdigest() != (out / 'plan.sha256').read_text().strip():
        raise RuntimeError('plan.json 与冻结哈希不一致：拒绝执行')
    plan = json.loads(plan_text)
    (out / 'records').mkdir(exist_ok=True)
    if not (out / 'environment.json').exists():
        (out / 'environment.json').write_text(json.dumps(environment(), ensure_ascii=False, indent=1), encoding='utf-8')
    done = {p.stem for p in (out / 'records').glob('*.json')}
    pending = [j for j in plan['jobs'] if j['job_id'] not in done]
    log(f'{utc()} plan {len(plan["jobs"])} jobs, {len(done)} recorded, {len(pending)} pending, workers={workers}')
    os.environ.update(THREAD_ENV)
    ctx = mp.get_context('spawn')
    cores = cores or list(range(workers))
    slots = []

    def spawn(i):
        parent, child = ctx.Pipe()
        proc = ctx.Process(target=_worker, args=(child, cores[i % len(cores)]), daemon=True)
        proc.start()
        child.close()
        return {'proc': proc, 'conn': parent, 'job': None, 't0': None, 'core': cores[i % len(cores)]}

    slots = [spawn(i) for i in range(workers)]
    queue = list(pending)
    counts: dict = {}
    last = time.time()
    try:
        while queue or any(s['job'] for s in slots):
            for i, s in enumerate(slots):
                if s['job'] is None and queue:
                    s['job'], s['t0'] = queue.pop(0), time.perf_counter()
                    s['conn'].send(s['job'])
                if s['job'] is None:
                    continue
                job, cap = s['job'], float(s['job'].get('timeout_s', plan['timeout_s']))
                rec = None
                if s['conn'].poll(0.01):
                    try:
                        rec = s['conn'].recv()
                    except EOFError:
                        rec = None
                elapsed = time.perf_counter() - s['t0']
                dead = not s['proc'].is_alive()
                if rec is None and (dead or elapsed > cap):
                    state = 'timeout' if elapsed > cap and not dead else 'worker_crash'
                    exitcode = s['proc'].exitcode
                    s['proc'].kill()
                    s['proc'].join(5)
                    rec = {'job_id': job['job_id'], 'family': job['family'], 'config': job['config'], 'algorithm': job['algorithm'],
                           'params': job['params'], 'instance': job['instance'], 'status': state, 'objective': None,
                           'valid': False, 'errors': [f'{state} after {elapsed:.1f}s (cap {cap:.0f}s, exitcode {exitcode})'],
                           'worker_wall_s': elapsed, 'finished_utc': utc(), 'affinity': [s['core']]}
                    slots[i] = spawn(i)
                    s = slots[i]
                if rec is not None:
                    rec['supervisor_wall_s'] = time.perf_counter() - s['t0'] if s['t0'] else None
                    _write_record(out, rec)
                    counts[rec['status']] = counts.get(rec['status'], 0) + 1
                    s['job'], s['t0'] = None, None
            if time.time() - last > 30:
                last = time.time()
                busy = sum(1 for s in slots if s['job'])
                log(f'{utc()} recorded {sum(counts.values())}/{len(pending)} {counts} busy={busy} queue={len(queue)}')
    finally:
        for s in slots:
            try:
                s['conn'].send(None)
            except Exception:  # noqa: BLE001
                pass
            s['proc'].join(2)
            if s['proc'].is_alive():
                s['proc'].kill()
    log(f'{utc()} finished {counts}')
    return counts


def load_records(out: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding='utf-8')) for p in sorted((Path(out) / 'records').glob('*.json'))]
