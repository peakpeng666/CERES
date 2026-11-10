# CERES 路径转角倒圆可执行性独立裁决 (Fillet Corner Rounding)
# 判别三点曲率采样盲区、数值微小扭结与实际圆弧倒圆，严格复核车体刚体足迹与覆盖率
from __future__ import annotations

import hashlib
import json
import math
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .sweep import THREAD_ENV, load_records, utc

STEP = 0.02
DEV_TOL = 0.01          # metres; sensitivity 0.005 / 0.02
RETRIES = 5


def _dedupe(pts, kinds):
    """Drop zero-length segments; kinds[i] labels segment i → i+1."""
    out_p, out_k = [list(pts[0])], []
    for i in range(1, len(pts)):
        if math.dist(out_p[-1], pts[i]) > 1e-9:
            out_k.append(kinds[i - 1] if i - 1 < len(kinds) else 'other')
            out_p.append(list(pts[i]))
    return out_p, out_k


def _circumradius(a, b, c):
    A, B, Cc = math.dist(a, b), math.dist(b, c), math.dist(a, c)
    area2 = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
    return A * B * Cc / (2 * area2) if area2 > 1e-15 else math.inf


def deviation(delta: float, radius: float) -> float:
    """Distance from a vertex to the radius-r arc tangent to both legs (minimal rounding deviation)."""
    return radius * (1 / math.cos(delta / 2) - 1)


def classify(pts, radius: float, step: float = STEP, dev_tol: float = DEV_TOL) -> list[dict]:
    """One entry per interior vertex with a heading change: index, class (arc/noise/corner), δ, leg lengths."""
    out = []
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        l_in, l_out = math.dist(a, b), math.dist(b, c)
        h_in, h_out = math.atan2(b[1] - a[1], b[0] - a[0]), math.atan2(c[1] - b[1], c[0] - b[0])
        delta = abs(math.atan2(math.sin(h_out - h_in), math.cos(h_out - h_in)))
        if delta <= 1e-12:
            continue
        if l_in <= 2 * step + 1e-12 and l_out <= 2 * step + 1e-12 and _circumradius(a, b, c) >= radius * (1 - 1e-3):
            cls = 'arc'
        elif deviation(delta, radius) <= dev_tol:
            cls = 'noise'
        else:
            cls = 'corner'
        out.append({'i': i, 'cls': cls, 'delta': delta, 'l_in': l_in, 'l_out': l_out,
                    'left': (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) > 0})
    return out


def _arc(corner, p_prev, p, p_next, radius, t, step):
    """Points of the radius-r arc replacing vertex p (tangent points included)."""
    ux, uy = (p[0] - p_prev[0]), (p[1] - p_prev[1])
    n_in = math.hypot(ux, uy)
    ux, uy = ux / n_in, uy / n_in
    vx, vy = (p_next[0] - p[0]), (p_next[1] - p[1])
    n_out = math.hypot(vx, vy)
    vx, vy = vx / n_out, vy / n_out
    start = (p[0] - t * ux, p[1] - t * uy)
    nx, ny = (-uy, ux) if corner['left'] else (uy, -ux)          # normal towards the arc centre
    cx, cy = start[0] + radius * nx, start[1] + radius * ny
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    sweep = corner['delta'] if corner['left'] else -corner['delta']
    n = max(2, math.ceil(abs(sweep) * radius / step) + 1)
    pts = [[cx + radius * math.cos(a0 + sweep * k / (n - 1)), cy + radius * math.sin(a0 + sweep * k / (n - 1))] for k in range(n)]
    pts[0] = [start[0], start[1]]
    pts[-1] = [p[0] + t * vx, p[1] + t * vy]
    return pts


