"""Sequencing: which line after which, in which direction, on which machine, with refills where.

All algorithms produce the same thing, an ordered list of oriented lines per machine; `split` then places the
refill stops optimally for that order (Prins-style shortest path over a sliding window, as in the giant-tour +
split scheme of Hossain & Zahin 2026). Only the ordering differs between algorithms, so comparisons isolate it.
"""
from __future__ import annotations

import math
import random
import time
from collections import deque

from .model import INF, Field

BIG = 1e7


class Plan:
    """Search-side view of a Field: plain lists, because `split` runs hundreds of thousands of times."""

    def __init__(self, F: Field):
        self.F = F
        rig, inst = F.rig, F.inst
        res = rig['resource']
        self.Q = res['capacity'] if res['kind'] != 'none' and res['capacity'] else None
        self.E = rig.get('endurance_s')
        self.objective = inst.get('objective', 'nonworking')
        self.K = inst['machines']
        self.n = F.n
        self.refresh()

    def refresh(self):
        F = self.F
        v_x = F.rig['speed_transfer_mps']
        n2 = 2 * F.n
        cost = F.cost.copy()
        tcost = F.t_cost.copy()
        for a, b in F.forbidden:
            if a >= 0 and b >= 0:
                cost[a, b] = INF
                tcost[a, b] = INF
        self.C = [[min(x, BIG) for x in row] for row in cost.tolist()]
        self.T = [[min(x, BIG) for x in row] for row in tcost.tolist()]
        self.work_t = (F.work_m / F.rig['speed_work_mps']).tolist()
        self.dem = F.demand.tolist()
        S = len(F.pts)
        self.to = [[min(x, BIG) for x in row] for row in F.to_line.tolist()]
        self.fro = [[min(x, BIG) for x in row] for row in F.from_line.tolist()]
        for a, b in F.forbidden:
            if a < 0:
                for row in self.to:
                    row[b] = BIG
            elif b < 0:
                for row in self.fro:
                    row[a] = BIG
        service = F.inst['stations'][0].get('service_s', 0.) if F.inst['stations'] else 0.
        self.service = service
        # refill link o_prev → station → o_next, cheapest over stations (index 0 is the depot itself)
        self.link_from = [[self.fro[s][o] for o in range(n2)] for s in range(1, S)] or [self.fro[0]]
        self.link_to = [[self.to[s][o] for o in range(n2)] for s in range(1, S)] or [self.to[0]]
        self.v_x = v_x

    def split(self, order, want_cuts=False):
        """Best refill placement for one machine's order → (distance, seconds, cuts)."""
        m = len(order)
        if not m:
            return (0., 0., []) if want_cuts else (0., 0.)
        C, T = self.C, self.T
        mk = self.objective == 'makespan'
        M = T if mk else C
        v = self.v_x
        scale = (1 / v) if mk else 1.
        pre = [0.] * m
        pt = [0.] * m
        for x in range(1, m):
            pre[x] = pre[x - 1] + M[order[x - 1]][order[x]]
            pt[x] = pt[x - 1] + T[order[x - 1]][order[x]]
        Q, E = self.Q, self.E
        wt = self.work_t
        depot_in = self.to[0][order[0]] * scale
        depot_out = self.fro[0][order[-1]] * scale
        if Q is None and E is None:
            total = pre[-1] + depot_in + depot_out
            return self._finish(order, total, [], want_cuts, C, T)
        dem = self.dem
        cum = [0.] * (m + 1)
        cwt = [0.] * (m + 1)
        for x, o in enumerate(order):
            cum[x + 1] = cum[x] + dem[o]
            cwt[x + 1] = cwt[x] + wt[o]
        service = self.service if mk else 0.
        lf, lt = self.link_from, self.link_to
        dp = [INF] * (m + 1)
        prev = [-1] * (m + 1)
        dp[0] = 0.
        queue = deque()
        lo = 0
        margin = 2 * max(self.fro[0]) / v if E is not None else 0.
        E_eff = (E - margin) if E is not None else None
        for j in range(1, m + 1):
            i = j - 1
            start = depot_in if i == 0 else min(f[order[i - 1]] + t[order[i]] for f, t in zip(lf, lt)) * scale + service
            val = dp[i] + start - pre[i]
            while queue and queue[-1][0] >= val:
                queue.pop()
            queue.append((val, i))
            while lo < j - 1 and ((Q is not None and cum[j] - cum[lo] > Q + 1e-9)
                                  or (E_eff is not None and (cwt[j] - cwt[lo]) + pt[j - 1] - pt[lo] > E_eff)):
                lo += 1
            while queue and queue[0][1] < lo:
                queue.popleft()
            if not queue:
                return (BIG * 10, BIG * 10, []) if want_cuts else (BIG * 10, BIG * 10)
            dp[j] = queue[0][0] + pre[j - 1]
            prev[j] = queue[0][1]
        total = dp[m] + depot_out
        cuts = []
        j = m
        while j > 0:
            cuts.append(prev[j])
            j = prev[j]
        cuts = sorted(c for c in cuts if c > 0)
        return self._finish(order, total, cuts, want_cuts, C, T)

    def _finish(self, order, total, cuts, want_cuts, C, T):
        wt = sum(self.work_t[o] for o in order)
        if self.objective == 'makespan':
            seconds = total + wt
            out = (seconds, seconds, cuts)
        else:
            out = (total, 0., cuts)
        return out if want_cuts else out[:2]

    def score(self, orders):
        parts = [self.split(o) for o in orders]
        if self.objective == 'makespan':
            return max(p[0] for p in parts) + .01 * sum(p[0] for p in parts)
        return sum(p[0] for p in parts)


