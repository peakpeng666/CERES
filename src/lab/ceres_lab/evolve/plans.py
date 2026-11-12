"""Portfolio definitions and frozen plan builders for the complementarity audit (development fields only)."""
from __future__ import annotations

import hashlib
import itertools

from . import instances as I

# Fields2Cover full pipeline: decomposition × swath-angle objective (3 × 3), turn model fixed by the machine
# profile; plus the two single-cell adapters registered in the library. Names are stable identifiers.
DECOMPOSITIONS = ('none', 'boustrophedon', 'trapezoidal')
OBJECTIVES = ('n_swath', 'swath_length', 'field_coverage')
COVERAGE_PORTFOLIO = {f'full:{d}/{o}': ('f2c.full', {'decomposition': d, 'swath_objective': o, 'turn_model': 'dubins'})
                      for d, o in itertools.product(DECOMPOSITIONS, OBJECTIVES)}
COVERAGE_PORTFOLIO.update({'single:boustrophedon': ('f2c.boustrophedon', {}), 'single:snake': ('f2c.snake', {})})
WORKBENCH_DEFAULT = 'full:boustrophedon/n_swath'     # F2C_FULL_PARAMS defaults in solvers/__init__.py
LABELS = {'none': '不分解', 'boustrophedon': '牛耕分解', 'trapezoidal': '梯形分解',
          'n_swath': '最少作业行', 'swath_length': '作业行最短', 'field_coverage': '覆盖最大'}

FLEET_BUDGET = {'time_limit_s': 3, 'seed': 42}       # system default, as registered in PRE_REGISTRATION §4
FLEET_PORTFOLIO = {a: (a, dict(FLEET_BUDGET)) for a in
                   ('pyvrp.fleet', 'ortools.fleet', 'panel.savings_fleet', 'panel.sweep', 'panel.cluster_first')}

# Loop-② repair candidates: the same full-pipeline parameters behind a local Dubins repair of curvature
# defects. Trapezoidal decomposition is excluded (main-audit timeouts and structural infeasibility), and the
# single-cell adapters are excluded (their failures are structural, not curvature defects).
REPAIR_DECOMPOSITIONS = ('none', 'boustrophedon')
REPAIR_PORTFOLIO = {f'repair:{d}/{o}': ('evolve.f2c_repair', {'decomposition': d, 'swath_objective': o, 'turn_model': 'dubins'})
                    for d, o in itertools.product(REPAIR_DECOMPOSITIONS, OBJECTIVES)}

# Repair v2 (round 2, T5): the v1 repair plus the unfilletable corners the fillet verdict exposes, then rounding.
REPAIR2_PORTFOLIO = {f'repair2:{d}/{o}': ('evolve.f2c_repair2', {'decomposition': d, 'swath_objective': o, 'turn_model': 'dubins'})
                     for d, o in itertools.product(REPAIR_DECOMPOSITIONS, OBJECTIVES)}


# Round-2 admission study (T4): candidates with deliberately different strengths (ceres_lab.evolve.candidates).
_BASE = {'turn_model': 'dubins'}
CANDIDATE_POOL = {
    'repairw:none/n_swath@0.5-1-2': ('evolve.f2c_repair_w', {**_BASE, 'decomposition': 'none', 'swath_objective': 'n_swath', 'windows': [0.5, 1, 2]}),
    'repairw:none/n_swath@2-4-8-16': ('evolve.f2c_repair_w', {**_BASE, 'decomposition': 'none', 'swath_objective': 'n_swath', 'windows': [2, 4, 8, 16]}),
    'repairw:none/field_coverage@0.5-1-2': ('evolve.f2c_repair_w', {**_BASE, 'decomposition': 'none', 'swath_objective': 'field_coverage', 'windows': [0.5, 1, 2]}),
    'repairw:none/field_coverage@2-4-8-16': ('evolve.f2c_repair_w', {**_BASE, 'decomposition': 'none', 'swath_objective': 'field_coverage', 'windows': [2, 4, 8, 16]}),
    'split:boustrophedon/n_swath@0.25': ('evolve.f2c_split', {**_BASE, 'decomposition': 'boustrophedon', 'swath_objective': 'n_swath', 'split_angle_pi': 0.25}),
    'split:boustrophedon/n_swath@0.75': ('evolve.f2c_split', {**_BASE, 'decomposition': 'boustrophedon', 'swath_objective': 'n_swath', 'split_angle_pi': 0.75}),
    'fillet:none/n_swath': ('evolve.f2c_fillet', {**_BASE, 'decomposition': 'none', 'swath_objective': 'n_swath'}),
    'fillet:none/field_coverage': ('evolve.f2c_fillet', {**_BASE, 'decomposition': 'none', 'swath_objective': 'field_coverage'}),
}

