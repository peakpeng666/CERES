"""Inputs of the research figures. Everything is read from the workbench's own records (runs, scenarios,
field-simulation results, studies, assets); derived series (planned schedules, cross-track error, inter-vehicle
distance) are computed deterministically from those records and exported next to each figure as source data."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

import numpy as np

SIM_BODY_DIAGONAL = math.hypot(0.8, 0.56)   # field-simulation robot body (fieldworld.py): contact possible below this


@dataclass
class Context:
    scenario: dict | None = None
    run: dict | None = None                    # focused run
    runs: list[dict] = field(default_factory=list)   # runs to compare (same scenario), focused first
    sim: dict | None = None                    # field-simulation result (+ execution_plan)
    study: dict | None = None
    analysis: dict | None = None
    space: dict | None = None
    study_runs: list[dict] = field(default_factory=list)
    asset: dict | None = None                  # manifest
    asset_version: dict | None = None
    geometry: dict | None = None               # planning geometry extracted from the asset
    lanes: list | None = None                  # crop-row lane centre lines of the scenario (travel along rows)
    notes: list[str] = field(default_factory=list)

    def digest(self) -> str:
        """Changes whenever any record a figure could draw from changes."""
        # IDs / finished timestamps are not content identities: checks, source data,
        # contact evidence and metadata may be completed after a record is created.
        return _sha(vars(self))

    def sources(self) -> dict:
        """Provenance block written next to every figure."""
        out: dict = {}
        if self.scenario:
            out['scenario'] = {'id': self.scenario.get('id'), 'title': self.scenario.get('title'), 'instance_sha256': _sha(self.scenario.get('instance'))}
            refs = (self.scenario.get('instance') or {}).get('asset_refs')
            if refs:
                out['assets'] = refs
        if self.runs:
            out['runs'] = [{'run_id': r['run_id'], 'algorithm': r['algorithm']['id'], 'params': r.get('params'), 'instance_sha256': r.get('instance_sha256'),
                            'created_utc': r.get('created_utc'), 'check_valid': (r.get('check') or {}).get('valid')} for r in self.runs]
        if self.sim:
            out['simulation'] = {'session_id': self.sim.get('session_id'), 'run_id': self.sim.get('run_id'), 'world': self.sim.get('world'),
                                 'finished_utc': self.sim.get('finished_utc'), 'execution': 'pure-pursuit path tracking',
                                 'localization': 'Gazebo ground truth'}
        if self.study:
            out['study'] = {'study_id': self.study.get('study_id'), 'title': self.study.get('title'), 'runs': len(self.study_runs),
                            'reference': (self.analysis or {}).get('reference_note')}
        if self.asset:
            out['asset'] = {'id': self.asset['id'], 'version': (self.asset_version or {}).get('v'), 'sha256': (self.asset_version or {}).get('sha256')}
        return out


def _sha(obj) -> str:
    def encode(v):
        if isinstance(v, np.ndarray):
            return v.tolist()
        if isinstance(v, np.generic):
            return v.item()
        raise TypeError(f'Unsupported figure source: {type(v).__name__}')
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=encode,
                                     separators=(',', ':')).encode()).hexdigest()


# ------------------------------------------------------------------ geometry --
def cumlen(poly: list) -> np.ndarray:
    p = np.asarray(poly, float)
    if len(p) < 2:
        return np.zeros(len(p))
    return np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])


def schedule(polyline: list, stops: list[tuple[int, list[float], float]], speed: float, depot: list[float] | None) -> dict:
    """Planned timeline of one vehicle: travel along its polyline at constant speed, dwelling at each stop.

    stops: (task id, stop xy, service seconds) in visiting order. Returns segments, arrivals and depot returns,
    following the planner's own time model (distance / speed + service time)."""
    p = np.asarray(polyline, float)
    cum = cumlen(polyline)
    v = max(float(speed or 1.0), 1e-6)
    t, j, last_d = 0.0, 0, 0.0
    segments, arrivals = [], []
    for task, xy, service in stops:
        if len(p):
            d = np.hypot(p[j:, 0] - xy[0], p[j:, 1] - xy[1])
            hit = np.nonzero(d < 1e-3)[0]
            k = j + int(hit[0] if len(hit) else np.argmin(d))
        else:
            k = 0
        dist = float(cum[k]) if len(cum) else 0.0
        travel = max(0.0, dist - last_d) / v
        segments.append(('travel', t, t + travel))
        t += travel
        arrivals.append({'task': task, 't': t, 'completed_s': t + service, 'distance_m': dist})
        if service > 0:
            segments.append(('service', t, t + service))
            t += service
        j, last_d = k, dist
    total = float(cum[-1]) if len(cum) else 0.0
    segments.append(('travel', t, t + max(0.0, total - last_d) / v))
    t += max(0.0, total - last_d) / v
    returns = []
    if depot is not None and len(p) > 2:
        at = np.nonzero(np.hypot(p[1:-1, 0] - depot[0], p[1:-1, 1] - depot[1]) < 1e-3)[0] + 1
        # time at a depot vertex = travel time to it plus the service dwelt before it
        for k in at:
            dwell = sum(s for _, _, s in stops[:sum(1 for a in arrivals if a['distance_m'] <= cum[k])])
            returns.append(float(cum[k]) / v + dwell)
    return {'segments': segments, 'arrivals': arrivals, 'returns': returns, 'end': t, 'length_m': total}


