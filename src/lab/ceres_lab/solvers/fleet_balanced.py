"""PyVRP with the latest finish as the goal: reloads inside a route and a shift limit tightened by bisection."""
from __future__ import annotations

import math
import time

import numpy as np

from .. import time_windows
from .fleet_common import depot_list, fleet_classes, vehicle_table

STOP_S = 1.5        # stopping at a task and pulling away again, on top of the dwell


def drive_seconds(graph, size: int, speed: float) -> np.ndarray:
    """Seconds between stops as the follower really drives them: distance at speed, plus stopping and turning on the spot at
    every sharp corner of the shortest path. ``field_coordination.timeline`` is the same model. Pairs the graph does not
    connect stay infinite; a graph keeps its matrices, because a time-window search runs on the same graph more than once."""
    from ..field_coordination import timeline
    cache = graph.__dict__.setdefault('drive_cache', {})
    if (size, speed) in cache:
        return cache[(size, speed)]
    out = np.zeros((size, size))
    for i in range(size):
        for j in range(size):
            if i != j:
                out[i, j] = timeline({'path': graph.path(i, j)}, speed)[1][-1] if np.isfinite(graph.dist[i, j]) else np.inf
    cache[(size, speed)] = out
    return out


def tw_bounds(window, margin_s: float) -> dict:
    """PyVRP's window on the 1/100 s scale, the deadline pulled forward by ``margin_s``. Rounded inwards, never past it."""
    if window is None:
        return {}
    early, late = window
    bounds = {'tw_early': math.ceil(early * 100)}
    if late is not None:
        bounds['tw_late'] = max(bounds['tw_early'], math.floor((late - margin_s) * 100))
    return bounds