def config_label(name: str) -> str:
    if name.startswith('repair2:'):
        return '修补v2·' + config_label('full:' + name[8:])
    if name.startswith('repair:'):
        return '修补·' + config_label('full:' + name[7:])
    if name.startswith('repairw:'):
        body, _, win = name[8:].partition('@')
        return f"修补(窗口{win.replace('-', '/')}r)·" + config_label('full:' + body)
    if name.startswith('split:'):
        body, _, ang = name[6:].partition('@')
        return f'分割角{ang}π·' + config_label('full:' + body)
    if name.startswith('fillet:'):
        return '倒圆输出·' + config_label('full:' + name[7:])
    if name.startswith('full:'):
        d, o = name[5:].split('/')
        return f'{LABELS[d]}·{LABELS[o]}'
    return {'single:boustrophedon': '单区往复', 'single:snake': '单区蛇形'}.get(name, name)


def _job_id(*parts) -> str:
    return hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:16]


def dev_fields(prefix: str = 'nl_') -> list[str]:
    rows = I.manifest()['fields']
    return [r['field'] for r in rows if r['split'] == 'dev' and r['field'].startswith(prefix)]


def coverage_plan(fields: list[str], profiles=tuple(I.PROFILES), timeout_s: float = 300.0) -> tuple[list[dict], list[dict]]:
    """Jobs for every admitted field × profile × configuration, and the geometric exclusions."""
    jobs, excluded = [], []
    for fid, prof in itertools.product(fields, profiles):
        _sc, inst = I.coverage_instance(fid, prof)
        adm = I.coverage_admission(inst)
        if not adm['admitted']:
            excluded.append({'field': fid, 'profile': prof, 'reason': adm['reason']})
            continue
        for name, (alg, params) in COVERAGE_PORTFOLIO.items():
            jobs.append({'job_id': _job_id('cov', fid, prof, name), 'family': 'coverage', 'config': name, 'algorithm': alg,
                         'params': params, 'instance': {'field': fid, 'profile': prof}, 'timeout_s': timeout_s})
    return jobs, excluded


def repair_plan(fields: list[str], profiles=tuple(I.PROFILES), timeout_s: float = 300.0) -> tuple[list[dict], list[dict]]:
    """Repair-candidate jobs on the same admitted coverage instances as ``coverage_plan`` (identical admission
    rule, so the two runs' instance sets can be merged cell-by-cell for the admission analysis)."""
    jobs, excluded = [], []
    for fid, prof in itertools.product(fields, profiles):
        _sc, inst = I.coverage_instance(fid, prof)
        adm = I.coverage_admission(inst)
        if not adm['admitted']:
            excluded.append({'field': fid, 'profile': prof, 'reason': adm['reason']})
            continue
        for name, (alg, params) in REPAIR_PORTFOLIO.items():
            jobs.append({'job_id': _job_id('rep', fid, prof, name), 'family': 'coverage', 'config': name, 'algorithm': alg,
                         'params': params, 'instance': {'field': fid, 'profile': prof}, 'timeout_s': timeout_s})
    return jobs, excluded


def repair2_plan(fields: list[str], profiles=tuple(I.PROFILES), timeout_s: float = 300.0) -> tuple[list[dict], list[dict]]:
    """Repair-v2 jobs on the same admitted coverage instances as ``coverage_plan`` (identical admission rule)."""
    jobs, excluded = [], []
    for fid, prof in itertools.product(fields, profiles):
        _sc, inst = I.coverage_instance(fid, prof)
        adm = I.coverage_admission(inst)
        if not adm['admitted']:
            excluded.append({'field': fid, 'profile': prof, 'reason': adm['reason']})
            continue
        for name, (alg, params) in REPAIR2_PORTFOLIO.items():
            jobs.append({'job_id': _job_id('rep2', fid, prof, name), 'family': 'coverage', 'config': name, 'algorithm': alg,
                         'params': params, 'instance': {'field': fid, 'profile': prof}, 'timeout_s': timeout_s})
    return jobs, excluded


