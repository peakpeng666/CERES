"""Post-sweep re-verification of feasible coverage paths: identity and a sampling-invariant curvature check.

The project checker (``f2c_coverage.check_path``) tests three-point curvature on the path's native samples. Turns
are sampled every 2 cm, but straight swath and headland segments are single long chords, so a corner joining two
long chords is judged by a wide three-point circle and can pass although its exact curvature is unbounded (found
on 2026-10-23: nl_field_1, small robot, no decomposition). This module re-runs every feasible coverage job
(Fields2Cover is deterministic), confirms the path hash equals the recorded one, and adds a strict verdict that
densifies every chord to ≤ 2 cm before the same curvature test. The official verdict is unchanged; the strict one
is a sensitivity analysis.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .sweep import THREAD_ENV, load_records, solve, utc

STEP = 0.02


def strict_curvature(points, radius: float, step: float = STEP) -> dict:
    """Max three-point curvature after densifying every chord to ≤ step; counts samples above 1/r (rel. tol 1e-3)."""
    limit = (1.0 / radius) * (1 + 1e-3)
    worst, violations, corners = 0.0, 0, []
    prev2 = prev1 = None
    n_dense = 0

    def visit(p):
        nonlocal prev2, prev1, worst, violations, n_dense
        n_dense += 1
        if prev2 is not None:
            a, b, c = prev2, prev1, p
            prod = math.dist(a, b) * math.dist(b, c) * math.dist(a, c)
            if prod > 1e-12:
                k = 2 * abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / prod
                if k > worst:
                    worst = k
                if k > limit:
                    violations += 1
                    if len(corners) < 20:
                        corners.append([round(b[0], 3), round(b[1], 3), round(k, 4)])
        prev2, prev1 = prev1, p

    for i, (a, b) in enumerate(zip(points, points[1:])):
        if i == 0:
            visit(a)
        n = max(1, math.ceil(math.dist(a, b) / step))
        for t in range(1, n + 1):
            visit((a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n))
    return {'strict_max_curvature_per_m': worst, 'strict_violations': violations, 'strict_valid_curvature': violations == 0,
            'limit_per_m': limit, 'dense_samples': n_dense, 'violation_samples': corners}


def _one(rec: dict) -> dict:
    os.environ.update(THREAD_ENV)
    from . import instances as I
    sc, inst = I.coverage_instance(rec['instance']['field'], rec['instance']['profile'])
    result = solve(sc, inst, rec['algorithm'], dict(rec['params']))
    routes = result.get('routes') or []
    digest = hashlib.sha256(json.dumps([r.get('polyline') for r in routes], separators=(',', ':')).encode()).hexdigest() if routes else None
    out = {'job_id': rec['job_id'], 'recorded_sha256': rec.get('routes_sha256'), 'rerun_sha256': digest,
           'identical': digest == rec.get('routes_sha256'), 'rerun_status': result['status'], 'checked_utc': utc()}
    if routes:
        out.update(strict_curvature(routes[0]['polyline'], inst['turn_radius']))
    return out


def run(out: Path, workers: int, cores: list[int] | None) -> dict:
    out = Path(out)
    target = out / 'reverify'
    target.mkdir(exist_ok=True)
    done = {p.stem for p in target.glob('*.json')}
    todo = [r for r in load_records(out) if r['family'] == 'coverage' and r['status'] == 'feasible' and r.get('valid')
            and r['job_id'] not in done]

    def init(core_list):
        if core_list and hasattr(os, 'sched_setaffinity'):
            os.sched_setaffinity(0, set(core_list))
    counts = {'identical': 0, 'different': 0, 'strict_fail': 0}
    with ProcessPoolExecutor(max_workers=workers, initializer=init, initargs=(cores,)) as ex:
        futs = {ex.submit(_one, r): r for r in todo}
        for f in as_completed(futs):
            res = f.result()
            (target / f"{res['job_id']}.json").write_text(json.dumps(res, sort_keys=True), encoding='utf-8')
            counts['identical' if res['identical'] else 'different'] += 1
            counts['strict_fail'] += 0 if res.get('strict_valid_curvature', True) else 1
    return counts


def load(out: Path) -> dict:
    return {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in (Path(out) / 'reverify').glob('*.json')}


def sensitivity(out: Path) -> dict:
    """Strict-verdict sensitivity analysis: replace feasibility with the densified-curvature verdict and
    recompute the coverage SBS/VBS/headroom, slices and the policy comparison. Official verdicts unchanged."""
    import numpy as np

    from . import audit, plans, policy
    out = Path(out)
    rev = load(out)
    bad = {j for j, r in rev.items() if not r.get('strict_valid_curvature', True)}
    mismatched = sorted(j for j, r in rev.items() if not r.get('identical'))
    records = [r for r in load_records(out) if r['family'] == 'coverage']
    m = audit.matrix(records, 'coverage', list(plans.COVERAGE_PORTFOLIO))
    jid_of = {(json.dumps(r['instance'], sort_keys=True), r['config']): r['job_id'] for r in records}
    cost = m['cost'].copy()
    flipped = []
    for i, key in enumerate(m['keys']):
        for j, c in enumerate(m['configs']):
            if np.isfinite(cost[i, j]) and jid_of.get((key, c)) in bad:
                flipped.append({'field': m['meta'][i]['field'], 'profile': m['meta'][i]['profile'], 'config': c})
                cost[i, j] = np.nan
    groups = [s['field'] for s in m['meta']]
    summary = audit.family_summary({**m, 'cost': cost}, groups, plans.WORKBENCH_DEFAULT)
    if 'sbs' in summary:
        summary['slices'] = audit.coverage_slices({**m, 'cost': cost}, summary['sbs']['config'])
    ds = policy.dataset(out)
    rho, solvable = audit.rho(cost, audit.PENALTY)
    ds = dict(ds, rho=rho, solvable=solvable)
    cv = policy.crossval(ds)
    comparisons = [policy.paired(cv, ds['groups'], 'static', 'default', 'first', better='higher')]
    for fs in ('agri', 'size'):
        for kind in policy.LEARNED:
            comparisons.append(policy.paired(cv, ds['groups'], f'{kind}:{fs}', 'static', 'first', better='higher'))
    res = {'identical_mismatches': mismatched, 'strict_infeasible_jobs': sorted(bad), 'flipped_instance_configs': flipped,
           'coverage_strict': summary, 'policy_cv': cv['policies'], 'policy_comparisons': comparisons,
           'note': '严格判定（每条弦加密至 2 cm 后的三点曲率）替换可行性后的敏感性分析；官方判定与正式结论不变'}
    (out / 'reverify').mkdir(exist_ok=True)
    (out / 'reverify' / 'sensitivity.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'flipped': len(flipped), 'mismatched': len(mismatched), 'strict_bad': len(bad),
            'sbs_strict': summary.get('sbs'), 'workbench_default_strict': summary.get('workbench_default')}
