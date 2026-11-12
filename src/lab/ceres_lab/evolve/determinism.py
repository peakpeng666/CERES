"""Run-to-run determinism of the Fields2Cover coverage pipeline (round 2, task T2).

Every job of ``evolve-determinism-20261024`` repeats one recorded task five times on the energy cores. This
module turns those records into per-task stability facts — distinct path hashes, agreement with the original
record, the set of terminal states, feasibility flips (feasible in some repeats, no solution in others) and the
coefficient of variation of the cost among feasible repeats — and aggregates them by sample group, configuration,
decomposition and machine profile. Flip probabilities carry Wilson 95 % intervals; a missing record is counted as
a failure and never silently dropped.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

Z = 1.96


def wilson(k: int, n: int, z: float = Z) -> list[float | None]:
    """Wilson score interval for a binomial proportion (k successes in n trials)."""
    if n <= 0:
        return [None, None]
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, centre - half), min(1.0, centre + half)]


def outcome(rec: dict | None) -> str:
    """feasible (independent check passed) / no_solution / error / timeout / worker_crash / missing."""
    if rec is None:
        return 'missing'
    if rec.get('status') == 'feasible':
        return 'feasible' if rec.get('valid') else 'invalid'
    return str(rec.get('status') or 'error')


def _cv(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = float(np.mean(values))
    if abs(mean) < 1e-12:
        return None
    return float(np.std(values, ddof=1) / mean)


def _decomposition(task: dict) -> str:
    params = task.get('params') or {}
    if 'decomposition' in params:
        return str(params['decomposition'])
    config = str(task.get('config') or '')
    if config.startswith(('full:', 'repair:', 'repairw:', 'repair2:', 'fillet:', 'split:')):
        body = config.split(':', 1)[1]
        return body.split('/')[0].split('@')[0]
    return 'single'


def task_summary(rows: list[dict], expected: int, source_sha256: str | None) -> dict:
    """Stability facts for the repeats of one source job (rows sorted by repeat)."""
    rows = sorted(rows, key=lambda r: int(r['instance'].get('repeat', 0)))
    outcomes = [outcome(r) for r in rows]
    outcomes += ['missing'] * max(0, expected - len(outcomes))
    hashes = [r.get('routes_sha256') if o == 'feasible' else None for r, o in zip(rows, outcomes)]
    hashes += [None] * max(0, expected - len(hashes))
    costs = [float(r['objective']) for r in rows if outcome(r) == 'feasible' and r.get('objective') is not None]
    present = [h for h in hashes if h]
    distinct = len(set(present)) if present else 0
    flip = 'feasible' in outcomes and 'no_solution' in outcomes
    flip_any = 'feasible' in outcomes and any(o != 'feasible' for o in outcomes)
    matches = sum(1 for r in rows if source_sha256 and r.get('routes_sha256') == source_sha256)
    return {'outcomes': outcomes, 'statuses': sorted(set(outcomes)), 'observed': len(rows), 'expected': expected,
            'distinct_hashes': distinct, 'reproducible': bool(present) and len(present) == expected and distinct == 1,
            'stable_feasibility': len(set(outcomes)) == 1, 'flip': bool(flip), 'flip_any': bool(flip_any),
            'matches_source': matches, 'source_available': source_sha256 is not None,
            'cost_cv': _cv(costs), 'feasible_repeats': len(costs), 'status_counts': dict(Counter(outcomes))}


def _cell(tasks: list[dict]) -> dict:
    n = len(tasks)
    if n == 0:
        return {'tasks': 0}
    flips = sum(t['flip'] for t in tasks)
    flips_any = sum(t['flip_any'] for t in tasks)
    cvs = [t['cost_cv'] for t in tasks if t['cost_cv'] is not None]
    repeats = sum(t['observed'] for t in tasks)
    expected = sum(t['expected'] for t in tasks)
    status = Counter()
    for t in tasks:
        status.update(t['status_counts'])
    source_tasks = [t for t in tasks if t['source_available']]
    return {'tasks': n, 'repeats_observed': repeats, 'repeats_expected': expected,
            'missing_repeats': expected - repeats,
            'reproducible_tasks': sum(t['reproducible'] for t in tasks),
            'reproducible_share': sum(t['reproducible'] for t in tasks) / n,
            'stable_tasks': sum(t['stable_feasibility'] for t in tasks),
            'stable_share': sum(t['stable_feasibility'] for t in tasks) / n,
            'flip_tasks': int(flips), 'flip_rate': flips / n, 'flip_wilson95': wilson(int(flips), n),
            'flip_any_tasks': int(flips_any), 'flip_any_rate': flips_any / n, 'flip_any_wilson95': wilson(int(flips_any), n),
            'cost_cv_median': float(np.median(cvs)) if cvs else None, 'cost_cv_max': float(np.max(cvs)) if cvs else None,
            'cost_cv_tasks': len(cvs), 'status_counts': dict(sorted(status.items())),
            'source_match_share': (sum(t['matches_source'] for t in source_tasks) / max(1, sum(t['expected'] for t in source_tasks)))
            if source_tasks else None}


def _group(tasks: list[dict], key) -> dict:
    cells = defaultdict(list)
    for t in tasks:
        cells[key(t)].append(t)
    return {str(k): _cell(v) for k, v in sorted(cells.items())}


def analyse(run_dir: Path) -> dict:
    """Per-task stability facts and aggregates; writes nothing (see ``write``)."""
    run_dir = Path(run_dir)
    plan = json.loads((run_dir / 'plan.json').read_text(encoding='utf-8'))
    recs = {}
    for p in sorted((run_dir / 'records').glob('*.json')):
        r = json.loads(p.read_text(encoding='utf-8'))
        recs[r['job_id']] = r
    groups = defaultdict(list)
    meta = {}
    for job in plan['jobs']:
        src = job.get('source_job')
        groups[src].append(recs.get(job['job_id']))
        meta.setdefault(src, {'config': job.get('config'), 'params': job.get('params') or {},
                              'sample_group': job.get('sample_group'), 'source_status': job.get('source_status'),
                              'source_sha256': job.get('source_sha256'), 'profile': job['instance'].get('profile'),
                              'instance': {k: v for k, v in job['instance'].items() if k != 'repeat'}})
    tasks = []
    for src, rows in groups.items():
        m = meta[src]
        expected = len(groups[src])
        t = {'source_job': src, 'config': m['config'], 'sample_group': m['sample_group'],
             'source_status': m['source_status'], 'decomposition': _decomposition({'config': m['config'], 'params': m['params']}),
             'profile': m['profile'], 'instance': m['instance'], **task_summary([r for r in rows if r], expected, m['source_sha256'])}
        tasks.append(t)
    tasks.sort(key=lambda t: (str(t['config']), str(t['source_job'])))
    by_group = _group(tasks, lambda t: t['sample_group'])
    by_config = _group(tasks, lambda t: t['config'])
    by_decomp = _group(tasks, lambda t: t['decomposition'])
    by_profile = _group(tasks, lambda t: t['profile'])
    by_cell = _group(tasks, lambda t: f"{t['decomposition']}|{t['profile']}")
    by_sample_decomp = _group(tasks, lambda t: f"{t['sample_group']}|{t['decomposition']}")
    return {'run': run_dir.name, 'tasks': len(tasks), 'repeats_per_task': max(t['expected'] for t in tasks) if tasks else 0,
            'repeats_observed': sum(t['observed'] for t in tasks), 'repeats_expected': sum(t['expected'] for t in tasks),
            'overall': _cell(tasks), 'by_sample_group': by_group, 'by_config': by_config,
            'by_decomposition': by_decomp, 'by_profile': by_profile, 'by_decomposition_profile': by_cell,
            'by_sample_decomposition': by_sample_decomp, 'per_task': tasks}


def write(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    res = analyse(run_dir)
    (run_dir / 'analysis.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'tasks': res['tasks'], 'overall': {k: res['overall'][k] for k in
                                               ('reproducible_share', 'stable_share', 'flip_rate', 'flip_wilson95', 'cost_cv_median')}}
