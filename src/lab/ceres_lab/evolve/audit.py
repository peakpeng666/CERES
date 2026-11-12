"""Complementarity audit: how much per-instance selection could gain over the single best configuration.

For each problem family the audit builds a performance matrix ρ[i, a] = cost(i, a) / VBS-cost(i) for feasible
outcomes (ρ = 1 is the per-instance best) and a penalty P for any non-feasible terminal state (no solution,
error, timeout, crash). Instances that no configuration solves are counted but excluded from ρ means. Reported:
feasible counts with full denominators, SBS (lowest mean penalised ρ), VBS, headroom, a greedy static try-order
curve, exact Shapley contributions to the VBS (Fréchette et al., AAAI 2016), slices, and field-grouped bootstrap
intervals. Coverage cost is path length per covered working hectare, so a shorter path that covers less is not
rewarded; fleet cost is the solver objective (metres or seconds) of independently checked routes.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from . import features as F
from . import instances as I
from . import plans
from .sweep import load_records

PENALTY = 2.0
PENALTY_SENSITIVITY = (1.5, 2.0, 3.0, 10.0)
BOOTSTRAP = 2000


def coverage_cost(rec: dict) -> float | None:
    """Path length per covered working hectare (m/ha) of an independently valid coverage path."""
    if rec.get('status') != 'feasible' or not rec.get('valid'):
        return None
    m = rec.get('metrics') or {}
    covered = (m.get('working_area_fraction') or 0) * (m.get('working_area_m2') or 0)
    length = rec.get('objective')
    if not covered or length is None or not math.isfinite(length):
        return None
    return float(length) / (covered / 1e4)


def fleet_cost(rec: dict) -> float | None:
    if rec.get('status') not in ('feasible', 'optimal') or not rec.get('valid'):
        return None
    v = rec.get('objective')
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def wall(rec: dict) -> float:
    m = rec.get('metrics') or {}
    return float(m.get('wall_s') or rec.get('worker_wall_s') or rec.get('supervisor_wall_s') or 0.0)


def matrix(records: list[dict], family: str, configs: list[str]) -> dict:
    """Instances × configs cost/feasibility/wall arrays; instance keys sorted for determinism."""
    cost_fn = coverage_cost if family == 'coverage' else fleet_cost
    cells, meta = {}, {}
    for r in records:
        key = json.dumps(r['instance'], sort_keys=True)
        cells[(key, r['config'])] = r
        meta[key] = r['instance']
    keys = sorted(meta)
    n, k = len(keys), len(configs)
    cost = np.full((n, k), np.nan)
    status = np.empty((n, k), dtype=object)
    walls = np.zeros((n, k))
    for i, key in enumerate(keys):
        for j, c in enumerate(configs):
            r = cells.get((key, c))
            if r is None:
                status[i, j] = 'missing'
                continue
            status[i, j] = r.get('status')
            walls[i, j] = wall(r)
            v = cost_fn(r)
            if v is not None:
                cost[i, j] = v
    return {'keys': keys, 'meta': [meta[k] for k in keys], 'configs': configs, 'cost': cost, 'status': status, 'wall': walls}


def rho(cost: np.ndarray, penalty: float = PENALTY) -> tuple[np.ndarray, np.ndarray]:
    """Normalised ratio matrix (penalised) and the mask of instances some configuration solves."""
    solvable = np.isfinite(cost).any(axis=1)
    best = np.where(solvable, np.nanmin(np.where(np.isfinite(cost), cost, np.inf), axis=1), np.nan)
    r = cost / best[:, None]
    r = np.where(np.isfinite(r), r, penalty)
    return r, solvable


def shapley(r: np.ndarray, penalty: float) -> np.ndarray:
    """Exact Shapley values of v(S) = mean_i [P − min_{a∈S} ρ_ia] (v(∅) = 0); configs ≤ 20.

    v is built for all 2^k subsets by doubling (subsets with bit b = subsets without b, combined with column b)."""
    n_inst, k = r.shape
    if k > 20:
        raise ValueError('too many configurations for exact Shapley values')
    v = np.zeros(1)
    mins = np.full((1, n_inst), penalty)
    for b in range(k):
        with_b = np.minimum(mins, r[:, b][None, :])
        mins = np.vstack([mins, with_b])
        v = np.concatenate([v, (penalty - with_b).mean(axis=1)])
    masks = np.arange(1 << k)
    sizes = np.array([bin(m).count('1') for m in masks])
    fact = np.array([math.factorial(i) for i in range(k + 1)], dtype=float)
    phi = np.zeros(k)
    for a in range(k):
        bit = 1 << a
        idx = masks[(masks & bit) == 0]
        s = sizes[idx]
        phi[a] = (fact[s] * fact[k - s - 1] / fact[k] * (v[idx | bit] - v[idx])).sum()
    return phi


def greedy_order(r: np.ndarray, penalty: float) -> list[int]:
    """Static try-order: each next configuration minimises the mean best-so-far penalised ρ."""
    k = r.shape[1]
    order, best = [], np.full(r.shape[0], penalty)
    for _ in range(k):
        cand = [(np.minimum(best, r[:, j]).mean(), j) for j in range(k) if j not in order]
        val, j = min(cand)
        order.append(j)
        best = np.minimum(best, r[:, j])
    return order


def _bootstrap(groups: list[str], stat, rng: np.random.Generator) -> tuple[float, float]:
    uniq = sorted(set(groups))
    idx = defaultdict(list)
    for i, g in enumerate(groups):
        idx[g].append(i)
    vals = []
    for _ in range(BOOTSTRAP):
        pick = rng.choice(len(uniq), len(uniq), replace=True)
        rows = [i for p in pick for i in idx[uniq[p]]]
        vals.append(stat(np.array(rows)))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def family_summary(m: dict, groups: list[str], default: str | None = None, penalty: float = PENALTY) -> dict:
    cost, configs = m['cost'], m['configs']
    r, solvable = rho(cost, penalty)
    feas = np.isfinite(cost)
    rs = r[solvable]
    n_all, n_solv = len(m['keys']), int(solvable.sum())
    mean_r = rs.mean(axis=0) if n_solv else np.full(len(configs), np.nan)
    sbs = int(np.argmin(mean_r)) if n_solv else None
    rows = []
    for j, c in enumerate(configs):
        own = cost[:, j][np.isfinite(cost[:, j])]
        states = defaultdict(int)
        for s in m['status'][:, j]:
            states[s] += 1
        rows.append({'config': c, 'label': plans.config_label(c), 'feasible': int(feas[:, j].sum()), 'instances': n_all,
                     'mean_rho_penalised': float(mean_r[j]) if n_solv else None,
                     'mean_rho_own_feasible': float(r[feas[:, j], j].mean()) if feas[:, j].any() else None,
                     'wins': int(((rs[:, j] <= 1 + 1e-9)).sum()) if n_solv else 0,
                     'median_wall_s': float(np.median(m['wall'][:, j])), 'p95_wall_s': float(np.percentile(m['wall'][:, j], 95)),
                     'states': dict(states), 'mean_cost_own_feasible': float(own.mean()) if own.size else None})
    out = {'instances': n_all, 'solvable': n_solv, 'unsolved': n_all - n_solv, 'penalty': penalty, 'configs': rows}
    if sbs is None:
        return out
    rng = np.random.default_rng(20261023)
    g_solv = [g for g, s in zip(groups, solvable) if s]
    sbs_feas = int(feas[:, sbs].sum())
    out['sbs'] = {'config': configs[sbs], 'label': plans.config_label(configs[sbs]), 'feasible': sbs_feas,
                  'mean_rho_penalised': float(mean_r[sbs])}
    out['vbs'] = {'feasible': n_solv, 'mean_rho_penalised': 1.0}
    out['headroom'] = {
        'rho': float(mean_r[sbs] - 1.0),
        'rho_ci95': _bootstrap(g_solv, lambda rows: rs[rows, sbs].mean() - 1.0, rng),
        'feasible': n_solv - sbs_feas, 'feasible_share': (n_solv - sbs_feas) / n_all,
        'instances_sbs_not_best': int((rs[:, sbs] > 1 + 1e-9).sum()),
        'sbs_infeasible_but_solvable': int((~feas[:, sbs] & solvable).sum())}
    order = greedy_order(rs, penalty)
    best, curve = np.full(n_solv, penalty), []
    for t, j in enumerate(order):
        best = np.minimum(best, rs[:, j])
        curve.append({'tries': t + 1, 'config': configs[j], 'label': plans.config_label(configs[j]),
                      'feasible': int((best < penalty).sum()), 'mean_rho': float(best.mean())})
    out['greedy_static_order'] = curve
    phi = shapley(rs, penalty)
    full = (penalty - rs.min(axis=1)).mean()
    out['shapley'] = [{'config': c, 'label': plans.config_label(c), 'shapley': float(p),
                       'marginal': float(full - (penalty - np.delete(rs, j, axis=1).min(axis=1)).mean()) if len(configs) > 1 else float(full)}
                      for j, (c, p) in enumerate(zip(configs, phi))]
    out['sensitivity'] = []
    for p in PENALTY_SENSITIVITY:
        rp, sv = rho(cost, p)
        mp_ = rp[sv].mean(axis=0)
        b = int(np.argmin(mp_))
        out['sensitivity'].append({'penalty': p, 'sbs': configs[b], 'headroom_rho': float(mp_[b] - 1.0)})
    if default in configs:
        d = configs.index(default)
        out['workbench_default'] = {'config': default, 'label': plans.config_label(default), 'feasible': int(feas[:, d].sum()),
                                    'mean_rho_penalised': float(mean_r[d]),
                                    'gap_to_vbs_rho_ci95': _bootstrap(g_solv, lambda rows: rs[rows, d].mean() - 1.0, rng)}
    return out


def coverage_slices(m: dict, sbs: str, penalty: float = PENALTY) -> list[dict]:
    """Pre-specified slices: profile, near-convex vs concave field, area tercile, estimated turn-share tercile."""
    feats = []
    for spec in m['meta']:
        _sc, inst = I.coverage_instance(spec['field'], spec['profile'])
        feats.append(F.coverage_features(inst['field'], inst['obstacles'], inst['swath_width'], inst['turn_radius'], inst['headland_width']))
    r, solvable = rho(m['cost'], penalty)
    feas = np.isfinite(m['cost'])
    j = m['configs'].index(sbs)
    area = np.array([f['area_ha'] for f in feats])
    turn = np.array([f['turn_share_est'] for f in feats])
    a_cut, t_cut = np.percentile(area, [100 / 3, 200 / 3]), np.percentile(turn, [100 / 3, 200 / 3])
    labels = {
        'profile': [s['profile'] for s in m['meta']],
        'shape': ['近凸（solidity≥0.97）' if f['solidity'] >= 0.97 else '凹形（solidity<0.97）' for f in feats],
        'area_tercile': [['小', '中', '大'][int(np.searchsorted(a_cut, a, side='right'))] for a in area],
        'turn_share_tercile': [['低', '中', '高'][int(np.searchsorted(t_cut, t, side='right'))] for t in turn],
    }
    out = []
    for dim, labs in labels.items():
        for lab in sorted(set(labs)):
            rows = np.array([x == lab for x in labs])
            sv = rows & solvable
            out.append({'dimension': dim, 'slice': lab, 'instances': int(rows.sum()), 'solvable': int(sv.sum()),
                        'sbs_feasible': int((feas[:, j] & rows).sum()),
                        'sbs_mean_rho': float(r[sv, j].mean()) if sv.any() else None,
                        'best_single_in_slice': m['configs'][int(np.argmin(r[sv].mean(axis=0)))] if sv.any() else None,
                        'best_single_mean_rho': float(r[sv].mean(axis=0).min()) if sv.any() else None})
    return out


FAILURE_KINDS = (  # checker / adapter message fragment → category (first match wins)
    ('采样三点曲率', 'curvature'), ('越出田块', 'footprint'), ('覆盖率低于', 'coverage<90%'),
    ('仅支持单个凸内缩', 'single_cell_only'), ('折返尖点', 'cusp'), ('没有生成路径', 'no_path'),
    ('没有返回转弯', 'no_turn'), ('不连续', 'discontinuous'))


def failure_taxonomy(records: list[dict]) -> list[dict]:
    """Count failure categories per configuration (a record may carry several checker messages)."""
    table: dict = defaultdict(lambda: defaultdict(int))
    for r in records:
        if r.get('status') == 'feasible' and r.get('valid'):
            continue
        cfg = r['config']
        if r.get('status') in ('timeout', 'worker_crash', 'error'):
            table[cfg][r['status']] += 1
            continue
        kinds = {next((k for frag, k in FAILURE_KINDS if frag in e), 'other') for e in r.get('errors') or ['']}
        for k in kinds:
            table[cfg][k] += 1
        table[cfg]['records'] += 1
    return [{'config': c, 'label': plans.config_label(c), **dict(v)} for c, v in sorted(table.items())]


def cvrp_archive(path: Path) -> dict:
    """The frozen 4505-run CVRP baseline: per case×seed ratios to the best configuration of that run."""
    rows = list(csv.DictReader(path.open(encoding='utf-8')))
    configs = sorted({r['algorithm'] for r in rows})
    keys = sorted({(r['case_id'], int(r['seed'])) for r in rows})
    index = {k: i for i, k in enumerate(keys)}
    cost = np.full((len(keys), len(configs)), np.nan)
    status = np.empty(cost.shape, dtype=object)
    walls = np.zeros(cost.shape)
    for r in rows:
        i, j = index[(r['case_id'], int(r['seed']))], configs.index(r['algorithm'])
        status[i, j] = r['status']
        walls[i, j] = float(r['cpu_seconds'] or 0)
        if r['feasible'] == 'True':
            cost[i, j] = float(r['cost'])
    m = {'keys': [f'{c}#s{s}' for c, s in keys], 'meta': [{'case': c, 'seed': s} for c, s in keys], 'configs': configs,
         'cost': cost, 'status': status, 'wall': walls}
    summary = family_summary(m, [c for c, _s in keys])
    summary['source'] = str(path)
    summary['note'] = '53 问题 × 5 种子；每个种子视为一次实例运行；53 问题均为已污染开发数据'
    return summary


def write(out: Path) -> dict:
    out = Path(out)
    plan = json.loads((out / 'plan.json').read_text(encoding='utf-8'))
    records = load_records(out)
    report = {'run_id': plan['run_id'], 'records': len(records), 'planned': len(plan['jobs']), 'families': {}}
    tables = out / 'audit'
    tables.mkdir(exist_ok=True)
    cov = [r for r in records if r['family'] == 'coverage']
    if cov:
        m = matrix(cov, 'coverage', list(plans.COVERAGE_PORTFOLIO))
        groups = [s['field'] for s in m['meta']]
        s = family_summary(m, groups, plans.WORKBENCH_DEFAULT)
        if 'sbs' in s:
            s['slices'] = coverage_slices(m, s['sbs']['config'])
        s['failure_taxonomy'] = failure_taxonomy(cov)
        report['families']['coverage'] = s
        _matrix_csv(tables / 'coverage_matrix.csv', m)
    fleet = [r for r in records if r['family'] == 'fleet']
    for objective in ('total_distance', 'makespan'):
        sub = [r for r in fleet if r['instance']['objective'] == objective]
        if sub:
            m = matrix(sub, 'fleet', list(plans.FLEET_PORTFOLIO))
            report['families'][f'fleet:{objective}'] = family_summary(m, [s['field'] for s in m['meta']])
            _matrix_csv(tables / f'fleet_{objective}_matrix.csv', m)
    archive = Path(__file__).resolve().parents[4] / 'experiments/baselines/cvrp/results/runs.csv'
    if archive.is_file():
        report['families']['cvrp_archive'] = cvrp_archive(archive)
    (tables / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    return {k: {kk: v.get(kk) for kk in ('instances', 'solvable', 'sbs', 'headroom', 'workbench_default')}
            for k, v in report['families'].items()}


def _matrix_csv(path: Path, m: dict):
    with path.open('w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['instance', 'config', 'status', 'cost', 'wall_s'])
        for i, key in enumerate(m['keys']):
            for j, c in enumerate(m['configs']):
                v = m['cost'][i, j]
                w.writerow([key, c, m['status'][i, j], '' if not np.isfinite(v) else f'{v:.6f}', f"{m['wall'][i, j]:.4f}"])
