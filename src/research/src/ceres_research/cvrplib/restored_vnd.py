"""Original, transparent feasible local search; no external code copied.

Both VND variants belong to ONE local-search family. No penalties, rounding,
route-count objective, model call, learning, or claim of mature solver parity.
"""
import math
import random

class BudgetExceeded(TimeoutError):
    pass

VERSION = "feasible-vnd-v2"


def route_cost(route, d):
    return math.fsum(d[a][b] for a, b in zip([0] + route, route + [0]))


def cost(routes, d):
    return math.fsum(route_cost(r, d) for r in routes)


def output(routes, d, meta=None):
    return {"routes": [[0] + r + [0] for r in routes],
            "reported_cost": cost(routes, d), "search": meta or {}}


def moves(routes, demands, capacity, d, kind):
    """Yield (delta, opaque move) for every feasible member of one neighborhood.

    Deltas only guide search; candidates and saved outputs are fully recomputed.
    Intra-route relocate and inter-route relocate, inter-route swap, ordinary
    symmetric intra-route 2-opt, and inter-route tail-swap 2-opt* are covered.
    """
    loads = [sum(demands[x] for x in r) for r in routes]
    for i, a in enumerate(routes):
        if kind == "two_opt":
            for u in range(len(a) - 1):
                for v in range(u + 1, len(a)):
                    before, first = a[u - 1] if u else 0, a[u]
                    last, after = a[v], a[v + 1] if v + 1 < len(a) else 0
                    yield d[before][last] + d[first][after] - d[before][first] - d[last][after], (kind, i, u, v)
        elif kind == "relocate":
            for u, node in enumerate(a):
                before, after = a[u - 1] if u else 0, a[u + 1] if u + 1 < len(a) else 0
                remove = d[before][after] - d[before][node] - d[node][after]
                for j, b in enumerate(routes):
                    if i != j and loads[j] + demands[node] > capacity:
                        continue
                    target = b[:u] + b[u + 1:] if i == j else b
                    for v in range(len(target) + 1):
                        if i == j and v == u:
                            continue
                        left, right = target[v - 1] if v else 0, target[v] if v < len(target) else 0
                        yield remove + d[left][node] + d[node][right] - d[left][right], (kind, i, u, j, v)
        elif kind in ("swap", "two_opt_star"):
            for j in range(i + 1, len(routes)):
                b = routes[j]
                if kind == "swap":
                    for u, x in enumerate(a):
                        for v, y in enumerate(b):
                            if (loads[i] - demands[x] + demands[y] > capacity or
                                    loads[j] - demands[y] + demands[x] > capacity):
                                continue
                            ap, an = a[u - 1] if u else 0, a[u + 1] if u + 1 < len(a) else 0
                            bp, bn = b[v - 1] if v else 0, b[v + 1] if v + 1 < len(b) else 0
                            delta = (d[ap][y] + d[y][an] + d[bp][x] + d[x][bn]
                                     - d[ap][x] - d[x][an] - d[bp][y] - d[y][bn])
                            yield delta, (kind, i, u, j, v)
                else:
                    prefix_a = [0]
                    prefix_b = [0]
                    for node in a:
                        prefix_a.append(prefix_a[-1] + demands[node])
                    for node in b:
                        prefix_b.append(prefix_b[-1] + demands[node])
                    for u in range(len(a) + 1):
                        for v in range(len(b) + 1):
                            if (prefix_a[u] + loads[j] - prefix_b[v] > capacity or
                                    prefix_b[v] + loads[i] - prefix_a[u] > capacity):
                                continue
                            ap, an = a[u - 1] if u else 0, a[u] if u < len(a) else 0
                            bp, bn = b[v - 1] if v else 0, b[v] if v < len(b) else 0
                            yield d[ap][bn] + d[bp][an] - d[ap][an] - d[bp][bn], (kind, i, u, j, v)
        else:
            raise ValueError(kind)


def apply_move(routes, move):
    routes = [r[:] for r in routes]
    kind, i, u, *tail = move
    if kind == "two_opt":
        v = tail[0]
        routes[i][u:v + 1] = reversed(routes[i][u:v + 1])
    else:
        j, v = tail
        if kind == "relocate":
            node = routes[i].pop(u)
            routes[j].insert(v, node)
        elif kind == "swap":
            routes[i][u], routes[j][v] = routes[j][v], routes[i][u]
        elif kind == "two_opt_star":
            a, b = routes[i], routes[j]
            routes[i], routes[j] = a[:u] + b[v:], b[:v] + a[u:]
        else:
            raise ValueError(kind)
    return [r for r in routes if r]


class WorkLimit(Exception):
    pass


class SearchState:
    def __init__(self, limit, budget, epsilon):
        self.limit, self.budget, self.epsilon = limit, budget, epsilon
        self.evaluations = 0
        self.accepted_moves = 0
        self.completed_starts = 0
        self.move_counts = {k: 0 for k in ("two_opt", "relocate", "swap", "two_opt_star")}
        self.local_optima = 0
        self.incumbent_routes = None
        self.incumbent_cost = math.inf

    def observe(self, routes, d):
        value = cost(routes, d)
        if value < self.incumbent_cost - self.epsilon:
            self.incumbent_cost, self.incumbent_routes = value, [r[:] for r in routes]

    def tick(self):
        if self.evaluations >= self.limit:
            raise WorkLimit
        self.evaluations += 1
        self.budget.check()


def descend(instance, routes, d, state):
    """Best improvement in each neighborhood; restart VND at first on a move."""
    kinds = ("two_opt", "relocate", "swap", "two_opt_star")
    current = cost(routes, d)
    state.observe(routes, d)
    neighborhood = 0
    while neighborhood < len(kinds):
        kind = kinds[neighborhood]
        best_delta, best_move = -state.epsilon, None
        candidates = iter(moves(routes, instance.demands, instance.capacity, d, kind))
        while True:
            # Guard BEFORE advancing the generator: v1 computed one extra delta
            # at its cap even though it did not admit that delta for comparison.
            if state.evaluations >= state.limit:
                raise WorkLimit
            try:
                delta, move = next(candidates)
            except StopIteration:
                break
            state.tick()
            if delta < best_delta:
                best_delta, best_move = delta, move
        if best_move is None:
            neighborhood += 1
            continue
        candidate = apply_move(routes, best_move)
        # Trust neither incremental sums nor a numerically spurious improvement.
        new_cost = cost(candidate, d)
        if new_cost >= current - state.epsilon:
            neighborhood += 1
            continue
        routes, current = candidate, new_cost
        state.accepted_moves += 1
        state.move_counts[kind] += 1
        state.observe(routes, d)
        neighborhood = 0
    state.local_optima += 1
    return routes

