"""Try-order policies for coverage configuration selection, evaluated without touching sealed test fields.

A policy maps an instance's features to an ordered list of configurations. Execution tries them in order; the
project's independent checker decides feasibility, so the first feasible path is accepted (``stop``), or a policy
may spend a fixed number of tries and keep the best (``best-of-T``). Policies:

- baselines: ``default`` (workbench default config, one shot), ``sbs`` (training-best single config),
  ``static`` (training-fitted greedy order, the same for every instance), ``random``, ``oracle`` (VBS);
- learned, each with the size-only control features and the agricultural feature set: ``knn1``
  (nearest training instance), ``knn5`` (inverse-distance weighted 5 neighbours), ``logit``
  (per-configuration L2 logistic feasibility model; order by expected penalised ratio).

Evaluation: field-grouped 5-fold cross-validation repeated 10 times (no field appears in training and test of
the same fit), per-field paired Wilcoxon tests with Hodges–Lehmann effects and field bootstrap intervals, and a
prequential stream (test-then-train over a random field order, 20 orders) that measures learning from experience.
Regularisation and k are fixed in advance; nothing is tuned on held-out folds.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy import optimize, stats

from . import audit
from . import features as F
from . import instances as I
from . import plans
from .sweep import load_records

PENALTY = audit.PENALTY
LOG_FEATURES = {'area_ha', 'perimeter_m', 'mrr_long_m', 'mrr_short_m', 'vertices_s', 'reflex_s', 'swaths_est',
                'swath_len_over_turn_diameter', 'field_short_over_swath', 'elongation', 'holes', 'inner_reflex_s',
                'uturn_over_swath', 'headland_over_turn_diameter', 'turn_radius_over_swath'}
L2 = 1.0
REPEATS, FOLDS, ORDERS = 10, 5, 20
COLD_START = 10          # fields of experience before a learned policy replaces the cold-start order
LEARNED = ('knn1', 'knn5', 'logit')


# ------------------------------------------------------------------ data

def dataset(out: Path) -> dict:
    records = [r for r in load_records(out) if r['family'] == 'coverage']
    m = audit.matrix(records, 'coverage', list(plans.COVERAGE_PORTFOLIO))
    feats = []
    for spec in m['meta']:
        _sc, inst = I.coverage_instance(spec['field'], spec['profile'])
        feats.append(F.coverage_features(inst['field'], inst['obstacles'], inst['swath_width'], inst['turn_radius'], inst['headland_width']))
    r, solvable = audit.rho(m['cost'], PENALTY)
    return {'m': m, 'rho': r, 'solvable': solvable, 'feats': feats, 'groups': np.array([s['field'] for s in m['meta']]),
            'profiles': np.array([s['profile'] for s in m['meta']]), 'configs': m['configs'], 'wall': m['wall']}


def design(feats: list[dict], names) -> np.ndarray:
    X = np.array([[f[k] for k in names] for f in feats], dtype=float)
    for j, k in enumerate(names):
        if k in LOG_FEATURES:
            X[:, j] = np.log1p(np.maximum(X[:, j], 0.0))
    return X


class Scaler:
    def __init__(self, X):
        self.mu = X.mean(axis=0)
        sd = X.std(axis=0)
        self.sd = np.where(sd > 1e-12, sd, 1.0)

    def __call__(self, X):
        return (X - self.mu) / self.sd


# ------------------------------------------------------------------ policies

def greedy(rho_tr: np.ndarray) -> list[int]:
    return audit.greedy_order(rho_tr, PENALTY)


def _logit_fit(X, y):
    """L2-regularised logistic regression (intercept unpenalised); constant model when y is constant."""
    n, d = X.shape
    if y.min() == y.max():
        return {'const': (y.sum() + 0.5) / (n + 1.0)}
    Xb = np.hstack([np.ones((n, 1)), X])

    def loss(w):
        z = Xb @ w
        ll = np.logaddexp(0, z) - y * z
        return ll.sum() + 0.5 * L2 * (w[1:] @ w[1:])

    def grad(w):
        p = 1 / (1 + np.exp(-(Xb @ w)))
        g = Xb.T @ (p - y)
        g[1:] += L2 * w[1:]
        return g
    res = optimize.minimize(loss, np.zeros(d + 1), jac=grad, method='L-BFGS-B')
    return {'w': res.x.tolist()}


def _logit_prob(model, X):
    if 'const' in model:
        return np.full(len(X), model['const'])
    w = np.array(model['w'])
    return 1 / (1 + np.exp(-(np.hstack([np.ones((len(X), 1)), X]) @ w)))


def fit(kind: str, X: np.ndarray, rho_tr: np.ndarray) -> dict:
    """Train one policy on training rows (X unscaled design matrix, rho_tr penalised ratios)."""
    model = {'kind': kind, 'static': greedy(rho_tr), 'sbs': int(np.argmin(rho_tr.mean(axis=0)))}
    if kind in LEARNED:
        sc = Scaler(X)
        model['scaler'] = sc
        Z = sc(X)
        if kind in ('knn1', 'knn5'):
            model.update(Z=Z, rho=rho_tr)
        else:
            feas = rho_tr < PENALTY
            model['logit'] = [_logit_fit(Z, feas[:, j].astype(float)) for j in range(rho_tr.shape[1])]
            model['mu'] = np.array([rho_tr[feas[:, j], j].mean() if feas[:, j].any() else PENALTY for j in range(rho_tr.shape[1])])
    return model


def order(model: dict, X: np.ndarray, rng: np.random.Generator | None = None) -> list[list[int]]:
    """Try-order per row of X (ties broken by the static order so policies differ only through learning)."""
    k = len(model['static'])
    rank = {j: t for t, j in enumerate(model['static'])}
    kind = model['kind']
    if kind == 'static':
        return [list(model['static']) for _ in range(len(X))]
    if kind == 'sbs':
        return [[model['sbs']] + [j for j in model['static'] if j != model['sbs']] for _ in range(len(X))]
    if kind == 'random':
        return [list(rng.permutation(k)) for _ in range(len(X))]
    Z = model['scaler'](X)
    out = []
    if kind in ('knn1', 'knn5'):
        nn = 1 if kind == 'knn1' else 5
        for z in Z:
            d = np.sqrt(((model['Z'] - z) ** 2).sum(axis=1))
            idx = np.argsort(d, kind='stable')[:nn]
            w = 1.0 / (d[idx] + 1e-9)
            score = (model['rho'][idx] * w[:, None]).sum(axis=0) / w.sum()
            out.append(sorted(range(k), key=lambda j: (score[j], rank[j])))
        return out
    probs = np.column_stack([_logit_prob(mj, Z) for mj in model['logit']])
    ev = probs * model['mu'][None, :] + (1 - probs) * PENALTY
    return [sorted(range(k), key=lambda j: (ev[i, j], rank[j])) for i in range(len(Z))]


def execute(order_i: list[int], rho_i: np.ndarray, wall_i: np.ndarray, tries: int | None = None, best_of: bool = False) -> dict:
    """Outcome of following a try-order on one instance with recorded results (no solver call)."""
    spent, best, used = 0.0, PENALTY, 0
    for t, j in enumerate(order_i[: tries or len(order_i)]):
        spent += wall_i[j]
        used = t + 1
        if rho_i[j] < PENALTY:
            best = min(best, rho_i[j])
            if not best_of:
                break
    return {'rho': best, 'feasible': best < PENALTY, 'tries': used, 'wall_s': spent,
            'first_feasible': bool(rho_i[order_i[0]] < PENALTY), 'first_rho': float(rho_i[order_i[0]])}


def _policy_names(feature_sets) -> list[str]:
    return ['default', 'sbs', 'static', 'random', 'oracle'] + [f'{k}:{fs}' for k in LEARNED for fs in feature_sets]


def _orders_for(name: str, model_cache: dict, X_by_fs: dict, rows, rho, rng, default_idx: int):
    if name == 'default':
        k = rho.shape[1]
        return [[default_idx] + [j for j in range(k) if j != default_idx] for _ in rows]
    if name == 'oracle':
        return [list(np.argsort(rho[i], kind='stable')) for i in rows]
    kind, _, fs = name.partition(':')
    return order(model_cache[name], X_by_fs[fs or 'size'][rows], rng)


def crossval(ds: dict, feature_sets=('size', 'agri')) -> dict:
    """Field-grouped repeated K-fold; per-instance outcomes averaged over repeats."""
    rho, wall = ds['rho'], ds['wall']
    keep = np.where(ds['solvable'])[0]
    groups = ds['groups']
    X_by_fs = {fs: design(ds['feats'], F.FEATURE_SETS[fs]) for fs in feature_sets}
    default_idx = ds['configs'].index(plans.WORKBENCH_DEFAULT)
    names = _policy_names(feature_sets)
    acc = {n: {'rho': np.zeros(len(rho)), 'first': np.zeros(len(rho)), 'tries': np.zeros(len(rho)), 'wall': np.zeros(len(rho)),
               'first_rho': np.zeros(len(rho))} for n in names}
    uniq = np.array(sorted(set(groups[keep])))
    for rep in range(REPEATS):
        rng = np.random.default_rng(1000 + rep)
        fold_of = {g: f for g, f in zip(rng.permutation(uniq), np.arange(len(uniq)) % FOLDS)}
        for fold in range(FOLDS):
            test = np.array([i for i in keep if fold_of[groups[i]] == fold])
            train = np.array([i for i in keep if fold_of[groups[i]] != fold])
            cache = {'sbs': fit('sbs', None, rho[train]), 'static': fit('static', None, rho[train]),
                     'random': fit('static', None, rho[train]) | {'kind': 'random'}}
            for kind in LEARNED:
                for fs in feature_sets:
                    cache[f'{kind}:{fs}'] = fit(kind, X_by_fs[fs][train], rho[train])
            for n in names:
                orders = _orders_for(n, cache, X_by_fs, test, rho, rng, default_idx)
                for i, o in zip(test, orders):
                    e = execute(o, rho[i], wall[i])
                    a = acc[n]
                    a['rho'][i] += e['rho'] / REPEATS
                    a['first'][i] += e['first_feasible'] / REPEATS
                    a['tries'][i] += e['tries'] / REPEATS
                    a['wall'][i] += e['wall_s'] / REPEATS
                    a['first_rho'][i] += e['first_rho'] / REPEATS
    summary = {}
    for n in names:
        a = acc[n]
        summary[n] = {'first_try_feasible': float(a['first'][keep].mean()), 'one_shot_rho': float(a['first_rho'][keep].mean()),
                      'stop_rho': float(a['rho'][keep].mean()), 'tries': float(a['tries'][keep].mean()),
                      'wall_s': float(a['wall'][keep].mean())}
    return {'policies': summary, 'per_instance': acc, 'keep': keep, 'names': names}


def paired(cv: dict, groups, a: str, b: str, metric: str, better: str = 'lower') -> dict:
    """Per-field paired comparison of policy a vs b (field means over profiles); Wilcoxon + HL + bootstrap."""
    keep = cv['keep']
    va, vb = cv['per_instance'][a][metric], cv['per_instance'][b][metric]
    by = {}
    for i in keep:
        by.setdefault(groups[i], []).append(va[i] - vb[i])
    d = np.array([np.mean(v) for v in by.values()])
    nz = d[np.abs(d) > 1e-12]
    test = stats.wilcoxon(nz, alternative='less' if better == 'lower' else 'greater') if len(nz) >= 10 else None
    walsh = (d[:, None] + d[None, :])[np.triu_indices(len(d))] / 2
    rng = np.random.default_rng(7)
    boots = [rng.choice(d, len(d), replace=True).mean() for _ in range(audit.BOOTSTRAP)]
    return {'a': a, 'b': b, 'metric': metric, 'fields': int(len(d)), 'nonzero': int(len(nz)), 'mean_diff': float(d.mean()),
            'hodges_lehmann': float(np.median(walsh)), 'bootstrap_ci95': [float(x) for x in np.percentile(boots, [2.5, 97.5])],
            'wilcoxon_p_one_sided': float(test.pvalue) if test is not None else None,
            'wins_losses_ties': [int((d < -1e-12).sum()), int((d > 1e-12).sum()), int((np.abs(d) <= 1e-12).sum())]
            if better == 'lower' else [int((d > 1e-12).sum()), int((d < -1e-12).sum()), int((np.abs(d) <= 1e-12).sum())]}


def prequential(ds: dict, feature_sets=('size', 'agri')) -> dict:
    """Test-then-train over random field orders: cumulative regret (stop ρ − 1) and first-try failures."""
    rho, wall, groups = ds['rho'], ds['wall'], ds['groups']
    keep = np.where(ds['solvable'])[0]
    fields = sorted(set(groups[keep]))
    rows_of = {g: [i for i in keep if groups[i] == g] for g in fields}
    X_by_fs = {fs: design(ds['feats'], F.FEATURE_SETS[fs]) for fs in feature_sets}
    default_idx = ds['configs'].index(plans.WORKBENCH_DEFAULT)
    k = rho.shape[1]
    cold = [default_idx] + [j for j in range(k) if j != default_idx]
    names = ['default', 'static-online'] + [f'{kind}:{fs}' for kind in LEARNED for fs in feature_sets] + ['oracle']
    curves = {n: [] for n in names}
    for o in range(ORDERS):
        rng = np.random.default_rng(2000 + o)
        seq = list(rng.permutation(fields))
        cum = {n: {'regret': 0.0, 'first_fail': 0, 'wall': 0.0} for n in names}
        trace = {n: [] for n in names}
        seen: list[int] = []
        for t, g in enumerate(seq):
            rows = rows_of[g]
            enough = t >= COLD_START
            models = {}
            if enough:
                models['static-online'] = fit('static', None, rho[seen])
                for kind in LEARNED:
                    for fs in feature_sets:
                        models[f'{kind}:{fs}'] = fit(kind, X_by_fs[fs][seen], rho[seen])
            for n in names:
                if n == 'oracle':
                    ords = [list(np.argsort(rho[i], kind='stable')) for i in rows]
                elif n == 'default' or not enough:
                    ords = [cold for _ in rows]
                elif n == 'static-online':
                    ords = [list(models[n]['static']) for _ in rows]
                else:
                    fs = n.split(':')[1]
                    ords = order(models[n], X_by_fs[fs][rows], rng)
                for i, ordi in zip(rows, ords):
                    e = execute(ordi, rho[i], wall[i])
                    cum[n]['regret'] += e['rho'] - 1.0
                    cum[n]['first_fail'] += 0 if e['first_feasible'] else 1
                    cum[n]['wall'] += e['wall_s']
                trace[n].append([cum[n]['regret'], cum[n]['first_fail'], cum[n]['wall']])
            seen += rows
        for n in names:
            curves[n].append(trace[n])
    out = {}
    for n in names:
        arr = np.array(curves[n])                      # orders × fields × 3
        out[n] = {'mean': arr.mean(axis=0).tolist(), 'p10': np.percentile(arr, 10, axis=0).tolist(),
                  'p90': np.percentile(arr, 90, axis=0).tolist(), 'final_mean': arr[:, -1, :].mean(axis=0).tolist()}
    return {'orders': ORDERS, 'cold_start_fields': COLD_START, 'fields': len(fields), 'instances': int(len(keep)),
            'columns': ['cumulative_regret_rho', 'cumulative_first_try_failures', 'cumulative_wall_s'], 'curves': out}


# ------------------------------------------------------------------ deployable model

def export_model(ds: dict, kind: str, fs: str, cv_summary: dict, path: Path) -> dict:
    """Fit on all development instances and write a fingerprinted model document (cf. models/cvrp-knn-v1.json).

    ``kind='static'`` exports the experience-derived fixed try-order (no per-instance model); the feature
    scaler and training cloud are still recorded because the recommender keeps its scope gates for it."""
    keep = np.where(ds['solvable'])[0]
    names = F.FEATURE_SETS[fs]
    X = design(ds['feats'], names)
    if kind == 'static':
        model = fit('static', None, ds['rho'][keep])
        sc = Scaler(X[keep])
    else:
        model = fit(kind, X[keep], ds['rho'][keep])
        sc = model['scaler']
    Z = sc(X[keep])
    # Scope: leave-one-field-out nearest-neighbour distance quantile in the scaled feature space.
    groups = ds['groups'][keep]
    nn = []
    for i in range(len(keep)):
        other = groups != groups[i]
        nn.append(np.sqrt(((Z[other] - Z[i]) ** 2).sum(axis=1)).min())
    artifact = {'kind': kind, 'feature_set': fs, 'feature_version': F.VERSION, 'features': list(names),
                'log_features': sorted(LOG_FEATURES & set(names)), 'configs': ds['configs'],
                'config_params': {c: plans.COVERAGE_PORTFOLIO[c] for c in ds['configs']},
                'scaler': {'mu': sc.mu.tolist(), 'sd': sc.sd.tolist()}, 'static_order': model['static'], 'sbs': model['sbs'],
                'penalty': PENALTY, 'profiles': I.PROFILES,
                'scope': {'nn_distance_q99': float(np.percentile(nn, 99)), 'nn_distance_max': float(np.max(nn)),
                          'feature_min': X[keep].min(axis=0).tolist(), 'feature_max': X[keep].max(axis=0).tolist()},
                'training': {'instances': int(len(keep)), 'fields': int(len(set(groups))),
                             'instance_keys_sha256': hashlib.sha256('\n'.join(ds['m']['keys'][i] for i in keep).encode()).hexdigest()}}
    artifact['train_Z'] = Z.tolist()            # scaled training features: needed by the out-of-scope gate
    artifact['train_rho_mean'] = ds['rho'][keep].mean(axis=0).tolist()
    if kind == 'logit':
        artifact['logit'] = model['logit']
        artifact['mu'] = model['mu'].tolist()
    elif kind in ('knn1', 'knn5'):
        artifact['train_rho'] = ds['rho'][keep].tolist()
        artifact['k'] = 1 if kind == 'knn1' else 5
    payload = json.dumps(artifact, sort_keys=True, separators=(',', ':')).encode()
    doc = {'status': 'evaluated', 'artifact': artifact, 'artifact_sha256': hashlib.sha256(payload).hexdigest(),
           'evaluation': cv_summary,
           'scope_note': '开发侧（荷兰田块）分组交叉验证；爱沙尼亚/立陶宛封存测试尚未运行，不作为正式结论',
           'protocol': 'docs/持续进化决策与调度.md §6；experiments/runs 中对应运行目录'}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    return doc


def write(out: Path) -> dict:
    out = Path(out)
    ds = dataset(out)
    cv = crossval(ds)
    groups = ds['groups']
    comparisons = []
    for fs in ('agri', 'size'):
        for kind in LEARNED:
            name = f'{kind}:{fs}'
            comparisons.append(paired(cv, groups, name, 'static', 'first', better='higher'))
            comparisons.append(paired(cv, groups, name, 'static', 'wall'))
            comparisons.append(paired(cv, groups, name, 'sbs', 'first_rho'))
    for kind in LEARNED:
        comparisons.append(paired(cv, groups, f'{kind}:agri', f'{kind}:size', 'first', better='higher'))
        comparisons.append(paired(cv, groups, f'{kind}:agri', f'{kind}:size', 'first_rho'))
    comparisons.append(paired(cv, groups, 'static', 'default', 'first', better='higher'))
    preq = prequential(ds)
    result = {'run_id': json.loads((out / 'plan.json').read_text(encoding='utf-8'))['run_id'],
              'instances_solvable': int(len(cv['keep'])), 'instances_all': int(len(ds['rho'])),
              'fields': int(len(set(groups[cv['keep']]))), 'configs': ds['configs'], 'penalty': PENALTY,
              'cv': {'repeats': REPEATS, 'folds': FOLDS, 'policies': cv['policies']}, 'comparisons': comparisons,
              'prequential': preq}
    target = out / 'policy'
    target.mkdir(exist_ok=True)
    (target / 'policy.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'cv': cv['policies'], 'n': result['instances_solvable']}