def rows_in_order(F: Field) -> list[list[int]]:
    """Line indices in boustrophedon cells: a row split by an obstacle continues in its own cell, not across it."""
    by_row: dict[int, list[int]] = {}
    for i, line in enumerate(F.lines):
        by_row.setdefault(line['row'], []).append(i)
    h = F.h

    def span(i):
        line = F.lines[i]
        a = line['p1'][0] * h[0] + line['p1'][1] * h[1]
        b = line['p2'][0] * h[0] + line['p2'][1] * h[1]
        return min(a, b), max(a, b)

    cells: list[list[int]] = []
    open_cells: list[int] = []        # cell index for each piece of the previous row
    prev: list[int] = []
    for row in sorted(by_row):
        pieces = by_row[row]
        current = []
        same_shape = len(pieces) == len(prev)
        for p, i in enumerate(pieces):
            lo, hi = span(i)
            match = None
            if same_shape:
                plo, phi = span(prev[p])
                if min(hi, phi) - max(lo, plo) > 0:
                    match = open_cells[p]
            if match is None:
                cells.append([])
                match = len(cells) - 1
            cells[match].append(i)
            current.append(match)
        prev, open_cells = pieces, current
    return cells


def _oriented(sequence: list[int]) -> list[int]:
    return [2 * i + (k % 2) for k, i in enumerate(sequence)]


def _partition(F: Field, sequence: list[int], K: int) -> list[list[int]]:
    """Contiguous blocks of about equal work."""
    total = sum(F.lines[i]['length_m'] for i in sequence)
    out, acc, block = [], 0., []
    for x, i in enumerate(sequence):
        block.append(i)
        acc += F.lines[i]['length_m']
        if acc >= total * (len(out) + 1) / K - 1e-9 and len(out) < K - 1:
            out.append(block)
            block = []
    out.append(block)
    return [b for b in out if b]


def sequential(F: Field, K: int) -> list[list[int]]:
    """Every pass in field order, turning into the adjacent row: what a driver or a plain AB-line does."""
    flat = [i for row in rows_in_order(F) for i in row]
    return [_oriented(b) for b in _partition(F, flat, K)]


def skip_step(F: Field) -> int:
    return max(2, math.ceil(2 * F.radius / F.pitch) + 1)


def skip_pattern(F: Field, K: int, step: int | None = None) -> list[list[int]]:
    """Alternate over a block so that every turn is a clean P turn: step = ⌈2r/w⌉ + 1 rows (Bochtis 2008 block patterns)."""
    k = step or skip_step(F)
    rows = [i for row in rows_in_order(F) for i in row]
    out = []
    for block in _partition(F, rows, K):
        seq = []
        for s0 in range(0, len(block), 2 * k):
            chunk = block[s0:s0 + 2 * k]
            half = (len(chunk) + 1) // 2
            lo, hi = chunk[:half], chunk[half:]
            for a in range(half):
                seq.append(lo[a])
                if a < len(hi):
                    seq.append(hi[a])
        out.append(_oriented(seq))
    return out


