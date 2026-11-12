"""What repeated physics runs say: one run reduced to a row, and a set of rows reduced to a distribution.

Plain records in, plain records out; no engine and no numerical library, so the gateway, the worker and an offline analysis
all count the same way. The figures describe sensitivity inside the simulation under assumed parameter ranges.
"""
from __future__ import annotations

import math
from collections import Counter

SCOPE = '同一方案在假定参数范围内的仿真敏感度；扰动范围未经田间标定'
FINISHED = ('succeeded', 'failed', 'crashed')
# In causal order: a run stopped by the guard also misses its tasks and its path, and only the first cause is counted.
REASONS = (
    ('worker_crash', '进程异常退出', ('进程异常退出',)),
    ('diverged', '仿真发散', ('状态发散',)),
    ('no_progress', '失去路径进展', ('未取得路径进展',)),
    ('vehicle_guard', '车距保护停车', ('车间距离保护',)),
    ('contact', '与障碍或车辆接触', ('记录到车辆与实体障碍',)),
    ('timeout', '执行超时', ('执行超时',)),
    ('footprint', '足迹越界或侵入障碍', ('圆足迹侵入',)),
    ('separation', '车距核验未通过', ('足迹净空',)),
    ('tracking', '偏离计划路径', ('未完整跟踪',)),
    ('service', '作业未完成', ('作业停留',)),
    ('time_window', '任务时间窗未满足', ('任务时间窗',)),
    ('stop', '终点未稳定停止', ('未稳定', '时间内稳定')),
    ('record', '里程记录不一致', ('不一致',)),
)
REASON_LABELS = {code: label for code, label, _ in REASONS} | {'other': '其他'}


def primary_reason(errors: list[str]) -> str:
    """The first cause of a failed run, from the messages the engine and the checks wrote."""
    for code, _, marks in REASONS:
        if any(mark in e for e in errors for mark in marks):
            return code
    return 'other'


def gate_margin(plan: dict, result: dict) -> float | None:
    """How far short of the contested stretch the held vehicles came to rest, the worst one; ``None`` with no hold.

    A gate sits one stopping distance before the place it protects. Below zero, a vehicle told to wait rolled into the place
    while the vehicle ahead of it in the order was still there, which is the passing order failing physically.
    """
    stop_m = (plan.get('traffic') or {}).get('stop_m')
    events = (result.get('traffic') or {}).get('events') or []
    margins = []
    for i, hold in enumerate(events):
        if hold.get('event') != 'hold' or stop_m is None:
            continue
        release = next((e for e in events[i + 1:] if e.get('event') == 'release' and e.get('vehicle') == hold['vehicle']), None)
        end = release['t_sim'] if release else math.inf
        rows = [p for p in (result.get('trails') or {}).get(hold['vehicle'], []) if hold['t_sim'] <= p[0] <= end]
        rolled = max((math.dist(rows[0][1:3], p[1:3]) for p in rows), default=0.)    # a straight line: wheel jitter at rest adds up along a trail
        margins.append(hold['gate_s'] + stop_m - hold['s'] - rolled)
    return min(margins) if margins else None


def sample_row(plan: dict, result: dict) -> dict:
    """One run as a batch keeps it. The full trajectory can be regenerated from the plan and the seed."""
    check = result.get('check') or {}
    errors = (result.get('errors') or []) + (check.get('errors') or [])
    windows = result.get('time_windows')
    if windows and (not windows.get('all_on_time') or windows.get('early_starts')):
        errors = [*errors, '任务时间窗核验未通过']
    valid = result.get('state') == 'succeeded' and check.get('valid') is True and not errors
    pairs = (check.get('traffic') or {}).get('pairs') or []
    traffic = result.get('traffic') or {}
    metrics = result.get('metrics') or {}
    return {'state': result.get('state'), 'valid': valid, 'reason': None if valid else primary_reason(errors), 'errors': errors[:4],
            'makespan_s': metrics.get('makespan_s'), 'distance_m': metrics.get('total_distance_m'), 'energy_wh': metrics.get('energy_wh'),
            'cross_track_max_m': metrics.get('cross_track_max_m'), 'tasks_completed': metrics.get('tasks_completed'), 'tasks_total': metrics.get('tasks_total'),
            'min_gap_m': round(min(p['min_footprint_gap_m'] for p in pairs), 4) if pairs else None,
            'contacts': len(result.get('contacts') or []), 'holds': sum(e.get('event') == 'hold' for e in traffic.get('events') or []),
            'held_s': round(sum((traffic.get('held_s') or {}).values()), 2), 'gate_margin_m': _rounded(gate_margin(plan, result)),
            'time_windows': windows, 'sim_time': result.get('sim_time'), 'wall_s': result.get('wall_s'), 'model_sha256': result.get('model_sha256'), 'perturbation': result.get('perturbation')}


