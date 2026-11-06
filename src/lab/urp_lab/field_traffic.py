"""Conservative fleet execution: one complete parking-to-parking mission at a time.

This is an explicit safety baseline, not a concurrent multi-agent planner. World generation
certifies that waiting/finished parking footprints are outside every other mission corridor.
"""
from __future__ import annotations

import math

POLICY = 'exclusive_mission_v1'
PASSAGE_POLICY = 'coordinated_passage_v1'
PARKING_MARGIN_M = 0.8  # accommodates the follower's 0.35 m final tolerance and observed tracking error


class MissionQueue:
    def __init__(self, drivers, radius, started):
        self.drivers, self.radius, self.started = drivers, radius, started
        self.active = None
        self.release_at = None
        self.events = []
        for d in drivers:
            d.state = 'waiting'
            d.t_start = started  # makespan includes queueing, even for later departures
            d.t_depart = None

    def step(self, now):
        for i, a in enumerate(self.drivers):
            for b in self.drivers[i + 1:]:
                if a.pose is not None and b.pose is not None:
                    distance = math.dist(a.pose[:2], b.pose[:2])
                    if distance < 2 * self.radius + 0.15:
                        raise RuntimeError(f'车间距离保护：{a.name}/{b.name} 间距 {distance:.3f} m；全部停车')
        if self.active is not None:
            d = self.active
            if d.state == 'failed':
                raise RuntimeError(f'{d.name} 执行失败，取消其余预约：{d.failure}')
            if d.state != 'done':
                return
            if self.release_at is None:
                self.release_at = now + 1.0  # let the physical drive decelerate before releasing the corridor
                return
            if now < self.release_at:
                return
            self.events.append({'event': 'release', 'vehicle': d.name, 't_sim': now})
            self.active, self.release_at = None, None
        d = next((d for d in self.drivers if d.state == 'waiting'), None)
        if d is not None:
            self.active = d
            d.state, d.t_depart = 'driving', now
            d.last_progress = (d.s, now)
            self.events.append({'event': 'depart', 'vehicle': d.name, 't_sim': now,
                                'wait_s': now - self.started})


class PassageQueue:
    """All vehicles move at once and stop only at gates, until the vehicles that go first have cleared the place ahead.

    The gate event dependencies from ``field_coordination`` are acyclic. Continued progress additionally assumes
    monotone path tracking, finite delays and valid bounded-error motion; failures stop the run, not prove safety.
    This class uses no geometry and can be consumed by the ROS controller.
    """

    def __init__(self, drivers, radius, started, gates):
        self.drivers, self.radius, self.started = drivers, radius, started
        self.by_name = {d.name: d for d in drivers}
        self.gates = {d.name: [dict(g) for g in gates.get(d.name, [])] for d in drivers}
        self.events = []
        self.held = {}
        self.held_s = {d.name: 0.0 for d in drivers}
        for d in drivers:
            d.state = 'waiting'
            d.t_start = started
            d.t_depart = None

    def _guard(self):
        for i, a in enumerate(self.drivers):
            if a.state == 'failed':
                raise RuntimeError(f'{a.name} 执行失败，全部停车：{a.failure}')
            for b in self.drivers[i + 1:]:
                if a.pose is not None and b.pose is not None:
                    distance = math.dist(a.pose[:2], b.pose[:2])
                    if distance < 2 * self.radius + 0.15:
                        raise RuntimeError(f'车间距离保护：{a.name}/{b.name} 间距 {distance:.3f} m；全部停车')

    def _clear(self, gate):
        return all(self.by_name[name].state == 'done' or self.by_name[name].s >= s for name, s in gate['after'])

    def step(self, now):
        self._guard()
        for d in self.drivers:
            if d.state == 'done':
                continue
            gates = self.gates[d.name]
            while gates and self._clear(gates[0]):
                gates.pop(0)
            blocked = bool(gates) and d.s >= gates[0]['s'] - 1e-9
            if d.state == 'driving' and blocked:
                d.state = 'waiting'
                self.held[d.name] = now
                self.events.append({'event': 'hold', 'vehicle': d.name, 't_sim': now, 's': d.s, 'gate_s': gates[0]['s'], 'after': gates[0]['after']})
            elif d.state == 'waiting' and not blocked:
                d.state = 'driving'
                d.last_progress = (d.s, now)
                if d.t_depart is None:
                    d.t_depart = now
                    self.events.append({'event': 'depart', 'vehicle': d.name, 't_sim': now, 'wait_s': now - self.started})
                else:
                    waited = now - self.held.pop(d.name)
                    self.held_s[d.name] += waited
                    self.events.append({'event': 'release', 'vehicle': d.name, 't_sim': now, 'wait_s': waited})


def separation(trails, radius):
    """Exact minimum of pairwise linearly interpolated positions on the common time interval.

    NumPy is imported only by the independent solver-side checker, never by the ROS controller.
    The interpolation assumption and maximum observed gap are recorded, not hidden.
    """
    import numpy as np
    arrays = {}
    for name, samples in trails.items():
        a = np.asarray(samples, dtype=float)
        if a.ndim != 2 or a.shape[0] < 2 or a.shape[1] < 4 or not np.isfinite(a).all():
            raise ValueError(f'{name} 缺少有效时间轨迹')
        if np.any(np.diff(a[:, 0]) <= 0):
            raise ValueError(f'{name} 时间戳没有严格递增')
        arrays[name] = a
    pairs = []
    for i, (na, a) in enumerate(arrays.items()):
        for nb, b in list(arrays.items())[i + 1:]:
            start, end = max(a[0, 0], b[0, 0]), min(a[-1, 0], b[-1, 0])
            if end <= start or abs(a[0, 0] - b[0, 0]) > 0.2 or abs(a[-1, 0] - b[-1, 0]) > 0.2:
                raise ValueError(f'{na}/{nb} 轨迹没有覆盖同一执行时段')
            t = np.unique(np.r_[start, a[(a[:, 0] > start) & (a[:, 0] < end), 0],
                                 b[(b[:, 0] > start) & (b[:, 0] < end), 0], end])
            rel = np.column_stack([np.interp(t, a[:, 0], a[:, c]) - np.interp(t, b[:, 0], b[:, c]) for c in (1, 2)])
            delta = np.diff(rel, axis=0)
            denom = (delta * delta).sum(axis=1)
            u = np.clip(np.divide(-(rel[:-1] * delta).sum(axis=1), denom,
                                  out=np.zeros_like(denom), where=denom > 1e-20), 0, 1)
            dist = np.linalg.norm(rel[:-1] + u[:, None] * delta, axis=1)
            k = int(dist.argmin())
            gap = max(float(np.diff(a[:, 0]).max()), float(np.diff(b[:, 0]).max()))
            pairs.append({'vehicles': [na, nb], 'min_center_distance_m': float(dist[k]),
                          'min_footprint_gap_m': float(dist[k]) - 2 * radius,
                          't_sim': float(t[k] + u[k] * (t[k + 1] - t[k])), 'max_sample_gap_s': gap,
                          'valid': bool(dist[k] >= 2 * radius and gap <= 0.5)})
    return {'valid': all(p['valid'] for p in pairs), 'pairs': pairs,
            'method': '同步分段线性插值的整段最小距离；圆足迹；采样间未观测的曲线运动不作证明',
            'radius_m': radius, 'max_allowed_sample_gap_s': 0.5}
