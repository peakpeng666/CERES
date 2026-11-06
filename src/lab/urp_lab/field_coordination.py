"""Concurrent execution of frozen vehicle paths through a temporal plan graph.

The paths stay exactly as planned and verified; only when a vehicle may pass a point changes. Wherever two corridors come
within the clearance, that pair of vehicles gets an order, and the later vehicle waits at a gate until the earlier one has
cleared the place. This is the temporal plan graph of Hönig et al. (2016) and the action dependency graph of Hönig et al.
(2019), applied to continuous paths instead of grid plans: the graph is acyclic at the event-dependency level. Progress assumes monotone path tracking, finite delays,
bounded braking and tracking error, and valid localisation. This is a simulation coordination candidate, not a
proof of physical safety or a guarantee under arbitrary failures. Timing only chooses a passing order.

This runs on the solver side and uses NumPy. The result is a list of plain gates per vehicle, which
``field_traffic.PassageQueue`` reads at run time with no geometry at all.
"""
from __future__ import annotations

import itertools
import math
import random
import time
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .field_traffic import PASSAGE_POLICY as POLICY

# On top of two footprints: the 0.15 m the run-time guard keeps, plus each vehicle's tracking error (0.15 m is above the
# 0.14 m worst case measured across both engines) and a sampling allowance.
ESTIMATE_MODEL = 'urp.homogeneous-diffdrive-coordination/v1'
CLEARANCE_MARGIN_M = 0.55
SAMPLE_M = 0.2
MAX_SAMPLES = 6000          # per path; longer paths are sampled more coarsely and the clearance grows with the step
CHUNK_M = 4.0               # a long shared stretch is split, so a follower trails the leader instead of waiting for all of it
EDGE_M = 0.25               # a place this close to a path end counts as containing that end
RESTART_S = 1.0             # a held vehicle has to accelerate again
IMPROVE_TRIALS = 2000       # orders tried after the first assignment; a count and not a clock, so a plan compiles the same everywhere


@dataclass
class Box:
    """Vehicles ``a`` and ``b`` must not be inside their ranges of this box at the same time."""
    a: int
    b: int
    a_lo: float
    a_hi: float
    b_lo: float
    b_hi: float


@dataclass
class Meeting:
    """One connected place where two corridors meet. All its boxes share one order, because vehicles cannot overtake inside it."""
    a: int
    b: int
    boxes: list[Box]
    first: int | None = None      # vehicle index that goes first
    choices: tuple[int, ...] = ()
    arrival: float = 0.0