def fillet(pts, kinds, radius: float, step: float = STEP, dev_tol: float = DEV_TOL) -> dict:
    """Round every corner (and every roundable numerical kink) by radius-r arcs inside the adjacent segments."""
    pts, kinds = _dedupe(pts, kinds)
    verts = classify(pts, radius, step, dev_tol)
    t_of = {}
    reasons, tolerated = [], 0
    seg_len = [math.dist(pts[s], pts[s + 1]) for s in range(len(pts) - 1)]
    budget_used = [0.0] * len(seg_len)
    for v in verts:
        if v['cls'] == 'arc':
            continue
        i = v['i']
        # Rounding is only done inside long chords: consuming a 2 cm turn sample would leave a near-zero segment
        # next to an arc vertex and break that vertex's own curvature. Kinks next to turn samples are tolerated
        # (and counted); corners next to turn samples need a repair, not a fillet.
        if min(v['l_in'], v['l_out']) <= 2 * step + 1e-12:
            if v['cls'] == 'noise':
                tolerated += 1
            else:
                reasons.append({'i': i, 'xy': [round(pts[i][0], 3), round(pts[i][1], 3)], 'delta_deg': round(math.degrees(v['delta']), 3),
                                'reason': '拐角邻接弧段，需修补'})
            continue
        t = radius * math.tan(v['delta'] / 2)
        fits = budget_used[i - 1] + t <= seg_len[i - 1] + 1e-12 and t <= seg_len[i] + 1e-12
        if fits:
            budget_used[i - 1] += t
            budget_used[i] += t
            t_of[i] = (t, v)
        elif v['cls'] == 'noise':
            tolerated += 1
        else:
            reasons.append({'i': i, 'xy': [round(pts[i][0], 3), round(pts[i][1], 3)], 'delta_deg': round(math.degrees(v['delta']), 3),
                            'reason': '相邻拐角过近，直段不足以倒圆'})
    # A segment shared by two corners must hold both tangent lengths.
    for s, used in enumerate(budget_used):
        if used > seg_len[s] + 1e-9:
            reasons.append({'i': s, 'reason': '相邻拐角过近，直段不足以倒圆', 'segment_m': round(seg_len[s], 4), 'needed_m': round(used, 4)})
    counts = {'arc': sum(v['cls'] == 'arc' for v in verts), 'noise': sum(v['cls'] == 'noise' for v in verts),
              'corner': sum(v['cls'] == 'corner' for v in verts), 'noise_tolerated': tolerated, 'rounded': len(t_of)}
    max_dev = max((radius * (1 / math.cos(v['delta'] / 2) - 1) for _t, v in t_of.values()), default=0.0)
    max_corner_deg = max((math.degrees(v['delta']) for v in verts if v['cls'] == 'corner'), default=0.0)
    if reasons:
        return {'ok': False, 'reasons': reasons[:20], 'n_unfilletable': len(reasons), **counts,
                'max_deviation_m': max_dev, 'max_corner_deg': max_corner_deg}
    out_p, out_k, swaths = [list(pts[0])], [], []
    for s in range(len(pts) - 1):
        a, b = pts[s], pts[s + 1]
        L = seg_len[s]
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        t0 = t_of[s][0] if s in t_of else 0.0
        t1 = t_of[s + 1][0] if s + 1 in t_of else 0.0
        start = [a[0] + t0 * ux, a[1] + t0 * uy]
        end = [b[0] - t1 * ux, b[1] - t1 * uy]
        if math.dist(out_p[-1], start) > 1e-9:          # only happens when the previous vertex was not rounded
            out_k.append(kinds[s])
            out_p.append(start)
        if math.dist(start, end) > 1e-9:
            out_k.append(kinds[s])
            out_p.append(end)
            if kinds[s] == 'swath':
                swaths.append([start, end])
        if s + 1 in t_of:
            t, v = t_of[s + 1]
            arc = _arc(v, pts[s], pts[s + 1], pts[s + 2], radius, t, step)
            for q in arc[1:]:
                out_k.append('turn')
                out_p.append(q)
    return {'ok': True, 'points': out_p, 'kinds': out_k, 'swaths': swaths, **counts, 'max_deviation_m': max_dev,
            'max_corner_deg': max_corner_deg, 'length_m': sum(math.dist(p, q) for p, q in zip(out_p, out_p[1:]))}


def verdict(instance: dict, pts, kinds, dev_tol: float = DEV_TOL) -> dict:
    """Fillet verdict for one path: roundability, then the unchanged official checker on the rounded path."""
    from ..solvers import f2c_coverage as C
    from .reverify import strict_curvature
    r = instance['turn_radius']
    f = fillet(pts, kinds, r, STEP, dev_tol)
    base = {k: f[k] for k in ('arc', 'noise', 'corner', 'noise_tolerated', 'rounded', 'max_deviation_m', 'max_corner_deg')}
    if not f['ok']:
        return {'fillet_valid': False, 'stage': 'fillet', 'reasons': f['reasons'], 'n_unfilletable': f['n_unfilletable'], **base}
    check, metrics = C.check_path(instance, f['points'], f['swaths'])
    strict = strict_curvature(f['points'], r)
    return {'fillet_valid': bool(check['valid']), 'stage': 'official_check_after_fillet', 'errors_after': check['errors'],
            'coverage_after': metrics.get('working_area_fraction'), 'length_after_m': f['length_m'],
            'strict_violations_after': strict['strict_violations'], **base}


# ------------------------------------------------------------------ batch over sweep records

