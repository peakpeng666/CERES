"""What this machine has measured about each algorithm, read straight from the run history.

Nothing here is stored or frozen: the numbers change as runs are added, and every figure can be traced to its runs.
A head-to-head only counts runs that share one comparison key and time budget and passed the independent check, so a
win always means the same instance, objective, unit and budget.
"""
from __future__ import annotations

import statistics as st

RECENT = 20


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _usable(row: dict) -> bool:
    return row['state'] == 'done' and row['valid'] == 1 and isinstance(row['value'], (int, float)) and row['key'] is not None


def head_to_head(rows: list[dict]) -> dict[str, dict]:
    """Each algorithm's best verified value per comparison key and budget, against the best any algorithm reached there."""
    groups: dict[tuple, dict[str, float]] = {}
    for r in filter(_usable, rows):
        best = groups.setdefault((r['key'], r['budget_s']), {})
        best[r['algorithm_id']] = min(r['value'], best.get(r['algorithm_id'], r['value']))
    tally: dict[str, dict] = {}
    for best in groups.values():
        if len(best) < 2:
            continue
        top = min(best.values())
        for aid, value in best.items():
            t = tally.setdefault(aid, {'instances': 0, 'wins': 0, 'gaps': []})
            t['instances'] += 1
            gap = (value - top) / abs(top) if top else 0.0
            t['wins'] += gap <= 1e-9
            t['gaps'].append(gap)
    return {aid: {'instances': t['instances'], 'wins': t['wins'], 'mean_gap': st.mean(t['gaps'])} for aid, t in tally.items()}


def _summary(rows: list[dict], duel: dict | None) -> dict:
    done = [r for r in rows if r['state'] == 'done']
    times = [r['wall_s'] for r in done if isinstance(r['wall_s'], (int, float))]
    return {'runs': len(rows), 'completed': len(done), 'valid': sum(r['valid'] == 1 for r in done),
            'failed': sum(r['state'] in ('error', 'timeout') for r in rows), 'canceled': sum(r['state'] == 'canceled' for r in rows),
            'wall_s': {'median': st.median(times) if times else None, 'p90': _quantile(times, .9)},
            'last_utc': max((r['created_utc'] for r in rows), default=None), 'head_to_head': duel}


def summaries(rows: list[dict]) -> dict[str, dict]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r['algorithm_id'], []).append(r)
    duels = head_to_head(rows)
    return {aid: _summary(part, duels.get(aid)) for aid, part in by.items()}


def physical_record(rows: list[dict], physics: list[dict], algorithm_id: str) -> dict | None:
    """How this algorithm's plans behaved in the physics engine: actual against planned length and worst tracking error."""
    ids = {r['run_id'] for r in rows if r['algorithm_id'] == algorithm_id}
    # One plan may run twice, one at a time and all at once; the plan's own record is the first.
    mine = [p for p in physics if p.get('run_id') in ids and not p.get('session_id') and p.get('state') not in ('running', 'queued') and p.get('traffic_mode', 'serial') == 'serial' and p.get('kind') != 'batch']
    if not mine:
        return None
    metrics = [p['metrics'] for p in mine if p.get('state') == 'succeeded' and p.get('metrics')]
    ratios = [m['total_distance_m'] / m['planned_total_distance_m'] for m in metrics if m.get('planned_total_distance_m')]
    tracks = [m['cross_track_max_m'] for m in metrics if isinstance(m.get('cross_track_max_m'), (int, float))]
    return {'runs': len(mine), 'passed': sum(p.get('state') == 'succeeded' and (p.get('check') or {}).get('valid') is True for p in mine),
            'distance_ratio': st.mean(ratios) if ratios else None, 'cross_track_max_m': max(tracks) if tracks else None}


def history(rows: list[dict], algorithm_id: str, physics: list[dict] | None = None) -> dict:
    mine = [r for r in rows if r['algorithm_id'] == algorithm_id]
    maps: dict[str, dict] = {}
    for r in mine:
        m = maps.setdefault(r['scenario_id'], {'scenario_id': r['scenario_id'], 'title': r['title'], 'runs': 0, 'best': None, 'unit': r['unit'], 'last_utc': r['created_utc']})
        m['runs'] += 1
        m['last_utc'] = max(m['last_utc'], r['created_utc'])
        if _usable(r) and (m['best'] is None or r['value'] < m['best']):
            m['best'], m['unit'] = r['value'], r['unit']
    keep = ('run_id', 'scenario_id', 'title', 'state', 'status', 'value', 'unit', 'wall_s', 'created_utc', 'budget_s')
    recent = [{**{k: r[k] for k in keep}, 'valid': None if r['valid'] is None else bool(r['valid'])} for r in mine[:RECENT]]
    return {'algorithm_id': algorithm_id, **_summary(mine, head_to_head(rows).get(algorithm_id)), 'physical': physical_record(rows, physics or [], algorithm_id),
            'maps': sorted(maps.values(), key=lambda m: m['last_utc'], reverse=True), 'recent': recent}
