"""Three transparent, deliberately modest construction baselines.

These are independent implementations of familiar construction ideas, not
reproductions of a mature solver. There is no local search, learning or LLM.
"""

import math
import random

ALGORITHM_VERSION = "construction-baselines-v1"


def distance_matrix(instance, budget):
    matrix = []
    for a in instance.coordinates:
        budget.check()
        matrix.append([math.hypot(a[0] - b[0], a[1] - b[1])
                       for b in instance.coordinates])
    return matrix


def package(routes, matrix, budget):
    budget.check()
    return {
        "routes": [[0] + route + [0] for route in routes],
        "reported_cost": math.fsum(
            matrix[a][b] for route in routes
            for a, b in zip([0] + route, route + [0])
        ),
    }


def nearest_neighbor(instance, seed, budget):
    """Nearest feasible customer until full; exact ties use customer index.

    The seed is recorded for interface uniformity but intentionally unused.
    """
    matrix = distance_matrix(instance, budget)
    remaining = set(range(1, instance.n_customers + 1))
    routes = []
    while remaining:
        route, load, last = [], 0, 0
        while remaining:
            budget.check()
            feasible = [j for j in remaining if load + instance.demands[j] <= instance.capacity]
            if not feasible:
                break
            node = min(feasible, key=lambda j: (matrix[last][j], j))
            route.append(node)
            load += instance.demands[node]
            last = node
            remaining.remove(node)
        routes.append(route)
    return package(routes, matrix, budget)


def savings(instance, seed, budget):
    """Parallel Clarke-Wright savings; merge feasible endpoint pairs.

    Sort decreasing d(0,i)+d(0,j)-d(i,j), then i,j. Reverse routes as needed.
    Ignore nonpositive savings. This implementation is deterministic.
    """
    matrix = distance_matrix(instance, budget)
    n = instance.n_customers
    routes = {i: [i] for i in range(1, n + 1)}
    owner = {i: i for i in routes}
    load = {i: instance.demands[i] for i in routes}
    candidates = []
    for i in range(1, n + 1):
        budget.check()
        for j in range(i + 1, n + 1):
            candidates.append((-(matrix[0][i] + matrix[0][j] - matrix[i][j]), i, j))
    candidates.sort()
    for negative_saving, i, j in candidates:
        budget.check()
        if negative_saving >= 0:
            continue
        left_id, right_id = owner[i], owner[j]
        if left_id == right_id or load[left_id] + load[right_id] > instance.capacity:
            continue
        left, right = routes[left_id], routes[right_id]
        if i not in (left[0], left[-1]) or j not in (right[0], right[-1]):
            continue
        if left[-1] != i:
            left = list(reversed(left))
        if right[0] != j:
            right = list(reversed(right))
        merged = left + right
        routes[left_id] = merged
        load[left_id] += load.pop(right_id)
        del routes[right_id]
        for node in merged:
            owner[node] = left_id
    return package([routes[key] for key in sorted(routes)], matrix, budget)


def sweep(instance, seed, budget):
    """Seeded angular sweep with greedy capacity split, no local search.

    Seed chooses angular origin and orientation. Visit customers in angular
    order within each route. Multiple seeds are replicates, not new instances.
    """
    matrix = distance_matrix(instance, budget)
    rng = random.Random(seed)
    origin, direction = rng.random() * 2 * math.pi, rng.choice((-1, 1))
    depot = instance.coordinates[0]
    def key(node):
        point = instance.coordinates[node]
        angle = math.atan2(point[1] - depot[1], point[0] - depot[0])
        return ((direction * angle - origin) % (2 * math.pi), node)
    order = sorted(range(1, instance.n_customers + 1), key=key)
    routes, route, load = [], [], 0
    for node in order:
        budget.check()
        if route and load + instance.demands[node] > instance.capacity:
            routes.append(route)
            route, load = [], 0
        route.append(node)
        load += instance.demands[node]
    if route:
        routes.append(route)
    return package(routes, matrix, budget)


ALGORITHMS = {
    "nearest_neighbor": nearest_neighbor,
    "parallel_savings": savings,
    "seeded_sweep": sweep,
}
