"""Loop ② admission analysis: should a candidate member enter the coverage library?

Implements the admission comparison of docs/下一阶段.md §2 on development data. For a candidate set C added to
the existing library L, report the four admission signals — single-member performance, VBS increment, actual
policy gain after refitting the policy, and Shapley contribution — and the five controls: old-library policy,
training SBS, new member alone, new-library VBS and new-library policy. Policies are refitted inside the same
field-grouped cross-validation folds, so "actual policy gain" is a held-out quantity; VBS figures are hindsight
upper bounds and never the main conclusion.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import audit, policy
from . import features as F
from . import instances as I
from .sweep import load_records


def merged_matrix(dirs: list[Path], configs: list[str]) -> dict:
    records = []
    for d in dirs:
        records += [r for r in load_records(d) if r['family'] == 'coverage' and r['config'] in configs]
    return audit.matrix(records, 'coverage', configs)


def _cv_policy(rho, wall, groups, feats, kind: str, fs: str | None, repeats: int, folds: int, keep, seed0: int = 1000):
    """Held-out (field-grouped) first-try feasibility, stop ratio and tries of one policy family on a portfolio.

    ``keep`` fixes the denominator (instances solvable by the extended library) so old and new libraries are
    compared on the same instances and the same yardstick (ratios to the extended library's per-instance best)."""
    keep = np.asarray(keep)
    X = policy.design(feats, F.FEATURE_SETS[fs or 'agri'])   # rows for every policy kind; static ignores the values
    uniq = np.array(sorted(set(groups[keep])))
    first = np.zeros(len(rho))
    stop = np.zeros(len(rho))
    tries = np.zeros(len(rho))
    for rep in range(repeats):
        rng = np.random.default_rng(seed0 + rep)
        fold_of = {g: f for g, f in zip(rng.permutation(uniq), np.arange(len(uniq)) % folds)}
        for fold in range(folds):
            test = np.array([i for i in keep if fold_of[groups[i]] == fold])
            train = np.array([i for i in keep if fold_of[groups[i]] != fold])
            model = policy.fit(kind, X[train] if kind in policy.LEARNED else None, rho[train])
            orders = policy.order(model, X[test], rng)
            for i, o in zip(test, orders):
                e = policy.execute(o, rho[i], wall[i])
                first[i] += e['first_feasible'] / repeats
                stop[i] += e['rho'] / repeats
                tries[i] += e['tries'] / repeats
    return {'first_try_feasible': float(first[keep].mean()), 'stop_rho': float(stop[keep].mean()), 'tries': float(tries[keep].mean()),
            'instances': int(len(keep))}


def analyse(dirs: list[Path], library: list[str], candidates: list[str], repeats: int = 10, folds: int = 5) -> dict:
    allc = library + candidates
    m = merged_matrix(dirs, allc)
    complete = ~np.any(m['status'] == 'missing', axis=1)
    keys = [k for k, c in zip(m['keys'], complete) if c]
    idx = np.where(complete)[0]
    cost, wall = m['cost'][idx], m['wall'][idx]
    meta = [m['meta'][i] for i in idx]
    groups = np.array([s['field'] for s in meta])
    feats = []
    for spec in meta:
        _sc, inst = I.coverage_instance(spec['field'], spec['profile'])
        feats.append(F.coverage_features(inst['field'], inst['obstacles'], inst['swath_width'], inst['turn_radius'], inst['headland_width']))
    lib_j = [allc.index(c) for c in library]
    new_j = list(range(len(allc)))
    rho_new, solv_new = audit.rho(cost, audit.PENALTY)            # ratios relative to the best of the new library
    feas = np.isfinite(cost)
    out = {'instances': int(len(idx)), 'library': library, 'candidates': candidates, 'excluded_incomplete': int((~complete).sum())}
    # Signals on the common denominator (instances solvable by the new library), ratios relative to new-library best.
    rs = rho_new[solv_new]
    out['single_member'] = [{'config': c, 'feasible': int(feas[:, j].sum()), 'mean_rho': float(rs[:, j].mean())} for j, c in enumerate(allc)]
    vbs_old = float(rs[:, lib_j].min(axis=1).mean())
    out['vbs'] = {'old_library_mean_rho': vbs_old, 'new_library_mean_rho': float(rs.min(axis=1).mean()),
                  'old_library_feasible': int(feas[:, lib_j].any(axis=1).sum()), 'new_library_feasible': int(feas.any(axis=1).sum()),
                  'vbs_increment_rho': vbs_old - float(rs.min(axis=1).mean())}
    if len(allc) <= 20:
        phi, shapley_method = audit.shapley(rs, audit.PENALTY), 'exact'
    else:
        from .ledger import _shapley
        phi, shapley_method = _shapley(rs, audit.PENALTY)
    out['shapley_method'] = shapley_method
    out['shapley'] = [{'config': c, 'shapley': float(p)} for c, p in zip(allc, phi)]
    # Controls and actual policy gain (held-out), each on its own portfolio's ratio matrix.
    keep = np.where(solv_new)[0]
    rho_old = rho_new[:, lib_j]                                     # same yardstick: the extended library's best
    sbs_old = int(np.argmin(rho_old[keep].mean(axis=0)))
    controls = {}
    for name, (r, w) in {'old_library': (rho_old, wall[:, lib_j]), 'new_library': (rho_new, wall)}.items():
        controls[name] = {'static': _cv_policy(r, w, groups, feats, 'static', None, repeats, folds, keep),
                          'logit:agri': _cv_policy(r, w, groups, feats, 'logit', 'agri', repeats, folds, keep),
                          'knn5:agri': _cv_policy(r, w, groups, feats, 'knn5', 'agri', repeats, folds, keep)}
    for c in candidates:
        j = allc.index(c)
        controls[f'member_alone:{c}'] = {'feasible_share': float(feas[:, j].mean())}
    controls['training_sbs_old'] = {'config': library[sbs_old], 'feasible_share': float(feas[:, lib_j[sbs_old]].mean())}
    out['controls'] = controls
    out['actual_policy_gain'] = {k: {'first_try_feasible': controls['new_library'][k]['first_try_feasible'] - controls['old_library'][k]['first_try_feasible'],
                                     'stop_rho': controls['old_library'][k]['stop_rho'] - controls['new_library'][k]['stop_rho'],
                                     'tries': controls['old_library'][k]['tries'] - controls['new_library'][k]['tries']}
                                 for k in ('static', 'logit:agri', 'knn5:agri')}
    out['yardstick'] = '两种组合都以扩展后组合的逐实例最好可行成本为 1；分母为扩展后组合可解的实例'
    out['keys_sha256'] = __import__('hashlib').sha256('\n'.join(keys).encode()).hexdigest()
    return out


def write(main_dir: Path, candidate_dir: Path, library: list[str], candidates: list[str]) -> dict:
    res = analyse([Path(main_dir), Path(candidate_dir)], library, candidates)
    target = Path(candidate_dir) / 'admission'
    target.mkdir(exist_ok=True)
    (target / 'admission.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    return res


def write_dirs(dirs: list[Path], library: list[str], candidates: list[str], target: Path) -> dict:
    """Admission analysis over an arbitrary set of run directories (e.g. baseline + repair v1 + repair v2)."""
    res = analyse([Path(d) for d in dirs], library, candidates)
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    (target / 'admission.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    return res