def candidate_plan(fields: list[str], profiles=tuple(I.PROFILES), timeout_s: float = 300.0) -> tuple[list[dict], list[dict]]:
    """Admission-study candidate jobs on the same admitted coverage instances as ``coverage_plan``."""
    jobs, excluded = [], []
    for fid, prof in itertools.product(fields, profiles):
        _sc, inst = I.coverage_instance(fid, prof)
        adm = I.coverage_admission(inst)
        if not adm['admitted']:
            excluded.append({'field': fid, 'profile': prof, 'reason': adm['reason']})
            continue
        for name, (alg, params) in CANDIDATE_POOL.items():
            jobs.append({'job_id': _job_id('cand', fid, prof, name), 'family': 'coverage', 'config': name, 'algorithm': alg,
                         'params': params, 'instance': {'field': fid, 'profile': prof}, 'timeout_s': timeout_s})
    return jobs, excluded

def fleet_plan(fields: list[str], timeout_s: float = 60.0) -> list[dict]:
    """Two travel modes per field with task size, spatial pattern and trip structure rotated over fields
    (a balanced, non-factorial cover of the factors), plus a makespan block on every other field."""
    sizes, dists = (20, 60, 120), ('uniform', 'clustered', 'rows')
    instances = []
    for k, fid in enumerate(fields):
        for travel in ('rows', 'free'):
            instances.append({'field': fid, 'n_tasks': sizes[k % 3], 'distribution': dists[(k // 3) % 3], 'travel': travel,
                              'objective': 'total_distance', 'seed': 1000 + k, 'multi_trip': bool(k % 2)})
        if k % 2 == 0:
            instances.append({'field': fid, 'n_tasks': 60, 'distribution': dists[k % 3], 'travel': 'rows',
                              'objective': 'makespan', 'seed': 5000 + k, 'multi_trip': False})
    jobs = []
    for spec in instances:
        for name, (alg, params) in FLEET_PORTFOLIO.items():
            jobs.append({'job_id': _job_id('fleet', *sorted(spec.items()), name), 'family': 'fleet', 'config': name,
                         'algorithm': alg, 'params': params, 'instance': spec, 'timeout_s': timeout_s})
    return jobs


def determinism_plan(run_dirs: list, repeats: int = 5, per_cell: int = 10, infeasible: int = 30, seed: int = 20261024,
                     timeout_s: float = 300.0) -> list[dict]:
    """Repeated runs for the run-to-run determinism study (round 2, T2): every recorded hash mismatch, a
    decomposition × machine stratified sample of officially feasible jobs, and a sample of no-solution jobs."""
    import json
    import random
    from pathlib import Path
    from .sweep import load_records
    recs, mismatched = [], set()
    for d in run_dirs:
        d = Path(d)
        recs += [r for r in load_records(d) if r['family'] == 'coverage']
        for p in (d / 'reverify').glob('*.json'):
            if p.name != 'sensitivity.json':
                rv = json.loads(p.read_text(encoding='utf-8'))
                if not rv.get('identical'):
                    mismatched.add(rv['job_id'])
    rng = random.Random(seed)
    by_id = {r['job_id']: r for r in recs}
    chosen = [by_id[j] for j in sorted(mismatched) if j in by_id]
    feas = [r for r in recs if r['status'] == 'feasible' and r.get('valid') and r['job_id'] not in mismatched
            and r['algorithm'] in ('f2c.full', 'evolve.f2c_repair')]
    for dec in DECOMPOSITIONS:
        for prof in I.PROFILES:
            cell = [r for r in feas if r['params'].get('decomposition') == dec and r['instance']['profile'] == prof]
            rng.shuffle(cell)
            chosen += cell[:per_cell]
    nosol = [r for r in recs if r['status'] == 'no_solution' and r['algorithm'] in ('f2c.full', 'evolve.f2c_repair')]
    rng.shuffle(nosol)
    chosen += nosol[:infeasible]
    jobs = []
    for r in chosen:
        group = 'mismatch' if r['job_id'] in mismatched else 'infeasible' if r['status'] == 'no_solution' else 'feasible'
        for k in range(repeats):
            jobs.append({'job_id': _job_id('det', r['job_id'], k), 'family': 'coverage', 'config': r['config'], 'algorithm': r['algorithm'],
                         'params': r['params'], 'instance': {**r['instance'], 'repeat': k}, 'timeout_s': timeout_s,
                         'source_job': r['job_id'], 'source_status': r['status'], 'source_sha256': r.get('routes_sha256'),
                         'sample_group': group})
    return jobs
