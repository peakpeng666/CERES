"""Learning when to re-plan: a gate that imitates the rollout teacher so the solver runs only when it matters.

The dynamic study (experiments/runs/evolve-dynamic-20261024) found that the past-only rollout applies a
re-optimised plan at only 1.7–18 % of decisions yet matches always re-planning — but it still calls the solver at
every decision. Here a logistic gate learns, from scheduling-state features available before any solver call
(``dynamic.FEATURES``: pending tasks, insertion cost, time since the last re-plan, spatial spread, …), whether the
rollout teacher would choose to re-plan. At run time the gate calls the solver only when the predicted probability
exceeds a threshold chosen on the training fields (recall of teacher re-plans ≥ 0.8). Evaluation is field-grouped:
the gate for a stream is trained only on other fields' decisions. Planning-level simulation, constant-speed timing.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from . import dynamic as Y
from . import policy as P

RECALL = 0.8


def collect(field: str, rate: str, horizon: float, vehicles: int, seed: int, budget_s: float = 0.3) -> dict:
    s = Y.make_stream(field, seed=seed, n_tasks=40, vehicles=vehicles, travel='free', horizon_s=horizon)
    out = Y.simulate(s, Y.Rollout(samples=3, seed=seed), budget_s=budget_s, seed=42)
    rows = [{'features': d['features'], 'label': d['action'] == 'reopt',
             'gain': (d['rollout_estimate']['insert'] - d['rollout_estimate']['reopt']) / max(d['rollout_estimate']['insert'], 1e-9)}
            for d in out['decisions'] if 'rollout_estimate' in d]
    return {'field': field, 'rate': rate, 'horizon_s': horizon, 'vehicles': vehicles, 'seed': seed, 'decisions': rows}


def _job(args):
    field, rate, horizon, k, seed, cores = args
    if cores and hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(0, set(cores))
    try:
        return collect(field, rate, horizon, k, seed)
    except Exception as exc:  # noqa: BLE001 - recorded
        return {'field': field, 'rate': rate, 'vehicles': k, 'seed': seed, 'error': f'{type(exc).__name__}: {exc}'[:300]}


def collect_all(out_dir: Path, fields, vehicles=(2, 4), seed: int = 7, workers: int = 4, cores=None):
    out_dir = Path(out_dir)
    (out_dir / 'decisions').mkdir(parents=True, exist_ok=True)
    todo = []
    for f in fields:
        for rate, horizon in Y.RATES.items():
            for k in vehicles:
                key = f'{f}__{rate}__k{k}__s{seed}'
                if not (out_dir / 'decisions' / f'{key}.json').exists():
                    todo.append((key, (f, rate, horizon, k, seed, cores)))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, a): key for key, a in todo}
        for fu in as_completed(futs):
            (out_dir / 'decisions' / f'{futs[fu]}.json').write_text(json.dumps(fu.result(), ensure_ascii=False, default=float), encoding='utf-8')


def _xy(rows):
    X = np.array([[r['features'][k] for k in Y.FEATURES] for r in rows], dtype=float)
    y = np.array([float(r['label']) for r in rows])
    return X, y


class Gate:
    def __init__(self, X, y):
        self.sc = P.Scaler(X)
        self.m = P._logit_fit(self.sc(X), y)
        probs = P._logit_prob(self.m, self.sc(X))
        pos = np.sort(probs[y > 0.5])
        # threshold keeping ≥ RECALL of the teacher's re-plans on the training fields
        self.tau = float(pos[int(np.floor((1 - RECALL) * len(pos)))]) if len(pos) else 1.0

    def prob(self, state):
        x = np.array([[state[k] for k in Y.FEATURES]], dtype=float)
        return float(P._logit_prob(self.m, self.sc(x))[0])

    def __call__(self, state):
        return 'reopt' if self.prob(state) >= self.tau else 'insert'


def _eval_job(args):
    field, rate, horizon, k, seed, gate, cores = args
    if cores and hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(0, set(cores))
    s = Y.make_stream(field, seed=seed, n_tasks=40, vehicles=k, travel='free', horizon_s=horizon)
    r = Y.simulate(s, gate, budget_s=0.3, seed=42)
    return {'field': field, 'rate': rate, 'vehicles': k, 'seed': seed,
            'gate': {m: r[m] for m in ('distance_m', 'mean_response_s', 'makespan_s', 'reopts', 'solver_s')}}


def evaluate(out_dir: Path, study_dir: Path, folds: int = 5, workers: int = 4, cores=None) -> dict:
    """Field-grouped cross-validation of the gate; compares with insert / always / rollout from the study."""
    from scipy import stats
    out_dir, study_dir = Path(out_dir), Path(study_dir)
    data = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((out_dir / 'decisions').glob('*.json'))]
    data = [d for d in data if 'error' not in d]
    fields = sorted({d['field'] for d in data})
    rng = np.random.default_rng(20261024)
    fold_of = {f: i % folds for i, f in enumerate(rng.permutation(fields))}
    jobs, gates = [], {}
    for fold in range(folds):
        train = [r for d in data if fold_of[d['field']] != fold for r in d['decisions']]
        X, y = _xy(train)
        gate = Gate(X, y)
        gates[fold] = {'tau': gate.tau, 'train_decisions': int(len(y)), 'train_positive_share': float(y.mean())}
        for d in data:
            if fold_of[d['field']] == fold:
                jobs.append((d['field'], d['rate'], d['horizon_s'], d['vehicles'], d['seed'], gate, cores))
    results = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(_eval_job, jobs):
            results.append(res)
    study = {}
    for p in (study_dir / 'streams').glob('*.json'):
        s = json.loads(p.read_text(encoding='utf-8'))
        study[(s['field'], s['rate'], s['vehicles'], s['seed'])] = s
    cells = {}
    for rate in Y.RATES:
        for k in sorted({r['vehicles'] for r in results}):
            rows = [r for r in results if r['rate'] == rate and r['vehicles'] == k]
            if not rows:
                continue
            cell = {'n': len(rows)}
            for metric in ('distance_m', 'mean_response_s'):
                for ref in ('insert', 'reopt', 'rollout'):
                    d = np.array([(r['gate'][metric] - study[(r['field'], r['rate'], r['vehicles'], r['seed'])][ref][metric])
                                  / study[(r['field'], r['rate'], r['vehicles'], r['seed'])][ref][metric] for r in rows])
                    nz = d[np.abs(d) > 1e-12]
                    boots = [rng.choice(d, len(d), replace=True).mean() for _ in range(2000)]
                    cell[f'gate_vs_{ref}_{metric}'] = {'mean_rel': float(d.mean()), 'ci95': [float(x) for x in np.percentile(boots, [2.5, 97.5])],
                                                       'p_two_sided': float(stats.wilcoxon(nz).pvalue) if len(nz) >= 10 else None}
            calls_gate = np.mean([r['gate']['reopts'] for r in rows])
            calls_reopt = np.mean([study[(r['field'], r['rate'], r['vehicles'], r['seed'])]['reopt']['reopts'] for r in rows])
            cell['solver_calls'] = {'gate': float(calls_gate), 'always_reopt': float(calls_reopt), 'saved_share': float(1 - calls_gate / calls_reopt)}
            cells[f'{rate}@k{k}'] = cell
    summary = {'folds': folds, 'recall_target': RECALL, 'gates': gates, 'streams': len(results), 'cells': cells,
               'features': list(Y.FEATURES),
               'note': '门控在其他田块的 rollout 决策上训练（按田块分组），只在预测概率超过阈值时调用求解器；规划层仿真，匀速代理计时'}
    (out_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    return {c: {'saved': round(v['solver_calls']['saved_share'], 3), 'dist_vs_reopt': round(v['gate_vs_reopt_distance_m']['mean_rel'], 4),
                'resp_vs_reopt': round(v['gate_vs_reopt_mean_response_s']['mean_rel'], 4)} for c, v in cells.items()}
