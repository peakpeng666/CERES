"""Loop ③ first step: which time model predicts simulated driving time best? (offline, leave-one-plan-out)

Data: successful field-simulation sessions under ``runtime/fieldsim`` (read-only). Target: per-vehicle driving time
= simulated duration − serial-reservation queue wait. Predictors:

- ``constant_speed``: the planner's proxy ``planned_duration_s`` (path length / speed + service);
- ``reference``: the stop–turn–go analytic reference stored in the plan (only sessions after 2026-10-18);
- ``reference_scaled``: reference × one scalar fitted on the training plans;
- ``constant_scaled``: constant-speed proxy × one scalar fitted on the training plans;
- ``linear``: least squares on path length / speed, total heading change, number of vertices and service time.

Sessions repeat the same plan, so evaluation leaves one *plan* (hash of all vehicle paths) out at a time; errors are
reported per vehicle run with a plan-level bootstrap. Simulation only — no claim about real vehicles.
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .. import paths


def _turning(path) -> tuple[float, int]:
    pts = [p[:2] for p in path]
    total, verts = 0.0, 0
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        h1, h2 = math.atan2(b[1] - a[1], b[0] - a[0]), math.atan2(c[1] - b[1], c[0] - b[0])
        d = abs(math.atan2(math.sin(h2 - h1), math.cos(h2 - h1)))
        if d > 1e-6:
            total += d
            verts += 1
    return total, verts


def rows(root: Path | None = None) -> list[dict]:
    root = Path(root or paths.RUNTIME / 'fieldsim')
    out = []
    for res_path in sorted(glob.glob(str(root / 'field_*' / 'result.json'))):
        res = json.loads(Path(res_path).read_text(encoding='utf-8'))
        plan_path = Path(res_path).with_name('plan.json')
        if res.get('state') != 'succeeded' or not plan_path.is_file():
            continue
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        ref = (plan.get('execution_timing_reference') or {}).get('vehicles') or {}
        digest = hashlib.sha256(json.dumps([v.get('path') for v in plan.get('vehicles') or []], sort_keys=True).encode()).hexdigest()[:16]
        speed = float(plan.get('speed_mps') or 1.0)
        pv = {v['name']: v for v in plan.get('vehicles') or []}
        for v in res.get('vehicles') or []:
            if v.get('state') != 'done' or v['name'] not in pv:
                continue
            path = pv[v['name']].get('path') or []
            length = sum(math.dist(a[:2], b[:2]) for a, b in zip(path, path[1:]))
            turn, verts = _turning(path)
            service = sum(float(t.get('service_s') or 0) for t in pv[v['name']].get('tasks') or [])
            wait = float(v.get('queue_wait_s') or 0.0)
            out.append({'session': Path(res_path).parent.name, 'plan': digest, 'vehicle': v['name'], 'speed': speed,
                        'driving_s': float(v['duration_s']) - wait, 'constant_speed_s': float(v['planned_duration_s']),
                        'reference_s': (ref.get(v['name']) or {}).get('duration_s'), 'length_over_speed_s': length / speed,
                        'turn_rad': turn, 'vertices': verts, 'service_s': service})
    return out


def _lopo(data: list[dict], predict) -> np.ndarray:
    """Absolute percentage errors with each plan held out once."""
    errs = np.full(len(data), np.nan)
    plans = sorted({r['plan'] for r in data})
    for p in plans:
        train = [r for r in data if r['plan'] != p]
        test_idx = [i for i, r in enumerate(data) if r['plan'] == p]
        for i in test_idx:
            yhat = predict(train, data[i])
            if yhat is not None:
                errs[i] = abs(yhat - data[i]['driving_s']) / data[i]['driving_s']
    return errs


def _scalar(train, key):
    x = np.array([r[key] for r in train])
    y = np.array([r['driving_s'] for r in train])
    return float((x @ y) / (x @ x))


def _linear(train, row):
    X = np.array([[r['length_over_speed_s'], r['turn_rad'], r['vertices'], r['service_s'], 1.0] for r in train])
    y = np.array([r['driving_s'] for r in train])
    w, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(np.array([row['length_over_speed_s'], row['turn_rad'], row['vertices'], row['service_s'], 1.0]) @ w)


MODELS = {
    'constant_speed': lambda train, r: r['constant_speed_s'],
    'constant_scaled': lambda train, r: _scalar(train, 'constant_speed_s') * r['constant_speed_s'],
    'reference': lambda train, r: r['reference_s'],
    'reference_scaled': lambda train, r: _scalar([t for t in train if t['reference_s']], 'reference_s') * r['reference_s']
    if r['reference_s'] and any(t['reference_s'] for t in train) else None,
    'linear': _linear,
}


def evaluate(root: Path | None = None, boot: int = 2000) -> dict:
    data = rows(root)
    rng = np.random.default_rng(20261024)
    subsets = {'all': data, 'with_reference': [r for r in data if r['reference_s']]}
    out = {'vehicle_runs': len(data), 'plans': len({r['plan'] for r in data}),
           'with_reference_runs': len(subsets['with_reference']), 'with_reference_plans': len({r['plan'] for r in subsets['with_reference']}),
           'results': {}}
    for name, sub in subsets.items():
        plans = sorted({r['plan'] for r in sub})
        res = {}
        for m, fn in MODELS.items():
            if m.startswith('reference') and name == 'all':
                continue
            e = _lopo(sub, fn)
            ok = ~np.isnan(e)
            if not ok.any():
                continue
            per_plan = {p: e[[i for i, r in enumerate(sub) if r['plan'] == p and ok[i]]] for p in plans}
            per_plan = {p: v for p, v in per_plan.items() if len(v)}
            bs = []
            keys = list(per_plan)
            for _ in range(boot):
                pick = rng.choice(len(keys), len(keys), replace=True)
                bs.append(float(np.concatenate([per_plan[keys[k]] for k in pick]).mean()))
            res[m] = {'mape': float(e[ok].mean()), 'median_ape': float(np.median(e[ok])), 'n': int(ok.sum()),
                      'mape_ci95_plan_bootstrap': [float(x) for x in np.percentile(bs, [2.5, 97.5])]}
        out['results'][name] = res
    out['note'] = '仿真驾驶用时（扣除串行排队）按方案留一评价；会话重复同一方案，独立单位为方案。不外推为实车。'
    return out


def write(out_dir: Path) -> dict:
    res = evaluate()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'exec_model.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    return res
