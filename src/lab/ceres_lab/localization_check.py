"""AMCL estimate vs ground-truth comparison and Nav2 replan events for TB4 simulation runs.

Pure post-hoc analysis of timelines the session already records (`actual.amcl`,
`actual.ground_truth`, `nav2_plans`). It never gates a run: estimation quality is
reported, not judged — the honest scope is a simulated 2-D lidar under AMCL defaults,
not a calibrated localization system.
"""
from __future__ import annotations

import math


def _interp(times, values, q):
    lo, hi = 0, len(times) - 1
    if q <= times[0]:
        return values[0]
    if q >= times[-1]:
        return values[-1]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if times[mid] <= q:
            lo = mid
        else:
            hi = mid
    span = times[hi] - times[lo]
    u = 0.0 if span <= 0 else (q - times[lo]) / span
    return values[lo] + (values[hi] - values[lo]) * u


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def compare(execution: dict, mode: str) -> dict:
    """Estimate-vs-truth error percentiles and replan summary for one execution record."""
    out = {'mode': mode,
           'scope': 'simulated 2-D lidar + AMCL defaults vs Gazebo truth, paired at estimate source stamps; '
                    'recorded evidence, not a calibrated localization claim'}
    amcl = execution.get('actual', {}).get('amcl') or []
    truth = execution.get('actual', {}).get('ground_truth') or []
    if mode != 'amcl' or len(amcl) < 2 or len(truth) < 2:
        out['estimate_error'] = None if mode != 'amcl' else '估计或真值时间线不足，无法配对'
        out['replans'] = _replans(execution)
        return out
    tt = [row[0] for row in truth]
    lo, hi = max(amcl[0][0], tt[0]), min(amcl[-1][0], tt[-1])
    paired = [row for row in amcl if lo <= row[0] <= hi]
    if len(paired) < 2:
        out['estimate_error'] = '估计与真值时间重叠不足，无法配对'
        out['replans'] = _replans(execution)
        return out
    errors, yaw_errors = [], []
    for t, x, y, a in paired:
        dx, dy = x - _interp(tt, [r[1] for r in truth], t), y - _interp(tt, [r[2] for r in truth], t)
        errors.append(math.hypot(dx, dy))
        yaw_errors.append(abs(_wrap(a - _interp(tt, [r[3] for r in truth], t))))
    ordered = sorted(errors)
    out['estimate_error'] = {
        'paired_samples': len(paired),
        'position_rmse_m': math.sqrt(sum(e * e for e in errors) / len(errors)),
        'position_mean_m': sum(errors) / len(errors),
        'position_p50_m': ordered[len(ordered) // 2],
        'position_p95_m': ordered[min(len(ordered) - 1, int(.95 * len(ordered)))],
        'position_max_m': ordered[-1],
        'position_final_m': errors[-1],
        'yaw_rmse_rad': math.sqrt(sum(e * e for e in yaw_errors) / len(yaw_errors)),
    }
    out['replans'] = _replans(execution)
    return out


def _replans(execution: dict) -> dict:
    plans = execution.get('nav2_plans') or []
    times = [p['t_sim'] for p in plans]
    lengths = [sum(math.dist(a, b) for a, b in zip(p['path'], p['path'][1:])) for p in plans]
    return {'count': len(plans), 'first_t_sim': times[0] if times else None,
            'last_t_sim': times[-1] if times else None,
            'path_length_first_m': lengths[0] if lengths else None,
            'path_length_last_m': lengths[-1] if lengths else None,
            'note': 'every Nav2 global plan message; replan reasons are not published by the BT and are not inferred'}