def pyvrp_makespan(inst, D, demand, params, K, multi, graph=None, margin_s=0.0):
    """PyVRP on the real fleet with the latest finish as the goal. Vehicles reload at their depot inside one route (PyVRP's
    multi-trip routes), so trips are assigned by the search and not afterwards. PyVRP has no minimax objective, so a shift
    limit is tightened by bisection: each step asks the full search for a plan that finishes within the limit, and the
    smallest limit it meets wins. With the travel graph, durations are what the follower takes and not distance over speed,
    so the search balances the real working time. Same returns as ``ortools_makespan``.

    Task windows go in as PyVRP's own ``tw_early`` / ``tw_late``. PyVRP may delay a route's start to avoid waiting, so with
    windows the limit binds when the route ends and not how long it lasts; the fleet still leaves together and waits at the
    task, which ends at the same time. When the distance is the goal the search keeps the budget and nothing is bisected.
    If no plan meets the windows, a plan that breaks only the deadlines comes back for the checker to name what it misses."""
    begun = time.perf_counter()
    from pyvrp import Model
    from pyvrp.solve import solve as search
    from pyvrp.stop import MaxRuntime
    classes = fleet_classes(inst)
    table = vehicle_table(inst)
    depots = depot_list(inst)
    nd, n = len(depots), len(inst['tasks'])
    size = nd + n
    dist = np.rint(D[:size, :size] * 100).astype(np.int64)
    service = [int(round(100 * (float(t.get('service_s', 0) or 0) + (STOP_S if graph is not None else 0)))) for t in inst['tasks']]
    limit = float(params.get('time_limit_s', 3))
    seed = int(params.get('seed', 42))
    windows = [time_windows.of(t) for t in inst['tasks']]
    windowed = any(windows)
    bisect = not windowed or inst.get('objective') == 'makespan'
    groups: dict = {}
    for vi, ci, _cls, di in table:
        groups.setdefault((ci, di), []).append(vi)
    keys = list(groups)
    fastest = max(c['speed_mps'] or 1.0 for c in classes)

    def problem(deadlines=True):
        model = Model()
        locs = [model.add_location(i, 0) for i in range(size)]
        handles = [model.add_depot(locs[i]) for i in range(nd)]
        for i in range(n):
            window = windows[i] if deadlines or windows[i] is None else (windows[i][0], None)
            model.add_client(locs[nd + i], delivery=demand[nd + i], service_duration=service[i], **tw_bounds(window, margin_s))
        profiles = [model.add_profile() for _ in classes]
        for ci, di in keys:
            extra = {'reload_depots': [handles[di]]} if multi else {}
            model.add_vehicle_type(num_available=len(groups[(ci, di)]), capacity=classes[ci]['capacity'], start_depot=handles[di], end_depot=handles[di],
                                   profile=profiles[ci], **extra)
        for ci, profile in enumerate(profiles):
            speed = classes[ci]['speed_mps'] or 1.0
            seconds = D[:size, :size] / speed if graph is None else drive_seconds(graph, size, speed)
            times = np.rint(seconds * 100).astype(np.int64)
            for i in range(size):
                for j in range(size):
                    model.add_edge(locs[i], locs[j], distance=int(dist[i, j]), duration=int(times[i, j]), profile=profile)
        return model.data()

    data = problem()
    started = begun
    trace = []

    closest = []

    def span(solution):
        return max(r.end_time() if windowed else r.duration() for r in solution.routes())

    def within(v, shift):
        return v.replace(tw_late=shift, start_late=shift) if windowed else v.replace(shift_duration=shift)

    def attempt(shift, seconds):
        limited = data if shift is None else data.replace(vehicle_types=[within(v, shift) for v in data.vehicle_types()])
        result = search(limited, MaxRuntime(seconds), seed=seed, collect_stats=False, display=False)
        if result.best.is_feasible():
            return result.best
        closest.append(result.best)
        return None

    def plan_of(solution):
        trips, owner, reload_at = [], [], []
        used = {key: 0 for key in keys}
        for route in solution.routes():
            key = keys[int(route.vehicle_type())]
            vehicle = groups[key][used[key]]
            used[key] += 1
            current: list[int] = []
            for activity in route:
                if activity.is_client():
                    current.append(activity.idx + nd)      # activity.idx is the 0-based client index
                elif current:
                    trips.append(current)
                    owner.append(vehicle)
                    reload_at.append(key[1])
                    current = []
            if current:
                trips.append(current)
                owner.append(vehicle)
                reload_at.append(key[1])
        return trips, owner, reload_at, trace

    best = attempt(None, limit * (.2 if not windowed else .5 if bisect else .7))
    if best is None:
        near = closest[-1]
        if not windowed:
            return None, None, None, trace
        if near.is_complete() and not near.has_excess_load():
            return plan_of(near)
        # The search traded load for time, so that plan says nothing true about lateness. One that keeps every other constraint does.
        left = max(.05, limit - (time.perf_counter() - started))
        relaxed = search(problem(deadlines=False), MaxRuntime(left), seed=seed, collect_stats=False, display=False).best
        return plan_of(relaxed) if relaxed.is_feasible() else (None, None, None, trace)
    hi = span(best)
    trace.append([round(time.perf_counter() - started, 4), hi / 100])
    if not bisect:
        return plan_of(best)
    # No vehicle can finish before the longest single errand, nor the fleet before its share of the unavoidable work.
    nearest = np.where(np.eye(size, dtype=bool), np.iinfo(np.int64).max, dist).min(axis=1)[nd:]
    errand = max(int(dist[:nd, nd + i].min()) * 2 / 100 / fastest * 100 + service[i] for i in range(n))
    lo = int(max(errand, (sum(service) + nearest.sum() / fastest) / max(K, 1)))
    rounds = 4
    for step in range(rounds):
        left = limit - (time.perf_counter() - started)
        if left <= .02 or hi - lo <= max(100, hi // 200):
            break
        mid = (lo + hi) // 2
        found = attempt(mid, left / (rounds - step))
        if found is not None and span(found) < hi:
            best, hi = found, span(found)
            trace.append([round(time.perf_counter() - started, 4), hi / 100])
        else:
            lo = mid
    return plan_of(best)
