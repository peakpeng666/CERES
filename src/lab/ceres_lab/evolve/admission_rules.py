"""Which admission rule builds the better library? Nested, field-grouped evaluation (round 2, task T4).

Base library L0 (the 11 audited coverage configurations) and a candidate pool C (repair v1, repair window
variants, split-angle variants, fillet-output variants). For every outer split (5 folds × repeats, grouped by
field) each rule selects up to m candidates **using only the outer-training fields**:

- R1 single member — lowest mean penalised ratio on the training fields;
- R2 VBS increment — greedy forward: largest gain in training instances solved by L0 ∪ S, ties by mean best ratio;
- R3 actual policy gain — greedy forward: largest inner 4-fold (field-grouped) gain in held-out first-try
  feasibility of the static try-order on L0 ∪ S;
- R4 policy residual — greedy forward: most training instances on which the current static order's first choice
  fails but the candidate succeeds;
- random — m random candidates (20 draws per split).

The selected library is evaluated on the outer-test fields with its static order and the logit (agricultural
features) policy, both fitted on the outer-training fields. Ratios use one yardstick: the best feasible cost over
L0 ∪ C on that instance. Control: L0 alone, with first-try and "feasible within 1 + m tries" (equal extra tries).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import stats

from . import audit, policy
from . import features as F
from .verdicts import dataset_from, _holm

RULES = ('R1_single', 'R2_vbs', 'R3_policy', 'R4_residual', 'random')
CAPACITIES = (1, 2, 4)


def _static_first(rho, wall, train, test, cols):
    order = policy.greedy(rho[np.ix_(train, cols)])
    seq = [cols[j] for j in order]
    out = [policy.execute(seq, rho[i], wall[i]) for i in test]
    return out, seq


def _inner_gain(rho, wall, groups, train, cols, folds=4, seed=0):
    uniq = np.array(sorted(set(groups[train])))
    rng = np.random.default_rng(seed)
    fold_of = {g: f for g, f in zip(rng.permutation(uniq), np.arange(len(uniq)) % folds)}
    hits = []
    for f in range(folds):
        tr = np.array([i for i in train if fold_of[groups[i]] != f])
        te = np.array([i for i in train if fold_of[groups[i]] == f])
        if len(tr) == 0 or len(te) == 0:
            continue
        res, _ = _static_first(rho, wall, tr, te, cols)
        hits += [e['first_feasible'] for e in res]
    return float(np.mean(hits)) if hits else 0.0


def select(rule, m, rho, wall, groups, train, base, cand, rng):
    feas = rho < policy.PENALTY
    chosen = []
    if rule == 'random':
        return list(rng.choice(cand, size=min(m, len(cand)), replace=False))
    if rule == 'R1_single':
        scores = {c: rho[train, c].mean() for c in cand}
        return sorted(cand, key=lambda c: (scores[c], c))[:m]
    for _ in range(m):
        best, best_key = None, None
        lib = base + chosen
        if rule == 'R4_residual':
            _, seq = _static_first(rho, wall, train, train[:1], lib)
            first = seq[0]
            residual = train[~feas[train, first]]
        for c in cand:
            if c in chosen:
                continue
            cols = lib + [c]
            if rule == 'R2_vbs':
                key = (int(feas[np.ix_(train, cols)].any(axis=1).sum()), -float(rho[np.ix_(train, cols)].min(axis=1).mean()))
            elif rule == 'R3_policy':
                key = (_inner_gain(rho, wall, groups, train, cols), -float(rho[np.ix_(train, cols)].min(axis=1).mean()))
            else:  # R4
                key = (int(feas[residual, c].sum()) if len(residual) else 0, -float(rho[train, c].mean()))
            if best_key is None or key > best_key:
                best, best_key = c, key
        if best is None:                          # fewer candidates than the requested capacity
            break
        chosen.append(best)
    return chosen


def _logit_eval(rho, wall, X, train, test, cols):
    model = policy.fit('logit', X[train], rho[np.ix_(train, cols)])
    orders = policy.order(model, X[test])
    return [policy.execute([cols[j] for j in o], rho[i], wall[i]) for i, o in zip(test, orders)]


def evaluate(dirs: list[Path], base_cfgs: list[str], cand_cfgs: list[str], verdict: str = 'official',
             repeats: int = 5, folds: int = 5, random_draws: int = 20) -> dict:
    configs = base_cfgs + cand_cfgs
    ds = dataset_from(dirs, configs, verdict)
    rho, wall, groups = ds['rho'], ds['wall'], ds['groups']
    X = policy.design(ds['feats'], F.FEATURE_SETS['agri'])
    keep = np.where(ds['solvable'])[0]
    base = list(range(len(base_cfgs)))
    cand = list(range(len(base_cfgs), len(configs)))
    uniq = np.array(sorted(set(groups[keep])))
    per = {}                                    # (rule, m) → per-instance accumulators
    picks = {}
    control = {'first': np.zeros(len(rho)), 'within': {m: np.zeros(len(rho)) for m in CAPACITIES}, 'rho': np.zeros(len(rho))}
    n_eval = np.zeros(len(rho))
    for rep in range(repeats):
        rng = np.random.default_rng(3000 + rep)
        fold_of = {g: f for g, f in zip(rng.permutation(uniq), np.arange(len(uniq)) % folds)}
        for f in range(folds):
            train = np.array([i for i in keep if fold_of[groups[i]] != f])
            test = np.array([i for i in keep if fold_of[groups[i]] == f])
            n_eval[test] += 1
            res0, seq0 = _static_first(rho, wall, train, test, base)
            for i, e in zip(test, res0):
                control['first'][i] += e['first_feasible']
                control['rho'][i] += e['rho']
                for m in CAPACITIES:
                    control['within'][m][i] += policy.execute(seq0, rho[i], wall[i], tries=1 + m)['feasible']
            for m in CAPACITIES:
                for rule in RULES:
                    draws = random_draws if rule == 'random' else 1
                    acc = per.setdefault((rule, m), {'first': np.zeros(len(rho)), 'rho': np.zeros(len(rho)), 'tries': np.zeros(len(rho)),
                                                     'logit_first': np.zeros(len(rho)), 'logit_rho': np.zeros(len(rho))})
                    for d in range(draws):
                        sel = select(rule, m, rho, wall, groups, train, base, cand, np.random.default_rng(10_000 * rep + 100 * f + d))
                        picks.setdefault((rule, m), []).append([configs[c] for c in sel])
                        cols = base + sel
                        res, _ = _static_first(rho, wall, train, test, cols)
                        lres = _logit_eval(rho, wall, X, train, test, cols)
                        for i, e, le in zip(test, res, lres):
                            acc['first'][i] += e['first_feasible'] / draws
                            acc['rho'][i] += e['rho'] / draws
                            acc['tries'][i] += e['tries'] / draws
                            acc['logit_first'][i] += le['first_feasible'] / draws
                            acc['logit_rho'][i] += le['rho'] / draws
    k = keep
    norm = n_eval[k]

    def field_ci(values):
        by = {}
        for i, v in zip(k, values):
            by.setdefault(groups[i], []).append(v)
        means = np.array([np.mean(v) for v in by.values()])
        rngb = np.random.default_rng(7)
        boots = [rngb.choice(means, len(means), replace=True).mean() for _ in range(2000)]
        return float(means.mean()), [float(x) for x in np.percentile(boots, [2.5, 97.5])]

    out = {'verdict': verdict, 'instances': int(len(k)), 'unverifiable_cells': ds['unverifiable_cells'], 'base': base_cfgs,
           'candidates': cand_cfgs, 'repeats': repeats, 'folds': folds,
           'control_L0': {'first_try': field_ci(control['first'][k] / norm), 'stop_rho': field_ci(control['rho'][k] / norm),
                          **{f'within_{1 + m}_tries': field_ci(control['within'][m][k] / norm) for m in CAPACITIES}},
           'rules': {}}
    for (rule, m), acc in per.items():
        sel_lists = picks[(rule, m)]
        freq = {}
        for s in sel_lists:
            for c in s:
                freq[c] = freq.get(c, 0) + 1
        out['rules'][f'{rule}@{m}'] = {
            'static_first_try': field_ci(acc['first'][k] / norm), 'static_stop_rho': field_ci(acc['rho'][k] / norm),
            'static_tries': field_ci(acc['tries'][k] / norm), 'logit_first_try': field_ci(acc['logit_first'][k] / norm),
            'logit_stop_rho': field_ci(acc['logit_rho'][k] / norm),
            'selection_frequency': dict(sorted(freq.items(), key=lambda kv: -kv[1])), 'selections': len(sel_lists)}
    pn = np.maximum(n_eval, 1.0)
    out['per_instance'] = {
        'groups': [groups[i] for i in k],
        'first': {f'{rule}@{m}': (per[(rule, m)]['first'] / pn)[k].tolist() for (rule, m) in per},
        'logit_first': {f'{rule}@{m}': (per[(rule, m)]['logit_first'] / pn)[k].tolist() for (rule, m) in per},
        'rho': {f'{rule}@{m}': (per[(rule, m)]['rho'] / pn)[k].tolist() for (rule, m) in per},
        'control_first': (control['first'] / pn)[k].tolist(), 'control_rho': (control['rho'] / pn)[k].tolist()}
    return out


def _field_means(values, groups) -> np.ndarray:
    by = {}
    for g, v in zip(groups, values):
        by.setdefault(g, []).append(float(v))
    return np.array([np.mean(v) for v in by.values()])


def compare(result: dict, B: int = 2000) -> dict:
    """Per-field paired rule comparisons (two-sided Wilcoxon, bootstrap CI, Holm within metric × capacity)
    and the Jaccard overlap of each rule's top-m selected members."""
    groups = result['per_instance']['groups']
    out = {'families': [], 'selection_overlap': {}}
    for metric in ('first', 'logit_first', 'rho'):
        for m in CAPACITIES:
            names = [f'{rule}@{m}' for rule in RULES] + ['control']
            vals = {}
            for n in names:
                if n == 'control':
                    vals[n] = result['per_instance']['control_rho' if metric == 'rho' else 'control_first']
                else:
                    vals[n] = result['per_instance'][metric][n]
            means = {n: _field_means(vals[n], groups) for n in names}
            recs = []
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    d = means[a] - means[b]
                    nz = d[np.abs(d) > 1e-12]
                    p = float(stats.wilcoxon(nz).pvalue) if len(nz) >= 10 else None
                    rng = np.random.default_rng(7)
                    boots = [rng.choice(d, len(d), replace=True).mean() for _ in range(B)]
                    recs.append({'a': a, 'b': b, 'mean_diff': float(d.mean()),
                                 'ci95': [float(x) for x in np.percentile(boots, [2.5, 97.5])],
                                 'p_two_sided': p, 'fields': int(len(d)), 'nonzero': int(len(nz))})
            raw = {f"{r['a']}|{r['b']}": r['p_two_sided'] for r in recs}
            holm = _holm(raw)
            for r in recs:
                r['p_holm'] = holm[f"{r['a']}|{r['b']}"]
            out['families'].append({'metric': metric, 'capacity': m, 'comparisons': recs})
    for m in CAPACITIES:
        top = {}
        for rule in RULES:
            freq = result['rules'][f'{rule}@{m}']['selection_frequency']
            top[rule] = set(list(freq)[:m])
        for i, a in enumerate(RULES):
            for b in RULES[i + 1:]:
                out['selection_overlap'][f'{a}|{b}@{m}'] = {
                    'jaccard': len(top[a] & top[b]) / max(1, len(top[a] | top[b])),
                    'a': sorted(top[a]), 'b': sorted(top[b])}
    return out


def write(dirs: list[Path], base_cfgs: list[str], cand_cfgs: list[str], out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {v: evaluate(dirs, base_cfgs, cand_cfgs, v) for v in ('official', 'fillet')}
    for v in res:
        res[v]['pairwise'] = compare(res[v])
    (out_dir / 'admission_rules.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    return {v: {k: (r['static_first_try'][0], list(r['selection_frequency'])[:3]) for k, r in res[v]['rules'].items()} for v in res}