def _rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def quantile(values: list[float], q: float) -> float:
    """Linear interpolation between order statistics, as NumPy's default."""
    ordered = sorted(values)
    at = q * (len(ordered) - 1)
    low = math.floor(at)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (at - low)


def spread(values: list[float]) -> dict:
    """A distribution in five numbers; ``n`` says how many runs stand behind them."""
    if not values:
        return {'n': 0, 'min': None, 'p10': None, 'median': None, 'p90': None, 'max': None}
    return {'n': len(values), 'min': round(min(values), 4), 'p10': round(quantile(values, .1), 4), 'median': round(quantile(values, .5), 4),
            'p90': round(quantile(values, .9), 4), 'max': round(max(values), 4)}


def wilson(passed: int, n: int, z: float = 1.959964) -> list[float] | None:
    """The 95 % Wilson interval of a success rate. With few runs it stays honest where the plain ratio cannot: 16 of 16 still
    only supports about 81 % and up."""
    if not n:
        return None
    rate, scale = passed / n, 1 + z * z / n
    centre = (rate + z * z / (2 * n)) / scale
    half = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / scale
    return [round(max(0., centre - half), 4), round(min(1., centre + half), 4)]


def summarize(rows: list[dict], requested: int) -> dict:
    """A batch as a distribution.

    Working time is over the runs that passed, because a failed run has no finish; read it with the success rate. Vehicle gap
    is over every run that recorded one, failures included, since those are the closest approaches. Runs cut off by the time
    limit count as neither: they are also the slowest, so with any cut off the upper quantiles are lower bounds.
    """
    finished = [r for r in rows if r.get('state') in FINISHED]
    passed = [r for r in finished if r.get('valid')]
    margins = [r['gate_margin_m'] for r in finished if r.get('gate_margin_m') is not None]
    counts = Counter(r.get('reason') or 'other' for r in finished if not r.get('valid'))
    return {'requested': requested, 'finished': len(finished), 'cut_off': requested - len(finished), 'passed': len(passed),
            'success_rate': round(len(passed) / len(finished), 4) if finished else None, 'success_ci95': wilson(len(passed), len(finished)),
            'makespan_s': spread([r['makespan_s'] for r in passed if r.get('makespan_s') is not None]),
            'min_gap_m': spread([r['min_gap_m'] for r in finished if r.get('min_gap_m') is not None]),
            'held_s': spread([r['held_s'] for r in passed]),
            'gate_margin_m': spread(margins), 'order_violations': sum(m < 0 for m in margins),
            'failures': [{'reason': code, 'label': REASON_LABELS.get(code, code), 'count': n} for code, n in counts.most_common()],
            'scope': SCOPE}


def paired(a: list[dict], b: list[dict]) -> dict:
    """Two plans under the same draws, matched by seed, so the difference is between the plans and not between the draws."""
    other = {r['seed']: r for r in b if r.get('state') in FINISHED}
    pairs = [(x, other[x['seed']]) for x in a if x.get('state') in FINISHED and x['seed'] in other]
    both = [(x, y) for x, y in pairs if x.get('valid') and y.get('valid')]
    deltas = [x['makespan_s'] - y['makespan_s'] for x, y in both]
    return {'pairs': len(pairs), 'both_passed': len(both), 'only_a_passed': sum(x.get('valid') and not y.get('valid') for x, y in pairs),
            'only_b_passed': sum(y.get('valid') and not x.get('valid') for x, y in pairs), 'a_faster': sum(d < 0 for d in deltas), 'b_faster': sum(d > 0 for d in deltas),
            'median_delta_s': round(quantile(deltas, .5), 2) if deltas else None}
