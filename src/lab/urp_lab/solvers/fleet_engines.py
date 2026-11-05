"""PyVRP / OR-Tools fleet engines: total-distance and makespan models."""
from __future__ import annotations

from collections import Counter
import math
import time

import numpy as np

from .fleet_common import depot_list, fleet_classes, vehicle_table
from .fleet_trace import makespan_callback, makespan_sample as _makespan_sample


def _scaled_costs(values, scale, max_arc_cost, reachable=None):
    """Round finite costs only; zero placeholders must be excluded by the model.

    Bound before the int64 cast (and compare as Python ints at its upper edge).
    A supplied reachability mask also detects arithmetic overflow on real arcs,
    rather than silently treating that overflow as a newly missing edge.
    """
    values = np.asarray(values, dtype=float)
    if np.isnan(values).any() or (values < 0).any():
        raise ValueError('求解器距离/时间须为非负有限值或不可达的正无穷')
    reachable = np.isfinite(values) if reachable is None else reachable
    with np.errstate(over='ignore', invalid='ignore'):
        rounded = np.rint(values[reachable] * scale)
    if not np.isfinite(rounded).all() or (rounded.size and int(rounded.max()) > max_arc_cost):
        raise ValueError('求解器距离/时间超出安全整数标度范围')
    costs = np.zeros(values.shape, dtype=np.int64)
    costs[reachable] = rounded.astype(np.int64)
    return costs, reachable


def _forbid_unreachable_arcs(routing, manager, reachable):
    """Forbid absent arcs in NextVar domains, including every start/end copy."""
    if reachable.all():
        return
    nodes = [manager.IndexToNode(i) for i in range(manager.GetNumberOfIndices())]
    sources = {}
    for i in range(routing.Size()):  # end indices have no NextVar
        sources.setdefault(nodes[i], []).append(i)
    for node, indices in sources.items():
        blocked = np.flatnonzero(~reachable[node, nodes]).tolist()
        for i in indices:
            routing.NextVar(i).RemoveValues(blocked)


def _disable_unused_depots(routing, manager, nd, homes):
    """Original depots without a home vehicle are not service/reload clients.

    RoutingIndexManager otherwise exposes them as mandatory ordinary nodes.
    Make them optional AND force inactivity: a zero penalty alone still permits
    gratuitous depot visits. Actual reload copies are modelled separately.
    """
    for depot in set(range(nd)) - set(homes):
        idx = manager.NodeToIndex(depot)
        routing.AddDisjunction([idx], 0)
        routing.ActiveVar(idx).SetValue(0)