def route_stops(route: dict, inst: dict) -> list[tuple[int, list[float], float]]:
    tasks = {t['id']: t for t in inst.get('tasks', [])}
    out = []
    for sv in route.get('services') or []:
        tk = tasks.get(sv.get('task'))
        xy = sv.get('stop') or (tk or {}).get('xy')
        if xy is not None:
            out.append((sv.get('task'), xy, float((tk or {}).get('service_s') or 0.0)))
    return out


def dist_to_path(points: np.ndarray, path: list) -> np.ndarray:
    """Distance of each point to a polyline (vectorised over points, looped over segments)."""
    P = np.asarray(path, float)
    out = np.full(len(points), np.inf)
    for a, b in zip(P[:-1], P[1:]):
        ab = b - a
        L = float(ab @ ab)
        t = np.clip(((points - a) @ ab) / L, 0, 1) if L > 0 else np.zeros(len(points))
        proj = a + t[:, None] * ab
        out = np.minimum(out, np.hypot(*(points - proj).T))
    return out


def project(point, path: list) -> tuple[np.ndarray, float]:
    """Closest point of a polyline to a point, and the distance."""
    P, q = np.asarray(path, float), np.asarray(point, float)
    best, bd = P[0], np.inf
    for a, b in zip(P[:-1], P[1:]):
        ab = b - a
        L = float(ab @ ab)
        t = float(np.clip((q - a) @ ab / L, 0, 1)) if L > 0 else 0.0
        c = a + t * ab
        d = float(np.hypot(*(q - c)))
        if d < bd:
            best, bd = c, d
    return best, bd


def trail_xy_at(trail: list, ts: np.ndarray) -> np.ndarray:
    T = np.asarray(trail, float)
    return np.c_[np.interp(ts, T[:, 0], T[:, 1]), np.interp(ts, T[:, 0], T[:, 2])]


def separation_series(sim: dict, step: float = 0.5) -> dict | None:
    trails = {k: v for k, v in (sim.get('trails') or {}).items() if len(v) > 1}
    if len(trails) < 2:
        return None
    end = max(v[-1][0] for v in trails.values())
    ts = np.arange(0.0, end + 1e-9, step)
    pos = {k: trail_xy_at(v, ts) for k, v in trails.items()}
    names = sorted(pos)
    dmin = np.full(len(ts), np.inf)
    pair = np.empty(len(ts), dtype=object)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            d = np.hypot(*(pos[a] - pos[b]).T)
            better = d < dmin
            dmin = np.where(better, d, dmin)
            pair[better] = f'{a}–{b}'
    return {'t': ts, 'd': dmin, 'pair': pair}


def cross_track(sim: dict, prefer_control: bool = True) -> dict:
    plan = {v['name']: v for v in ((sim.get('execution_plan') or {}).get('vehicles') or [])}
    out = {}
    # Match the controller's own online metric; waiting/service cycles have no CTE
    # measurement and must not repeat the previous value or fabricate a zero.
    controls = (sim.get('control_traces') or {}) if prefer_control else {}
    for name, trace in controls.items():
        rows = [row for row in trace if len(row) >= 3 and row[2] is not None]
        if rows:
            T = np.asarray([r[:3] for r in rows], float)
            out[name] = {'t': T[:, 0], 'e': T[:, 2], 'source': 'control_cycle'}
    for name, trail in (sim.get('trails') or {}).items():
        if name in controls:
            continue
        path = (plan.get(name) or {}).get('path')
        if not path or len(path) < 2 or not trail:
            continue
        T = np.asarray(trail, float)
        out[name] = {'t': T[:, 0], 'e': dist_to_path(T[:, 1:3], path), 'source': 'reconstructed_from_trail'}
    return out


def vehicle_label(sim: dict, name: str) -> str:
    for v in sim.get('vehicles') or []:
        if v['name'] == name:
            return v.get('label') or name
    return name
