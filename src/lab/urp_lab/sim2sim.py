"""How far two physics engines disagree on one frozen plan.

Both records must come from the same plan. Two questions are answered separately because they fail differently: where
the robots went (path difference, unaffected by a time shift) and when they got there (position at the same time since
departure, plus task arrival times). The gap is a measurement; no threshold here turns it into a pass.
"""
from __future__ import annotations

import math

import numpy as np

SCHEMA = 'urp.sim2sim/1'
SAMPLE_M = .05


def _track(record: dict, name: str) -> np.ndarray | None:
    rows = (record.get('rigid_body_poses') or {}).get(name)
    if rows:
        a = np.asarray(rows, dtype=float)[:, [0, 1, 2, 6]]
    else:
        trail = (record.get('trails') or {}).get(name)
        if not trail:
            return None
        a = np.asarray(trail, dtype=float)[:, :4]
    start = record.get('execution_started_sim_s')
    return a[a[:, 0] >= start] if start is not None else a


def _departure(record: dict, name: str, track: np.ndarray) -> float:
    """The moment this vehicle starts to move: queueing differs between engines and is not part of the physics."""
    moved = np.hypot(track[:, 1] - track[0, 1], track[:, 2] - track[0, 2]) > .02
    return float(track[np.argmax(moved), 0]) if moved.any() else float(track[0, 0])


def _resample(track: np.ndarray, step: float = SAMPLE_M) -> np.ndarray:
    seg = np.hypot(np.diff(track[:, 1]), np.diff(track[:, 2]))
    s = np.r_[0, np.cumsum(seg)]
    if s[-1] < step:
        return track[:1, 1:3]
    at = np.arange(0, s[-1], step)
    return np.column_stack([np.interp(at, s, track[:, 1]), np.interp(at, s, track[:, 2])])


def _nearest(points: np.ndarray, line: np.ndarray) -> np.ndarray:
    """Distance from each point to the polyline."""
    a, b = line[:-1], line[1:]
    d = b - a
    denom = np.maximum((d * d).sum(1), 1e-12)
    out = np.empty(len(points))
    for lo in range(0, len(points), 512):
        p = points[lo:lo + 512, None, :]
        u = np.clip(((p - a) * d).sum(2) / denom, 0, 1)
        out[lo:lo + 512] = np.linalg.norm(p - a - u[..., None] * d, axis=2).min(1)
    return out


def _vehicle(name: str, label: str, a: dict, b: dict, ta: np.ndarray, tb: np.ndarray) -> dict:
    pa, pb = _resample(ta), _resample(tb)
    both = np.r_[_nearest(pa, pb), _nearest(pb, pa)] if len(pa) > 1 and len(pb) > 1 else np.array([math.dist(pa[0], pb[0])])
    da, db = _departure(a, name, ta), _departure(b, name, tb)
    horizon = min(ta[-1, 0] - da, tb[-1, 0] - db)
    clock = np.arange(0, max(horizon, 0), .1)
    if len(clock) > 1:
        gap = np.hypot(*[np.interp(clock + da, ta[:, 0], ta[:, c]) - np.interp(clock + db, tb[:, 0], tb[:, c]) for c in (1, 2)])
        at_time = {'rmse_m': float(np.sqrt((gap * gap).mean())), 'max_m': float(gap.max())}
    else:
        at_time = {'rmse_m': None, 'max_m': None}
    va = next(v for v in a['vehicles'] if v['name'] == name)
    vb = next(v for v in b['vehicles'] if v['name'] == name)
    arrivals = {}
    for side, rec, dep in (('a', a, da), ('b', b, db)):
        for task in rec.get('tasks', []):
            if task.get('vehicle') == name and task.get('t_sim') is not None:
                arrivals.setdefault(str(task['id']), {})[side] = task['t_sim'] - dep
    deltas = [x['b'] - x['a'] for x in arrivals.values() if len(x) == 2]
    return {'name': name, 'label': label,
            'path': {'mean_m': float(both.mean()), 'p95_m': float(np.percentile(both, 95)), 'max_m': float(both.max())},
            'at_time': at_time,
            'task_arrival_s': {'tasks': len(deltas), 'mean_abs': float(np.mean(np.abs(deltas))) if deltas else None,
                               'max_abs': float(np.max(np.abs(deltas))) if deltas else None},
            'distance_m': {'a': va.get('distance_m'), 'b': vb.get('distance_m')},
            'duration_s': {'a': float(ta[-1, 0] - da), 'b': float(tb[-1, 0] - db)},
            'cross_track_max_m': {'a': va.get('cross_track_max_m'), 'b': vb.get('cross_track_max_m')}}


def compare(a: dict, b: dict) -> dict:
    """``a`` is the reference record, ``b`` the transfer target; both are execution records of the same plan."""
    if a.get('run_id') != b.get('run_id'):
        raise ValueError('两条记录不是同一个方案')
    rows = []
    for v in a['vehicles']:
        ta, tb = _track(a, v['name']), _track(b, v['name'])
        if ta is None or tb is None or len(ta) < 2 or len(tb) < 2:
            continue
        rows.append(_vehicle(v['name'], v.get('label', v['name']), a, b, ta, tb))
    ma, mb = a.get('metrics') or {}, b.get('metrics') or {}

    def pair(key):
        return {'a': ma.get(key), 'b': mb.get(key)}

    return {'schema': SCHEMA, 'run_id': a.get('run_id'), 'a': {'engine': a.get('engine', 'gazebo'), 'session_id': a.get('session_id'), 'state': a.get('state')},
            'b': {'engine': b.get('engine'), 'version': b.get('engine_version'), 'state': b.get('state')},
            'vehicles': rows,
            'path_max_m': max((r['path']['max_m'] for r in rows), default=None),
            'path_mean_m': float(np.mean([r['path']['mean_m'] for r in rows])) if rows else None,
            'time_rmse_m': max((r['at_time']['rmse_m'] for r in rows if r['at_time']['rmse_m'] is not None), default=None),
            'metrics': {k: pair(k) for k in ('makespan_s', 'total_distance_m', 'cross_track_mean_m', 'cross_track_max_m', 'energy_wh', 'tasks_completed')},
            'scope': '同一冻结方案在两个物理引擎中的差异；是引擎间差距，不是实车误差'}
