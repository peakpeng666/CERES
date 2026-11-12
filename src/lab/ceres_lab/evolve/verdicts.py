"""Verdict-aware coverage datasets and the extended-library policy evaluation (round 2, task T3).

A *verdict* decides which officially recorded paths count as feasible:

- ``official`` — the sweep record (status feasible and the project checker valid);
- ``fillet``   — ``fillet/<job>.json`` (rounded path passes the unchanged checker; 1 cm deviation tolerance);
- ``strict``   — ``reverify/<job>.json`` (2 cm densified three-point curvature).

Fillet and strict verdicts that could not be evaluated on the recorded path (a re-run never reproduced its hash)
are ``None``: treated as infeasible and counted, with a sensitivity that drops affected instances. The decision
rule applied here is the one committed in ``experiments/runs/evolve-policy-ext-20261024/README.md`` before the run.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import stats

from . import audit, plans, policy
from . import features as F
from . import instances as I
from .sweep import load_records

VERDICTS = ('official', 'fillet', 'strict')


def verdict_map(run_dir: Path, verdict: str) -> dict:
    """job_id → True / False / None (unverifiable or missing) for officially feasible coverage jobs."""
    run_dir = Path(run_dir)
    if verdict == 'official':
        return {}
    if verdict == 'fillet':
        out = {}
        for p in (run_dir / 'fillet').glob('*.json'):
            if p.name == 'summary.json':
                continue
            r = json.loads(p.read_text(encoding='utf-8'))
            out[r['job_id']] = None if r.get('verdict') != 'evaluated' else bool(r.get('fillet_valid'))
        return out
    if verdict == 'strict':
        out = {}
        for p in (run_dir / 'reverify').glob('*.json'):
            if p.name == 'sensitivity.json':
                continue
            r = json.loads(p.read_text(encoding='utf-8'))
            out[r['job_id']] = None if not r.get('identical') else bool(r.get('strict_valid_curvature', False))
        return out
    raise ValueError(verdict)


def strict_from_fillet(run_dir: Path) -> dict:
    """Derive strict verdicts from the fillet batch for solvers that return the rounded path (repair v2).

    Repair v2 applies rounding before returning, so the recorded path and the rounded path coincide; its fillet
    record's ``strict_violations_after`` is therefore the strict verdict on the recorded path. The fillet batch also
    verifies that the recorded hash is reproduced. Writes ``reverify/<job>.json`` in the format consumed by
    ``verdict_map``. For runs whose solver does not round before returning (repair v1), use ``reverify`` instead.
    """
    run_dir = Path(run_dir)
    target = run_dir / 'reverify'
    target.mkdir(exist_ok=True)
    n = valid = 0
    for p in (run_dir / 'fillet').glob('*.json'):
        if p.name == 'summary.json':
            continue
        r = json.loads(p.read_text(encoding='utf-8'))
        ok = bool(r.get('identical')) and r.get('verdict') == 'evaluated'
        rv = {'job_id': r['job_id'], 'config': r.get('config'), 'identical': ok,
              'strict_valid_curvature': bool(ok and int(r.get('strict_violations_after', 1)) == 0),
              'strict_violations': r.get('strict_violations_after'),
              'source': 'fillet batch (returned rounded path)'}
        (target / f"{r['job_id']}.json").write_text(json.dumps(rv, ensure_ascii=False), encoding='utf-8')
        n += 1
        valid += int(rv['strict_valid_curvature'])
    return {'jobs': n, 'strict_valid': valid}


def dataset_from(dirs: list[Path], configs: list[str], verdict: str = 'official', drop_unverifiable: bool = False) -> dict:
    records, vmap = [], {}
    for d in dirs:
        records += [r for r in load_records(d) if r['family'] == 'coverage' and r['config'] in configs]
        vmap.update(verdict_map(d, verdict))
    m = audit.matrix(records, 'coverage', configs)
    job = {(json.dumps(r['instance'], sort_keys=True), r['config']): r['job_id'] for r in records}
    cost = m['cost'].copy()
    unverifiable = np.zeros(cost.shape, dtype=bool)
    if verdict != 'official':
        for i, key in enumerate(m['keys']):
            for j, c in enumerate(configs):
                if not np.isfinite(cost[i, j]):
                    continue
                v = vmap.get(job.get((key, c)), None)
                if v is None:
                    unverifiable[i, j] = True
                if v is not True:
                    cost[i, j] = np.nan
    keep = np.ones(len(m['keys']), dtype=bool)
    if drop_unverifiable:
        keep = ~unverifiable.any(axis=1)
    complete = ~np.any(m['status'] == 'missing', axis=1)
    keep &= complete
    idx = np.where(keep)[0]
    m2 = {k: (v[idx] if isinstance(v, np.ndarray) else [v[i] for i in idx] if isinstance(v, list) and len(v) == len(m['keys']) else v)
          for k, v in m.items()}
    m2['cost'] = cost[idx]
    feats = []
    for spec in m2['meta']:
        _sc, inst = I.coverage_instance(spec['field'], spec['profile'])
        feats.append(F.coverage_features(inst['field'], inst['obstacles'], inst['swath_width'], inst['turn_radius'], inst['headland_width']))
    r, solvable = audit.rho(m2['cost'], policy.PENALTY)
    return {'m': m2, 'rho': r, 'solvable': solvable, 'feats': feats, 'groups': np.array([s['field'] for s in m2['meta']]),
            'profiles': np.array([s['profile'] for s in m2['meta']]), 'configs': configs, 'wall': m2['wall'],
            'verdict': verdict, 'unverifiable_cells': int(unverifiable[idx].sum()), 'instances_dropped': int((~keep).sum())}


def _holm(pvals: dict) -> dict:
    items = sorted(pvals.items(), key=lambda kv: (kv[1] is None, kv[1] if kv[1] is not None else 1.0))
    m = len(items)
    out, running = {}, 0.0
    for rank, (k, p) in enumerate(items):
        adj = 1.0 if p is None else min(1.0, (m - rank) * p)
        running = max(running, adj)
        out[k] = running
    return out


def decide(cv: dict, groups) -> dict:
    """Apply the committed promotion rule to every learned policy versus static."""
    first, stop, raw_p = {}, {}, {}
    for kind in policy.LEARNED:
        for fs in ('size', 'agri'):
            name = f'{kind}:{fs}'
            first[name] = policy.paired(cv, groups, name, 'static', 'first', better='higher')
            stop[name] = policy.paired(cv, groups, name, 'static', 'rho')
            raw_p[name] = first[name]['wilcoxon_p_one_sided']
    holm = _holm(raw_p)
    out = {}
    for name in first:
        f, s = first[name], stop[name]
        a = f['bootstrap_ci95'][0] > 0 and holm[name] < 0.05
        b = s['bootstrap_ci95'][1] <= 0.01
        d = np.array([0.0])
        two_sided = None
        keep = cv['keep']
        diffs = {}
        for i in keep:
            diffs.setdefault(groups[i], []).append(cv['per_instance'][name]['first'][i] - cv['per_instance']['static']['first'][i])
        d = np.array([np.mean(v) for v in diffs.values()])
        nz = d[np.abs(d) > 1e-12]
        if len(nz) >= 10:
            two_sided = float(stats.wilcoxon(nz).pvalue)
        out[name] = {'first_try_diff_mean': f['mean_diff'], 'first_try_ci95': f['bootstrap_ci95'], 'p_one_sided': raw_p[name],
                     'p_holm': holm[name], 'p_two_sided': two_sided, 'stop_rho_diff_mean': s['mean_diff'],
                     'stop_rho_ci95': s['bootstrap_ci95'], 'criterion_a': bool(a), 'criterion_b': bool(b), 'promote': bool(a and b),
                     'wins_losses_ties_first': f['wins_losses_ties']}
    return out


def evaluate(dirs: list[Path], configs: list[str], verdict: str, drop_unverifiable: bool = False, preq: bool = True) -> dict:
    ds = dataset_from(dirs, configs, verdict, drop_unverifiable)
    cv = policy.crossval(ds)
    res = {'verdict': verdict, 'configs': configs, 'instances': int(len(ds['rho'])), 'solvable': int(ds['solvable'].sum()),
           'unverifiable_cells': ds['unverifiable_cells'], 'instances_dropped': ds['instances_dropped'],
           'feasible_per_config': {c: int(np.isfinite(ds['m']['cost'][:, j]).sum()) for j, c in enumerate(configs)},
           'cv': cv['policies'], 'decisions': decide(cv, ds['groups']),
           'static_vs_default': policy.paired(cv, ds['groups'], 'static', 'default', 'first', better='higher')}
    if preq:
        pq = policy.prequential(ds)
        res['prequential_final'] = {n: c['final_mean'] for n, c in pq['curves'].items()}
    return res


def _combo(args):
    import os
    lib, cfgs, dirs, v, drop, preq, cores = args
    if cores and hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(0, set(cores))
    return lib, v, drop, evaluate(dirs, cfgs, v, drop_unverifiable=drop, preq=preq)


def write(main_dir: Path, repair_dir: Path, out_dir: Path, workers: int = 4, cores=None) -> dict:
    """All library × verdict combinations; prequential curves only for the official and fillet verdicts."""
    from concurrent.futures import ProcessPoolExecutor
    old = list(plans.COVERAGE_PORTFOLIO)
    ext = old + list(plans.REPAIR_PORTFOLIO)
    jobs = []
    for lib, cfgs, dirs in (('old', old, [main_dir]), ('extended', ext, [main_dir, repair_dir])):
        for v in VERDICTS:
            jobs.append((lib, cfgs, dirs, v, False, v in ('official', 'fillet'), cores))
            if v != 'official':
                jobs.append((lib, cfgs, dirs, v, True, False, cores))
    results = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for lib, v, drop, res in ex.map(_combo, jobs):
            results[f'{lib}:{v}' + (':drop_unverifiable' if drop else '')] = res
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'policy_ext.json').write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    return {k: {'solvable': v['solvable'], 'static_first': round(v['cv']['static']['first_try_feasible'], 3),
                'promoted': [n for n, d in v['decisions'].items() if d['promote']]} for k, v in results.items()}


def write_combined(dirs: list[Path], configs: list[str], out_dir: Path, workers: int = 4, cores=None) -> dict:
    """All verdict combinations for one merged library (e.g. old + repair v1 + repair v2)."""
    from concurrent.futures import ProcessPoolExecutor
    jobs = [('combined', configs, list(dirs), v, False, v in ('official', 'fillet'), cores) for v in VERDICTS]
    for v in VERDICTS:
        if v != 'official':
            jobs.append(('combined', configs, list(dirs), v, True, False, cores))
    results = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for lib, v, drop, res in ex.map(_combo, jobs):
            results[f'{lib}:{v}' + (':drop_unverifiable' if drop else '')] = res
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'policy_combined.json').write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    return {k: {'solvable': v['solvable'], 'static_first': round(v['cv']['static']['first_try_feasible'], 3),
                'promoted': [n for n, d in v['decisions'].items() if d['promote']]} for k, v in results.items()}