def complete(orders: list[list[int]], n: int) -> list[list[int]]:
    """A time-limited first solution can leave lines out; they go last, and the checker judges the result."""
    if not orders:
        return orders
    served = {o // 2 for order in orders for o in order}
    orders[-1].extend(2 * i for i in range(n) if i not in served)
    return orders


def nearest(plan: Plan, K: int) -> list[list[int]]:
    """Greedy: always the cheapest next line end. A third start point, unlike the two field-order patterns."""
    F = plan.F
    left = set(range(F.n))
    order: list[int] = []
    cur = min(range(2 * F.n), key=lambda o: plan.to[0][o])
    while True:
        order.append(cur)
        left.discard(cur // 2)
        if not left:
            break
        row = plan.C[cur]
        cur = min((o for i in left for o in (2 * i, 2 * i + 1)), key=lambda o: row[o])
    if K == 1:
        return [order]
    sequence = [o // 2 for o in order]
    return [[o for o in order if o // 2 in set(b)] for b in _partition(F, sequence, K)]


def anneal(plan: Plan, start: list[list[int]], seed: int, deadline: float, iterations: int | None, trace: list):
    rng = random.Random(seed)
    F = plan.F
    orders = [list(o) for o in start]
    while len(orders) < plan.K:
        orders.append([])
    parts = [plan.split(o)[0] for o in orders]

    def total(p):
        return max(p) + .01 * sum(p) if plan.objective == 'makespan' else sum(p)

    cur = total(parts)
    best, best_orders = cur, [list(o) for o in orders]
    t0 = time.perf_counter()
    # Cycles of cooling, each restarting from the best plan at a lower temperature, so a larger budget keeps paying.
    cycle = max(20000, 400 * F.n)
    temp0 = max(cur * .03, 1.)
    it = 0
    last = -1.
    best_at_cycle, idle_cycles, phase = best, 0, 0
    while True:
        if iterations:
            if it >= iterations:
                break
        elif time.perf_counter() >= deadline:
            break
        frac = phase / cycle
        temp = temp0 * (1 - frac) ** 2 + 1e-9
        it += 1
        phase += 1
        if phase >= cycle:
            phase = 0
            temp0 *= .7
            orders = [list(o) for o in best_orders]
            parts = [plan.split(o)[0] for o in orders]
            cur = best
            idle_cycles = idle_cycles + 1 if best >= best_at_cycle - 1e-9 else 0
            best_at_cycle = best
            if idle_cycles >= 3 and not iterations:
                break
        a = rng.randrange(len(orders))
        src = orders[a]
        if not src:
            continue
        move = rng.random()
        b = a
        ns = list(src)
        nd = None
        if move < .4 and len(ns) >= 2:
            i, j = sorted(rng.sample(range(len(ns) + 1), 2))
            ns[i:j] = [o ^ 1 for o in reversed(ns[i:j])]
        elif move < .7:
            size = 1 if rng.random() < .7 else min(2, len(ns))
            i = rng.randrange(len(ns) - size + 1)
            chunk = ns[i:i + size]
            del ns[i:i + size]
            if rng.random() < .5:
                chunk = [o ^ 1 for o in reversed(chunk)]
            if plan.K > 1 and rng.random() < .4:
                b = rng.randrange(len(orders))
            if b == a:
                p = rng.randrange(len(ns) + 1)
                ns[p:p] = chunk
            else:
                nd = list(orders[b])
                p = rng.randrange(len(nd) + 1)
                nd[p:p] = chunk
        elif move < .85:
            i = rng.randrange(len(ns))
            ns[i] ^= 1
        elif len(ns) >= 2:
            i, j = rng.sample(range(len(ns)), 2)
            ns[i], ns[j] = ns[j], ns[i]
        pa = plan.split(ns)[0]
        pb = plan.split(nd)[0] if nd is not None else None
        new_parts = list(parts)
        new_parts[a] = pa
        if pb is not None:
            new_parts[b] = pb
        val = total(new_parts)
        if val <= cur or rng.random() < math.exp(min(0., (cur - val) / temp)):
            orders[a] = ns
            if nd is not None:
                orders[b] = nd
            parts, cur = new_parts, val
            if cur < best - 1e-9:
                best, best_orders = cur, [list(o) for o in orders]
                t = time.perf_counter() - t0
                if t - last > .02:
                    trace.append([round(t, 4), round(best, 3)])
                    last = t
    return [o for o in best_orders if o], it


def ortools_sequence(plan: Plan, deadline_s: float, seed: int) -> list[list[int]]:
    """Independent sequencer: oriented lines as disjunctions, one virtual vehicle per trip, capacity as a dimension."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    F = plan.F
    n2 = 2 * F.n
    scale = 100
    N = n2 + 1
    big = int(BIG)
    total = sum(x.get('demand', 0.) for x in F.lines)
    trips = plan.K
    if plan.Q:
        trips = max(plan.K, math.ceil(total / plan.Q) + 2)
    manager = pywrapcp.RoutingIndexManager(N, trips, 0)
    routing = pywrapcp.RoutingModel(manager)
    work = plan.C
    sid = 1 if len(F.pts) > 1 else 0
    to, fro = plan.to[sid], plan.fro[sid]

    def arc(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        if a == 0 and b == 0:
            return 0
        if a == 0:
            return min(big, int(to[b - 1] * scale))
        if b == 0:
            return min(big, int(fro[a - 1] * scale))
        return min(big, int(work[a - 1][b - 1] * scale))

    cb = routing.RegisterTransitCallback(arc)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    for i in range(F.n):
        # Dropping a line must never be cheaper than any route that serves it.
        routing.AddDisjunction([manager.NodeToIndex(1 + 2 * i), manager.NodeToIndex(2 + 2 * i)], big * 100000, 1)
    if plan.Q:
        units = 1000
        dem = [0] + [int(math.ceil(d * units)) for d in plan.dem]

        def demand(i):
            return dem[manager.IndexToNode(i)]

        dcb = routing.RegisterUnaryTransitCallback(demand)
        routing.AddDimension(dcb, 0, int(plan.Q * units), True, 'load')
    if plan.objective == 'makespan':
        tmat = plan.T
        wt = plan.work_t
        v = plan.v_x

        def seconds(i, j):
            a, b = manager.IndexToNode(i), manager.IndexToNode(j)
            if a == 0 and b == 0:
                return 0
            if a == 0:
                return min(big, int(to[b - 1] / v))
            if b == 0:
                return min(big, int(fro[a - 1] / v + wt[a - 1]))
            return min(big, int(tmat[a - 1][b - 1] + wt[a - 1]))

        tcb = routing.RegisterTransitCallback(seconds)
        routing.AddDimension(tcb, 0, big, True, 'time')
        routing.GetDimensionOrDie('time').SetGlobalSpanCostCoefficient(1000)
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(max(100, int(deadline_s * 1000)))
    solution = routing.SolveWithParameters(params)
    if solution is None:
        return []
    tours = []
    for v in range(trips):
        idx = routing.Start(v)
        tour = []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node:
                tour.append(node - 1)
            idx = solution.Value(routing.NextVar(idx))
        if tour:
            tours.append(tour)
    # trips → machines, longest first
    tours.sort(key=lambda t: -sum(plan.work_t[o] for o in t))
    loads = [0.] * plan.K
    orders: list[list[int]] = [[] for _ in range(plan.K)]
    for t in tours:
        k = loads.index(min(loads))
        orders[k].extend(t)
        loads[k] += sum(plan.work_t[o] for o in t)
    return [o for o in orders if o]


def exact_small(plan: Plan):
    """Held–Karp per trip, then an exact partition of lines into trips; one station at the depot, ≤ 12 lines."""
    import numpy as np
    F = plan.F
    n = F.n
    if n > 12:
        raise ValueError('精确解只支持 12 条以内作业行')
    if plan.E is not None and sum(plan.work_t[::2]) > plan.E * .5:
        raise ValueError('精确解未建模续航约束')
    if plan.K != 1:
        raise ValueError('精确解只支持单机组')
    if len(F.pts) > 2 or (len(F.pts) == 2 and F.pts[0] != F.pts[1]):
        raise ValueError('精确解要求补给站与车库同址')
    C = np.array(plan.C)
    n2 = 2 * n
    to = np.array(plan.to[0])
    fro = np.array(plan.fro[0])
    full = 1 << n
    f = np.full((full, n2), INF)
    back = np.full((full, n2), -1, dtype=int)
    for i in range(n):
        for d in (0, 1):
            f[1 << i, 2 * i + d] = to[2 * i + d]
    masks = sorted(range(1, full), key=lambda x: bin(x).count('1'))
    line_of = np.arange(n2) // 2
    for mask in masks:
        row = f[mask]
        if not np.isfinite(row).any():
            continue
        cand = row[:, None] + C
        for j in range(n):
            if mask >> j & 1:
                continue
            nm = mask | (1 << j)
            for d in (0, 1):
                o = 2 * j + d
                col = cand[:, o]
                arg = int(np.argmin(col))
                if col[arg] < f[nm, o]:
                    f[nm, o] = col[arg]
                    back[nm, o] = arg
    dem = [plan.dem[2 * i] for i in range(n)]
    trip = np.full(full, INF)
    last = np.full(full, -1, dtype=int)
    for mask in range(1, full):
        need = sum(dem[i] for i in range(n) if mask >> i & 1)
        if plan.Q is not None and need > plan.Q + 1e-9:
            continue
        vals = f[mask] + fro
        arg = int(np.argmin(vals))
        trip[mask], last[mask] = vals[arg], arg
    g = [INF] * full
    pick = [0] * full
    g[0] = 0.
    for mask in range(1, full):
        low = mask & -mask
        sub = mask
        while sub:
            if sub & low and trip[sub] < INF:
                val = trip[sub] + g[mask ^ sub]
                if val < g[mask]:
                    g[mask], pick[mask] = val, sub
            sub = (sub - 1) & mask
    if g[-1] >= INF:
        return []
    order, mask = [], full - 1
    while mask:
        sub = pick[mask]
        tour, m, o = [], sub, int(last[sub])
        while o >= 0:
            tour.append(o)
            p = int(back[m, o])
            m ^= 1 << (o // 2)
            o = p
        order.extend(reversed(tour))
        mask ^= sub
    return [order], float(g[-1])