def metaheuristic(engine, inst, D, demand, params, K):
    """Minimise distance; return (trips, trip_class, trip_depot, trace).
    Heterogeneous classes retain capacities and round-robin home depots.
    Multi-trip mode allows n pseudo-routes per (class, depot); single-trip uses
    the real fleet. Single-depot instances retain legacy depot 0 ownership.
    """
    classes = fleet_classes(inst)
    multi = inst['vehicles'].get('trips', 'multi') == 'multi'
    nd = len(depot_list(inst))
    n = len(inst['tasks'])
    limit = float(params.get('time_limit_s', 3))
    trace = []
    real = [(ci, di) for _vi, ci, _cls, di in vehicle_table(inst)]
    pseudo = ([key for key in dict.fromkeys(real) for _ in range(n)]  # n routes per (class, depot)
              if multi else real)
    if engine == 'pyvrp':
        from pyvrp import Model
        from pyvrp.constants import MAX_VALUE
        from pyvrp.stop import MaxRuntime
        # Native missing edges use MAX_VALUE. A route has at most n + 1 arcs:
        # this nonbinding bound on every finite route makes ANY missing edge
        # infeasible, rather than just expensive. Reject unsafe scales explicitly.
        M, reachable = _scaled_costs(D[:nd + n, :nd + n], 100, (MAX_VALUE - 1) // (n + 1))
        max_distance = int(M.max()) * (n + 1)
        model = Model()
        locs = [model.add_location(i, 0) for i in range(nd + n)]
        handles = [model.add_depot(locs[i]) for i in range(nd)]
        for i in range(n):
            model.add_client(locs[nd + i], delivery=demand[nd + i])
        counts = Counter(pseudo)
        keys = list(counts)                                       # insertion order == table order
        for ci, di in keys:
            model.add_vehicle_type(num_available=counts[(ci, di)], capacity=classes[ci]['capacity'],
                                   start_depot=handles[di], end_depot=handles[di], max_distance=max_distance)
        for i in range(nd + n):
            for j in range(nd + n):
                if reachable[i, j]:
                    model.add_edge(locs[i], locs[j], distance=int(M[i, j]))
        result = model.solve(MaxRuntime(limit), seed=int(params.get('seed', 42)), display=False, collect_stats=True)
        elapsed, best = 0.0, math.inf
        for dt, datum in zip(result.stats.runtimes, result.stats.data):
            elapsed += dt
            if datum.best_feas and datum.best_cost < best:
                best = datum.best_cost
                trace.append([round(elapsed, 4), best / 100])
        if not result.best.is_feasible():
            return None, None, None, trace
        trips = [[a.idx + nd for a in r if a.is_client()] for r in result.best.routes()]   # a.idx is the 0-based client index
        typed = [keys[int(r.vehicle_type())] for r in result.best.routes()]
        return trips, [ci for ci, _ in typed], [di for _, di in typed], trace
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    # Only the n tasks may be active ordinary nodes (unused depots are disabled
    # below), so at most n + number-of-vehicles arcs contribute to route cost.
    M, reachable = _scaled_costs(D[:nd + n, :nd + n], 100,
                                np.iinfo(np.int64).max // max(1, n + len(pseudo)))
    starts = [di for _ci, di in pseudo]
    caps = [classes[ci]['capacity'] for ci, _di in pseudo]
    manager = pywrapcp.RoutingIndexManager(nd + n, len(pseudo), starts, starts)
    routing = pywrapcp.RoutingModel(manager)
    _disable_unused_depots(routing, manager, nd, starts)
    _forbid_unreachable_arcs(routing, manager, reachable)
    cb = routing.RegisterTransitCallback(lambda i, j: int(M[manager.IndexToNode(i), manager.IndexToNode(j)]))
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    dem = routing.RegisterUnaryTransitCallback(lambda i: demand[manager.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(dem, 0, caps, True, 'Capacity')
    t0 = time.perf_counter()

    def on_solution():
        v = routing.CostVar().Value() / 100
        if not trace or v < trace[-1][1]:
            trace.append([round(time.perf_counter() - t0, 4), v])
    routing.AddAtSolutionCallback(on_solution)
    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.FromMilliseconds(max(1, round(limit * 1000)))
    sol = routing.SolveWithParameters(p)
    if sol is None:
        return None, None, None, trace
    trips, trip_class, trip_depot = [], [], []
    for v, (ci, di) in enumerate(pseudo):
        idx, r = routing.Start(v), []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node >= nd:
                r.append(node)          # graph node numbers already offset the depots
            idx = sol.Value(routing.NextVar(idx))
        if r:
            trips.append(r)
            trip_class.append(ci)
            trip_depot.append(di)
    return trips, trip_class, trip_depot, trace


def ortools_makespan(inst, D, demand, params, K, multi, trace_diagnostics=None):
    """OR-Tools on the real fleet: each vehicle may return to the depot to unload (optional reload
    nodes, as in the OR-Tools reload sample), and a global span cost on the time dimension
    minimises the latest finish. Returns trips, the vehicle of each trip and the trace (seconds).

    Heterogeneous fleets get per-class travel-time matrices (speed) and per-vehicle capacities;
    reload nodes unload down to zero for every class."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    classes = fleet_classes(inst)
    table = vehicle_table(inst)
    caps = [cls['capacity'] for _vi, _ci, cls, _di in table]
    home_of = [di for _vi, _ci, _cls, di in table]     # round-robin home depot per vehicle (MDVRP starts/ends)
    nd = len(depot_list(inst))
    n = len(inst['tasks'])
    service = [0.0] * nd + [float(t.get('service_s', 0) or 0) for t in inst['tasks']]
    nu = len(inst.get('unload_points') or [])
    cap_ref = min(caps) if len(classes) > 1 else inst['vehicles']['capacity']
    per_depot = min(n, max(0, math.ceil(sum(demand) / cap_ref))) if multi else 0
    reloads = (nd + nu) * per_depot
    # Only tasks are mandatory. Supply coordinates enter through optional reload copies,
    # never as mandatory zero-demand clients. orig maps model indices to graph indices.
    task_end = nd + n
    orig = list(range(task_end)) + [d for d in range(nd) for _ in range(per_depot)] \
        + [nd + n + u for u in range(nu) for _ in range(per_depot)]
    serv = np.array([service[o] if o < nd + n else 0.0 for o in orig])
    distances = D[np.ix_(orig, orig)]
    reachable = np.isfinite(distances)
    # Bound the horizon and the complete objective (sum of route times plus
    # 100 * global span) before int64 conversion and Python -> C++ calls.
    arc_limit = np.iinfo(np.int64).max // ((len(orig) + 2) * (K + 100))
    T = {}
    for ci, cls in enumerate(classes):
        with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
            times = distances / cls['speed_mps'] + serv[:, None]
        np.fill_diagonal(times, 0)
        T[ci], _ = _scaled_costs(times, 10, arc_limit, reachable)
    load = [0] * nd + list(demand[nd:]) + [-max(caps)] * reloads
    manager = pywrapcp.RoutingIndexManager(task_end + reloads, K, home_of, home_of)
    routing = pywrapcp.RoutingModel(manager)
    _disable_unused_depots(routing, manager, nd, home_of)
    _forbid_unreachable_arcs(routing, manager, reachable)
    cbs = [routing.RegisterTransitCallback(lambda i, j, m=T[table[v][1]]: int(m[manager.IndexToNode(i), manager.IndexToNode(j)]))
           for v in range(K)]
    for v, cb in enumerate(cbs):
        routing.SetArcCostEvaluatorOfVehicle(cb, v)
    horizon = int(max(m.max() for m in T.values()) * (task_end + reloads + 2))
    routing.AddDimensionWithVehicleTransits(cbs, 0, horizon, True, 'Time')
    time_dim = routing.GetDimensionOrDie('Time')
    time_dim.SetGlobalSpanCostCoefficient(100)
    dem = routing.RegisterUnaryTransitCallback(lambda i: int(load[manager.IndexToNode(i)]))
    routing.AddDimensionWithVehicleCapacity(dem, max(caps), caps, True, 'Capacity')
    cap_dim = routing.GetDimensionOrDie('Capacity')
    for node in range(nd, nd + n):
        cap_dim.SlackVar(manager.NodeToIndex(node)).SetValue(0)
    for node in range(task_end, task_end + reloads):
        idx = manager.NodeToIndex(node)
        cap_dim.SlackVar(idx).SetRange(0, max(caps))  # unloading resets the load to zero for any class
        routing.AddDisjunction([idx], 0)               # reloads are optional and free
        routing.solver().Add(cap_dim.CumulVar(idx) >= routing.ActiveVar(idx))
        # A reload separates nonempty trips. Inactive copies may self-loop; active
        # copies must lead to a task, excluding empty reload chains and terminal detours.
        routing.solver().Add(routing.NextVar(idx).Member(
            [idx] + [manager.NodeToIndex(t) for t in range(nd, nd + n)]))
        source = orig[node]
        if source < nd and nd > 1:                     # MDVRP: a depot's reloads serve only its home vehicles
            routing.solver().Add(routing.VehicleVar(idx).Member([-1] + [v for v in range(K) if home_of[v] == source]))
        # unload-point reload copies are open to every vehicle (mid-route supply, not home-bound)
    t0, trace = time.perf_counter(), []

    routing.AddAtSolutionCallback(makespan_callback(
        routing, time_dim, K, t0, trace, trace_diagnostics, sample=_makespan_sample))
    p = pywrapcp.DefaultRoutingSearchParameters()
    # PATH_CHEAPEST_ARC cannot complete a first solution when the per-depot reload membership
    # constraints bind (multi-depot with required reloads); insertion heuristics respect them.
    p.first_solution_strategy = (routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION if nd > 1
                                 else routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.FromMilliseconds(max(1, round(float(params.get('time_limit_s', 3)) * 1000)))
    sol = routing.SolveWithParameters(p)
    if sol is None:
        return None, None, None, trace
    trips, owner, reload_at = [], [], []
    for v in range(K):
        idx, cur = routing.Start(v), []
        while True:
            idx = sol.Value(routing.NextVar(idx))
            node = manager.IndexToNode(idx)
            if routing.IsEnd(idx) or node >= nd + n:
                if cur:
                    trips.append(cur)
                    owner.append(v)
                    reload_at.append(orig[node])  # this trip ends at this reload or home
                cur = []
                if routing.IsEnd(idx):
                    break
            elif node >= nd:
                cur.append(node)
            else:
                raise ValueError('求解器返回了未启用的车库访问')
    return trips, owner, reload_at, trace