def _path(rec: dict, inst: dict):
    """Re-run one recorded job; returns (points, kinds or None, sha256 of the polyline as recorded by the sweep)."""
    from . import repair
    if rec['algorithm'] == 'f2c.full':
        pts, kinds, _robot, _cells = repair._native(inst, dict(rec['params']))
        poly = pts
    elif rec['algorithm'] == 'evolve.f2c_repair':
        routes, _check, _m, raw = repair.solve_full_repaired(inst, dict(rec['params']))
        if not routes:
            return None, None, None
        poly, kinds = routes[0]['polyline'], raw['kinds']
    elif rec['algorithm'] == 'evolve.f2c_repair2':
        from . import repair2
        routes, _check, _m, raw = repair2.solve_full_repaired2(inst, dict(rec['params']))
        if not routes:
            return None, None, None
        poly, kinds = routes[0]['polyline'], raw['kinds']
    else:                                                     # single-cell adapters: densified everywhere
        from .sweep import solve
        from . import instances as I
        sc, _ = I.coverage_instance(rec['instance']['field'], rec['instance']['profile'])
        result = solve(sc, inst, rec['algorithm'], dict(rec['params']))
        routes = result.get('routes') or []
        if not routes:
            return None, None, None
        poly, kinds = routes[0]['polyline'], None
    digest = hashlib.sha256(json.dumps([poly], separators=(',', ':')).encode()).hexdigest()
    return poly, kinds, digest


def _one(rec: dict, tolerances=(DEV_TOL, 0.005, 0.02)) -> dict:
    os.environ.update(THREAD_ENV)
    from . import instances as I
    from .reverify import strict_curvature
    _sc, inst = I.coverage_instance(rec['instance']['field'], rec['instance']['profile'])
    out = {'job_id': rec['job_id'], 'config': rec['config'], 'recorded_sha256': rec.get('routes_sha256'), 'checked_utc': utc(),
           'attempts': 0}
    for attempt in range(1, RETRIES + 1):
        poly, kinds, digest = _path(rec, inst)
        out['attempts'] = attempt
        if digest is not None and digest == rec.get('routes_sha256'):
            break
    else:
        out.update({'identical': False, 'fillet_valid': None, 'verdict': 'unverifiable',
                    'note': f'{RETRIES} 次重跑均未再现记录中的路径哈希（Fields2Cover 运行间非确定性）；不判定'})
        return out
    out['identical'] = True
    if kinds is None:                                         # single-cell adapter
        s = strict_curvature(poly, inst['turn_radius'])
        out.update({'fillet_valid': bool(s['strict_valid_curvature']), 'stage': 'single_cell_strict', 'verdict': 'evaluated'})
        for tol in tolerances:
            out[f'fillet_valid@{tol:g}'] = out['fillet_valid']
        return out
    main = verdict(inst, poly, kinds, DEV_TOL)
    out.update(main)
    out['verdict'] = 'evaluated'
    for tol in tolerances:
        out[f'fillet_valid@{tol:g}'] = main['fillet_valid'] if tol == DEV_TOL else verdict(inst, poly, kinds, tol)['fillet_valid']
    return out


def run(out: Path, workers: int, cores: list[int] | None) -> dict:
    """Fillet verdicts for every officially feasible coverage job of a run directory (resumable)."""
    out = Path(out)
    target = out / 'fillet'
    target.mkdir(exist_ok=True)
    done = {p.stem for p in target.glob('*.json') if p.name != 'summary.json'}
    todo = [r for r in load_records(out) if r['family'] == 'coverage' and r['status'] == 'feasible' and r.get('valid')
            and r['job_id'] not in done]

    def init(core_list):
        if core_list and hasattr(os, 'sched_setaffinity'):
            os.sched_setaffinity(0, set(core_list))
    with ProcessPoolExecutor(max_workers=workers, initializer=init, initargs=(cores,)) as ex:
        futs = {ex.submit(_one, r): r for r in todo}
        for f in as_completed(futs):
            try:
                res = f.result()
            except Exception as exc:  # noqa: BLE001 - recorded, never hidden
                r = futs[f]
                res = {'job_id': r['job_id'], 'config': r['config'], 'verdict': 'error', 'fillet_valid': None,
                       'error': f'{type(exc).__name__}: {exc}'[:300], 'checked_utc': utc()}
            (target / f"{res['job_id']}.json").write_text(json.dumps(res, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    return summarise(out)


def load(out: Path) -> dict:
    return {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in (Path(out) / 'fillet').glob('*.json') if p.name != 'summary.json'}


def summarise(out: Path) -> dict:
    from collections import Counter, defaultdict
    res = load(out)
    by_cfg = defaultdict(Counter)
    for r in res.values():
        key = 'unverifiable' if r.get('verdict') == 'unverifiable' else 'error' if r.get('verdict') == 'error' else \
            ('valid' if r.get('fillet_valid') else 'invalid')
        by_cfg[r['config']][key] += 1
        if r.get('stage') == 'fillet':
            by_cfg[r['config']]['not_filletable'] += 1
    summary = {'jobs': len(res), 'by_config': {c: dict(v) for c, v in sorted(by_cfg.items())},
               'tolerance_sensitivity_m': {tol: sum(1 for r in res.values() if r.get(f'fillet_valid@{tol}')) for tol in ('0.005', '0.01', '0.02')},
               'note': '倒圆判定：拐角能在相邻直段内用最小转弯半径圆弧倒圆，且倒圆后路径通过未修改的官方检查；单区适配器等同官方∧严格'}
    (Path(out) / 'fillet' / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    return summary
