"""How far two physics engines disagree on one frozen plan.

Both records must come from the same plan. Two questions are answered separately because they fail differently: where
the robots went (path difference, unaffected by a time shift) and when they got there (position at the same time since
departure, plus task arrival times). The gap is a measurement; no threshold here turns it into a pass.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

SCHEMA = 'urp.sim2sim/1'
SAMPLE_M = .05
IDENTITY_SCHEMA = 'urp.execution-plan-identity/1'
IDENTITY_PARTS = ('physics', 'traffic', 'vehicles', 'field', 'execution', 'perturbation', 'vehicle_asset')
TRACK_GAP_S = .25


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def plan_identity(plan: dict) -> dict:
    """Fingerprint actual execution inputs, independently of output paths and run labels.

    Keep array order: serial traffic and task execution depend on it. This helper
    uses only the standard library so the ROS process can record the same evidence.
    """
    vehicles = [{k: v for k, v in vehicle.items() if k not in ('label', 'color')}
                for vehicle in plan.get('vehicles') or []]
    asset = plan.get('vehicle_asset') or {}
    parts = {'physics': plan.get('physics'), 'traffic': plan.get('traffic'), 'vehicles': vehicles,
             'field': {k: plan.get(k) for k in ('instance', 'bounds', 'depot', 'speed_mps', 'robot_radius_m', 'travel')},
             'execution': plan.get('execution'), 'perturbation': plan.get('perturbation'),
             'vehicle_asset': {k: asset.get(k) for k in ('reference', 'metadata_sha256', 'adapter', 'generated_body')}}
    missing = [key for key in ('physics', 'traffic', 'vehicles', 'instance', 'bounds', 'speed_mps', 'robot_radius_m', 'travel')
               if plan.get(key) is None or plan.get(key) == [] or plan.get(key) == {}]
    for i, vehicle in enumerate(vehicles):
        missing.extend(f'vehicles[{i}].{key}' for key in ('name', 'start', 'path', 'tasks') if key not in vehicle)
    components = {key: _digest(value) for key, value in parts.items()}
    names = [vehicle.get('name') for vehicle in vehicles]
    return {'schema': IDENTITY_SCHEMA, 'sha256': _digest({'components': components, 'vehicle_names': names}),
            'components': components, 'vehicle_names': names, 'missing': missing}


def _identity_errors(a: dict, b: dict) -> tuple[list[str], dict]:
    errors, missing, different = [], [], []
    identities = []
    for side, record in (('a', a), ('b', b)):
        identity = record.get('plan_identity')
        if not isinstance(identity, dict) or identity.get('schema') != IDENTITY_SCHEMA:
            errors.append(f'{side} 缺少冻结执行计划身份')
            missing.append(f'{side}.plan_identity')
            identities.append(None)
            continue
        parts, names = identity.get('components'), identity.get('vehicle_names')
        if (not isinstance(parts, dict) or set(parts) != set(IDENTITY_PARTS)
                or any(not isinstance(value, str) or len(value) != 64 for value in parts.values())
                or not isinstance(names, list) or not names or any(not isinstance(n, str) or not n for n in names)
                or len(set(names)) != len(names) or identity.get('missing') != []
                or identity.get('sha256') != _digest({'components': parts, 'vehicle_names': names})):
            errors.append(f'{side} 冻结执行计划身份不完整或已损坏')
            missing.append(f'{side}.plan_identity.conditions')
            identities.append(None)
            continue
        identities.append(identity)
        for key in ('physics', 'perturbation'):
            try:
                matches = _digest(record.get(key)) == parts[key]
            except (TypeError, ValueError):
                matches = False
            if not matches:
                errors.append(f'{side} 结果的 {key} 与冻结计划身份不一致')
                different.append(f'{side}.{key}')
        vehicles = record.get('vehicles')
        if (not isinstance(vehicles, list) or any(not isinstance(v, dict) for v in vehicles)
                or [v.get('name') for v in vehicles] != names):
            errors.append(f'{side} 结果车辆集合或顺序与冻结计划不一致')
            different.append(f'{side}.vehicles')
    if all(identities):
        different.extend(key for key in IDENTITY_PARTS if identities[0]['components'][key] != identities[1]['components'][key])
        if identities[0]['vehicle_names'] != identities[1]['vehicle_names']:
            different.append('vehicle_names')
        if identities[0]['sha256'] != identities[1]['sha256']:
            errors.append('冻结执行条件不一致：' + '、'.join(different))
    return errors, {'a': a.get('plan_identity'), 'b': b.get('plan_identity'), 'missing': missing, 'different': different}


def _track(record: dict, name: str) -> np.ndarray:
    import numpy as np

    # If truth is present, a missing vehicle is a truth gap, not a reason to
    # substitute the less complete display trail.
    truth = record.get('rigid_body_poses')
    source, columns = (truth, [0, 1, 2, 6]) if truth is not None else (record.get('trails'), [0, 1, 2, 3])
    rows = source.get(name) if isinstance(source, dict) else None
    if not rows:
        raise ValueError('缺少轨迹')
    try:
        values = np.asarray(rows, dtype=float)
    except (TypeError, ValueError):
        raise ValueError('轨迹数据格式无效') from None
    if values.ndim != 2 or values.shape[1] <= max(columns):
        raise ValueError('轨迹列不完整')
    track = values[:, columns]
    if not np.isfinite(track).all() or (np.diff(track[:, 0]) <= 0).any():
        raise ValueError('轨迹含非有限值或非递增时钟')
    start = record.get('execution_started_sim_s')
    if not isinstance(start, (float, int)) or not math.isfinite(start):
        raise ValueError('缺少任务执行起始时钟')
    track = track[track[:, 0] >= start]
    if len(track) < 2:
        raise ValueError('执行开始后的轨迹不足两点')
    if track[0, 0] - start > TRACK_GAP_S or (np.diff(track[:, 0]) > TRACK_GAP_S + 1e-9).any():
        raise ValueError('轨迹存在超过 0.25 s 的缺口')
    vehicle = next(v for v in record['vehicles'] if v['name'] == name)
    duration = vehicle.get('duration_s')
    if not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration < 0:
        raise ValueError('缺少完整车辆执行时长')
    if track[-1, 0] < start + duration - TRACK_GAP_S:
        raise ValueError('轨迹未覆盖车辆执行终点')
    return track


def _departure(record: dict, name: str, track: np.ndarray) -> float:
    """Align motion after the first movement; mission-clock metrics retain queue delay."""
    import numpy as np

    moved = np.hypot(track[:, 1] - track[0, 1], track[:, 2] - track[0, 2]) > .02
    return float(track[np.argmax(moved), 0]) if moved.any() else float(track[0, 0])


def _resample(track: np.ndarray, step: float = SAMPLE_M) -> np.ndarray:
    import numpy as np

    seg = np.hypot(np.diff(track[:, 1]), np.diff(track[:, 2]))
    s = np.r_[0, np.cumsum(seg)]
    if s[-1] < step:
        return track[:1, 1:3]
    at = np.arange(0, s[-1], step)
    return np.column_stack([np.interp(at, s, track[:, 1]), np.interp(at, s, track[:, 2])])


def _nearest(points: np.ndarray, line: np.ndarray) -> np.ndarray:
    """Distance from each point to the polyline."""
    import numpy as np

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
    import numpy as np

    pa, pb = _resample(ta), _resample(tb)
    both = np.r_[_nearest(pa, pb), _nearest(pb, pa)] if len(pa) > 1 and len(pb) > 1 else np.array([math.dist(pa[0], pb[0])])
    da, db = _departure(a, name, ta), _departure(b, name, tb)
    at_time = _at_time(ta, tb, da, db)
    mission_clock = _at_time(ta, tb, a['execution_started_sim_s'], b['execution_started_sim_s'])
    va = next(v for v in a['vehicles'] if v['name'] == name)
    vb = next(v for v in b['vehicles'] if v['name'] == name)
    arrivals, task_counts, invalid_arrival = {}, {}, False
    for side, rec, dep in (('a', a, da), ('b', b, db)):
        seen = set()
        for task in rec.get('tasks') or []:
            if not isinstance(task, dict):
                invalid_arrival = True
                continue
            if task.get('vehicle') != name:
                continue
            key, value = str(task.get('id')), task.get('t_sim')
            if key in seen or task.get('id') is None:
                invalid_arrival = True
            seen.add(key)
            pair = arrivals.setdefault(key, {})
            pair[side] = value - dep if type(value) in (float, int) and math.isfinite(value) else None
        task_counts[side] = len(seen)
    unmatched = [key for key, value in arrivals.items() if value.get('a') is None or value.get('b') is None]
    complete_arrivals = (bool(arrivals) and not unmatched and not invalid_arrival
                         and task_counts['a'] == va.get('tasks_total', task_counts['a'])
                         and task_counts['b'] == vb.get('tasks_total', task_counts['b']))
    deltas = [value['b'] - value['a'] for value in arrivals.values()
              if value.get('a') is not None and value.get('b') is not None]
    return {'name': name, 'label': label,
            'path': {'mean_m': float(both.mean()), 'p95_m': float(np.percentile(both, 95)), 'max_m': float(both.max())},
            'at_time': at_time, 'mission_clock': mission_clock,
            'task_arrival_s': {'available': complete_arrivals, 'missing_tasks': unmatched, 'tasks': len(deltas),
                               'reason': None if complete_arrivals else '任务到达记录缺失、不完整或无效',
                               'mean_abs': float(np.mean(np.abs(deltas))) if complete_arrivals else None,
                               'max_abs': float(np.max(np.abs(deltas))) if complete_arrivals else None},
            'distance_m': {'a': va.get('distance_m'), 'b': vb.get('distance_m')},
            'duration_s': {'a': float(ta[-1, 0] - da), 'b': float(tb[-1, 0] - db)},
            'cross_track_max_m': {'a': va.get('cross_track_max_m'), 'b': vb.get('cross_track_max_m')}}


def _at_time(ta, tb, start_a: float, start_b: float) -> dict:
    import numpy as np

    horizon = min(ta[-1, 0] - start_a, tb[-1, 0] - start_b)
    clock = np.arange(max(0, ta[0, 0] - start_a, tb[0, 0] - start_b), max(horizon, 0), .1)
    if len(clock) < 2:
        return {'rmse_m': None, 'max_m': None, 'overlap_s': max(0., float(horizon))}
    gap = np.hypot(*[np.interp(clock + start_a, ta[:, 0], ta[:, c]) - np.interp(clock + start_b, tb[:, 0], tb[:, c]) for c in (1, 2)])
    return {'rmse_m': float(np.sqrt((gap * gap).mean())), 'max_m': float(gap.max()), 'overlap_s': float(clock[-1] - clock[0])}


def compare(a: dict, b: dict) -> dict:
    """Compare only records with matching execution evidence and complete vehicle tracks."""
    import numpy as np

    if a.get('run_id') != b.get('run_id'):
        raise ValueError('两条记录不是同一个方案')
    errors, identity = _identity_errors(a, b)
    if not a.get('run_id'):
        errors.append('缺少方案 run_id')
    rows = []
    if not errors:
        tracks = {}
        for vehicle in a['vehicles']:
            name = vehicle['name']
            for side, record in (('a', a), ('b', b)):
                try:
                    tracks[side, name] = _track(record, name)
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    errors.append(f'{side} 车辆 {name}：{exc}')
        if not errors:
            rows = [_vehicle(v['name'], v.get('label', v['name']), a, b, tracks['a', v['name']], tracks['b', v['name']])
                    for v in a['vehicles']]
    ma, mb = a.get('metrics') or {}, b.get('metrics') or {}

    def pair(key):
        return {'a': ma.get(key), 'b': mb.get(key)}

    return {'schema': SCHEMA, 'available': not errors, 'errors': errors, 'identity': identity,
            'run_id': a.get('run_id'), 'a': {'engine': a.get('engine', 'gazebo'), 'session_id': a.get('session_id'), 'state': a.get('state')},
            'b': {'engine': b.get('engine'), 'version': b.get('engine_version'), 'state': b.get('state')},
            'vehicles': rows,
            'path_max_m': max((r['path']['max_m'] for r in rows), default=None),
            'path_mean_m': float(np.mean([r['path']['mean_m'] for r in rows])) if rows else None,
            'time_alignment': 'per_vehicle_departure',
            'time_rmse_m': max((r['at_time']['rmse_m'] for r in rows if r['at_time']['rmse_m'] is not None), default=None),
            'mission_clock_rmse_m': max((r['mission_clock']['rmse_m'] for r in rows if r['mission_clock']['rmse_m'] is not None), default=None),
            'metrics': {k: pair(k) for k in ('makespan_s', 'total_distance_m', 'cross_track_mean_m', 'cross_track_max_m', 'energy_wh', 'tasks_completed')},
            'scope': ('已核验同一冻结执行条件；轨迹差距为引擎间差距，不是实车误差；time_rmse 按每车出发对齐（排除排队），mission_clock 按任务起点对齐（包含排队）；时序差距仅覆盖共有时段'
                      if not errors else '比较不可用：无法核验同一冻结执行条件或完整轨迹；原始结果保留')}