@dataclass
class _Graph:
    """Events are (vehicle, position along the path); edges mean 'must happen before'."""
    n: int
    out: list[set[int]] = field(default_factory=list)

    def __post_init__(self):
        self.out = [set() for _ in range(self.n)]

    def reaches(self, start: int, goal: int) -> bool:
        seen, stack = {start}, [start]
        while stack:
            node = stack.pop()
            if node == goal:
                return True
            for nxt in self.out[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False


def _arc(path: list) -> np.ndarray:
    pts = np.asarray(path, dtype=float)[:, :2]
    return np.r_[0.0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]


def _sample(path: list, step: float) -> tuple[np.ndarray, np.ndarray]:
    pts = np.asarray(path, dtype=float)[:, :2]
    cum = _arc(path)
    count = max(2, int(math.ceil(cum[-1] / step)) + 1)
    s = np.linspace(0, cum[-1], count)
    return s, np.column_stack([np.interp(s, cum, pts[:, 0]), np.interp(s, cum, pts[:, 1])])


def timeline(vehicle: dict, speed: float, yaw_rate: float = 1.6) -> tuple[list[float], list[float]]:
    """Free-flow time to reach each path vertex and task stop: speed along the path, turns in place, dwell at tasks."""
    path = vehicle['path']
    cum = _arc(path).tolist()
    stops = sorted((float(t.get('path_s', 0)), float(t.get('service_s') or 0)) for t in vehicle.get('tasks') or [])
    s_out, t_out, now, k = [0.0], [0.0], 0.0, 0
    for i in range(1, len(path)):
        now += (cum[i] - cum[i - 1]) / speed
        while k < len(stops) and stops[k][0] <= cum[i] + 1e-9:
            now += stops[k][1]
            k += 1
        if i + 1 < len(path):
            h1 = math.atan2(path[i][1] - path[i - 1][1], path[i][0] - path[i - 1][0])
            h2 = math.atan2(path[i + 1][1] - path[i][1], path[i + 1][0] - path[i][0])
            turn = abs((h2 - h1 + math.pi) % (2 * math.pi) - math.pi)
            if turn > 0.6:                     # the follower stops and turns on the spot here
                now += turn / yaw_rate + 1.0
        s_out.append(cum[i])
        t_out.append(now)
    return s_out, t_out


def _eta(line: tuple[list[float], list[float]], s: float) -> float:
    return float(np.interp(s, line[0], line[1]))


def meetings(vehicles: list[dict], radius: float) -> tuple[list[Meeting], float]:
    """Every place two corridors come within the clearance, with the clearance actually used."""
    longest = max(float(_arc(v['path'])[-1]) for v in vehicles)
    step = max(SAMPLE_M, longest / MAX_SAMPLES)
    clearance = 2 * radius + CLEARANCE_MARGIN_M + step
    samples = [_sample(v['path'], step) for v in vehicles]
    found: list[Meeting] = []
    for a in range(len(vehicles)):
        for b in range(a + 1, len(vehicles)):
            sa, pa = samples[a]
            sb, pb = samples[b]
            near = np.zeros((len(sa), len(sb)), dtype=bool)
            for lo in range(0, len(sa), 512):
                d = pa[lo:lo + 512, None, :] - pb[None, :, :]
                near[lo:lo + 512] = np.einsum('ijk,ijk->ij', d, d) < clearance * clearance
            labels, count = ndimage.label(near, structure=np.ones((3, 3), dtype=int))
            for index, window in enumerate(ndimage.find_objects(labels), 1):
                rows = np.flatnonzero((labels[window] == index).any(axis=1)) + window[0].start
                boxes = []
                span = max(1, int(round(CHUNK_M / step)))
                for lo in range(int(rows[0]), int(rows[-1]) + 1, span):
                    hi = min(lo + span, int(rows[-1]) + 1)
                    cols = np.flatnonzero((labels[lo:hi] == index).any(axis=0))
                    if cols.size:
                        boxes.append(Box(a, b, float(sa[lo]), float(sa[hi - 1]), float(sb[cols[0]]), float(sb[cols[-1]])))
                if boxes:
                    found.append(Meeting(a, b, boxes))
    return found, clearance


class Schedule:
    """Who goes first at every meeting, checked to be acyclic, with the free-flow prediction of what that costs."""

    def __init__(self, vehicles: list[dict], found: list[Meeting], stop_m: float, speed: float):
        self.vehicles, self.found, self.stop_m = vehicles, found, stop_m
        self.lengths = [float(_arc(v['path'])[-1]) for v in vehicles]
        self.lines = [timeline(v, speed) for v in vehicles]
        self.events: list[list[tuple[float, int]]] = [[] for _ in vehicles]     # per vehicle: (position, event id)
        self.nodes: list[tuple[int, float]] = []
        self.enter: dict[tuple[int, int], int] = {}
        self.leave: dict[tuple[int, int], int] = {}
        for m_id, m in enumerate(found):
            for b_id, box in enumerate(m.boxes):
                for v, lo, hi in ((box.a, box.a_lo, box.a_hi), (box.b, box.b_lo, box.b_hi)):
                    key = (id(box), v)
                    self.enter[key] = self._node(v, max(0.0, lo - stop_m))
                    self.leave[key] = self._node(v, hi)
        for line in self.events:
            line.sort()
        self.chain = []
        for line in self.events:
            self.chain += [(p[1], q[1]) for p, q in zip(line, line[1:])]

    def _node(self, v: int, s: float) -> int:
        self.nodes.append((v, s))
        self.events[v].append((s, len(self.nodes) - 1))
        return len(self.nodes) - 1

    def edges(self, m: Meeting, first: int) -> list[tuple[int, int]]:
        second = m.b if first == m.a else m.a
        return [(self.leave[(id(box), first)], self.enter[(id(box), second)]) for box in m.boxes]

    def graph(self, skip: Meeting | None = None) -> _Graph:
        g = _Graph(len(self.nodes))
        for u, v in self.chain:
            g.out[u].add(v)
        for m in self.found:
            if m.first is not None and m is not skip:
                for u, v in self.edges(m, m.first):
                    g.out[u].add(v)
        return g

    def fits(self, g: _Graph, m: Meeting, first: int) -> bool:
        added = []
        for u, v in self.edges(m, first):
            if g.reaches(v, u):
                for x, y in added:
                    g.out[x].discard(y)
                return False
            if v not in g.out[u]:
                g.out[u].add(v)
                added.append((u, v))
        for x, y in added:
            g.out[x].discard(y)
        return True

    def predict(self) -> tuple[float, float, list[float]]:
        """Latest finish, summed finish and each vehicle's finish when everyone drives free flow and waits only at gates."""
        g = self.graph()
        indegree = [0] * len(self.nodes)
        for outs in g.out:
            for v in outs:
                indegree[v] += 1
        ready = [i for i, d in enumerate(indegree) if d == 0]
        chain_prev = {v: u for u, v in self.chain}
        crossing: dict[int, list[int]] = {}
        for m in self.found:
            if m.first is not None:
                for u, v in self.edges(m, m.first):
                    crossing.setdefault(v, []).append(u)
        at = [0.0] * len(self.nodes)
        done = 0
        while ready:
            node = ready.pop()
            done += 1
            v, s = self.nodes[node]
            prev = chain_prev.get(node)
            own = _eta(self.lines[v], s) if prev is None else at[prev] + _eta(self.lines[v], s) - _eta(self.lines[v], self.nodes[prev][1])
            blocked = max((at[u] for u in crossing.get(node, ())), default=-math.inf)
            at[node] = max(own, blocked + RESTART_S)
            for nxt in g.out[node]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
        if done != len(self.nodes):
            raise ValueError('通行次序成环')
        finish = []
        for v, line in enumerate(self.events):
            total = _eta(self.lines[v], self.lengths[v])
            if line:
                s, node = line[-1]
                total = at[node] + total - _eta(self.lines[v], s)
            finish.append(total)
        return max(finish), sum(finish), finish


def _choices(m: Meeting, lengths: list[float]) -> tuple[int, ...]:
    """A vehicle parked inside a meeting at its start has to go first; one that ends inside it has to go last."""
    allowed = {m.a, m.b}
    for v, ranges in ((m.a, [(x.a_lo, x.a_hi) for x in m.boxes]), (m.b, [(x.b_lo, x.b_hi) for x in m.boxes])):
        other = m.b if v == m.a else m.a
        if min(r[0] for r in ranges) <= EDGE_M:
            allowed.discard(other)
        if max(r[1] for r in ranges) >= lengths[v] - EDGE_M:
            allowed.discard(v)
    return tuple(sorted(allowed))


def _assign(schedule: Schedule, prefer) -> Meeting | None:
    """Order every meeting, forced ones first; the meeting left with no acyclic order, or None."""
    for m in schedule.found:
        m.first = None
    g = schedule.graph()
    for m in sorted(schedule.found, key=lambda x: (len(x.choices) > 1, x.arrival)):
        for first in prefer(m):
            if schedule.fits(g, m, first):
                m.first = first
                for u, v in schedule.edges(m, first):
                    g.out[u].add(v)
                break
        else:
            return m
    return None


def compile_gates(vehicles: list[dict], radius: float, speed: float, decel: float = 2.0, trials: int = IMPROVE_TRIALS) -> dict:
    """The gates for these paths, or ``{'feasible': False, 'reason': ...}`` when no order exists and serial execution is left."""
    started = time.perf_counter()
    stop_m = speed * speed / (2 * decel) + speed * 0.1 + 0.1
    found, clearance = meetings(vehicles, radius)
    schedule = Schedule(vehicles, found, stop_m, speed)
    for m in found:
        m.choices = _choices(m, schedule.lengths)
        if not m.choices:
            return {'feasible': False, 'reason': f'{vehicles[m.a]["name"]} 与 {vehicles[m.b]["name"]} 的停车位落在彼此路线上，不存在可行次序'}
        m.arrival = min(min(_eta(schedule.lines[x.a], x.a_lo), _eta(schedule.lines[x.b], x.b_lo)) for x in m.boxes)
    def reach(m, v):
        box = min(m.boxes, key=lambda x: x.a_lo if v == m.a else x.b_lo)
        return _eta(schedule.lines[v], box.a_lo if v == m.a else box.b_lo)

    stuck = _assign(schedule, lambda m: sorted(m.choices, key=lambda v: reach(m, v)))
    if stuck:
        # First come first served can paint itself into a corner. A fixed priority between vehicles cannot: every wait then
        # points from a lower vehicle to a higher one, so only the forced orders of parked vehicles can still clash.
        tries = list(itertools.islice(itertools.permutations(range(len(vehicles))), 720))
        random.Random(0).shuffle(tries)
        for rank in tries:
            stuck = _assign(schedule, lambda m, rank=rank: sorted(m.choices, key=lambda v: rank[v]))
            if not stuck:
                break
    if stuck:
        return {'feasible': False, 'reason': f'{vehicles[stuck.a]["name"]} 与 {vehicles[stuck.b]["name"]} 的路线找不到无环的通行次序'}
    greedy = schedule.predict()
    best = greedy
    improved = True
    while improved and trials > 0:
        improved = False
        for m in found:
            if len(m.choices) < 2 or trials <= 0:
                continue
            trials -= 1
            was = m.first
            other = m.b if was == m.a else m.a
            if not schedule.fits(schedule.graph(skip=m), m, other):
                continue
            m.first = other
            trial = schedule.predict()
            if trial[:2] < best[:2]:
                best, improved = trial, True
            else:
                m.first = was
    gates: list[dict[float, list]] = [{} for _ in vehicles]
    for m in found:
        second = m.b if m.first == m.a else m.a
        for box in m.boxes:
            lo = box.a_lo if second == m.a else box.b_lo
            hi = box.a_hi if m.first == m.a else box.b_hi
            at = round(max(0.0, lo - stop_m), 3)
            gates[second].setdefault(at, {})
            name = vehicles[m.first]['name']
            gates[second][at][name] = max(gates[second][at].get(name, 0.0), round(hi, 3))
    return {'feasible': True, 'policy': POLICY, 'clearance_m': round(clearance, 3), 'stop_m': round(stop_m, 3),
            'meetings': len(found), 'boxes': sum(len(m.boxes) for m in found),
            'gates': {v['name']: [{'s': at, 'after': sorted(after.items())} for at, after in sorted(gates[i].items())] for i, v in enumerate(vehicles)},
            'predicted_makespan_s': round(best[0], 2), 'predicted_finish_s': {v['name']: round(best[2][i], 2) for i, v in enumerate(vehicles)},
            'greedy_makespan_s': round(greedy[0], 2), 'free_flow_s': {v['name']: round(_eta(schedule.lines[i], schedule.lengths[i]), 2) for i, v in enumerate(vehicles)},
            'compile_s': round(time.perf_counter() - started, 3)}


def estimate_scope(inst: dict, routes: list[dict]) -> str | None:
    """The estimator models only the built-in homogeneous differential-drive simulation."""
    from . import field_physics, fieldworld
    vehicle = inst.get('vehicles') or {}
    if vehicle.get('fleet'):
        return 'Explicit fleet classes have unmodelled per-class speed, size or kinematics.'
    if inst.get('ground'):
        return 'Ground-dependent execution is outside the ideal homogeneous model.'
    if not routes or len(routes) > fieldworld.MAX_SIM_VEHICLES:
        return 'Vehicle count is outside the validated simulation range.'
    speed = float(vehicle.get('speed_mps') or 1.0)
    radius = float(inst.get('robot_radius') or 0)
    if not math.isfinite(speed) or not 0 < speed <= field_physics.LIMITS['max_speed_mps']:
        return 'Speed is outside the built-in simulation range.'
    if not math.isfinite(radius) or radius < fieldworld.ROBOT_RADIUS:
        return 'Planning clearance is smaller than the built-in robot footprint.'
    return None


def estimate(inst: dict, routes: list[dict]) -> dict:
    """Optional same-model prediction, not a physical measurement or a general fleet estimate.

    The recorded 36-task benchmark was within 2 percent of its physics engine. That does not establish
    accuracy for a new instance. Unsupported or unschedulable cases retain an explicit reason, never a zero.
    """
    from . import fieldworld
    reason = estimate_scope(inst, routes)
    if reason:
        return {'model': ESTIMATE_MODEL, 'status': 'unsupported', 'reason': reason}
    try:
        vehicles = [{'name': v.name, 'path': v.path, 'tasks': v.tasks} for v in fieldworld.plan_vehicles(inst, routes)]
    except ValueError as exc:
        return {'model': ESTIMATE_MODEL, 'status': 'unavailable', 'reason': str(exc)}
    result = compile_gates(vehicles, fieldworld.ROBOT_RADIUS, float(inst['vehicles'].get('speed_mps') or 1.0), trials=200)
    if not result['feasible']:
        return {'model': ESTIMATE_MODEL, 'status': 'unavailable', 'reason': result['reason']}
    return {'model': ESTIMATE_MODEL, 'status': 'estimated',
            **{k: result[k] for k in ('predicted_makespan_s', 'meetings', 'free_flow_s', 'compile_s')}}


def apply(plan: dict) -> dict:
    """The same plan under concurrent execution; a plan with no valid order stays serial and says why."""
    limits = (plan.get('physics') or {}).get('limits') or {}
    result = compile_gates(plan['vehicles'], plan['robot_radius_m'], float(plan['speed_mps']), limits.get('decel_mps2', 2.0))
    if not result['feasible']:
        return {**plan, 'traffic': {**plan['traffic'], 'coordination_refused': result['reason']}}
    return {**plan, 'traffic': {**plan['traffic'], **result, 'scope': '路径不变，只决定车辆在路线相遇处的先后；事件依赖图无环；进展仍依赖沿路径推进、有限延迟和有界跟踪/制动误差，运行时另有车距保护；不代表实车安全证明'}}
