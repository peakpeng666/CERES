"""Independent output checker; never trusts a solver's feasibility or cost.

No solver helpers or solver distance matrices are imported here. Feasibility
and numeric cost agreement are separate fields. Only accepted outputs enter
comparative summaries. This is a CVRP checker, not a robot safety checker.
"""

from dataclasses import dataclass, asdict
import math

CHECKER_VERSION = "independent-cvrp-checker-v1"
ABS_COST_TOLERANCE = 1e-9
REL_COST_TOLERANCE = 1e-9


@dataclass(frozen=True)
class CheckResult:
    accepted: bool
    feasible: bool
    cost_consistent: bool
    recomputed_cost: float | None
    errors: tuple
    route_loads: tuple
    route_count: int

    def to_dict(self):
        return asdict(self)


def check_solution(instance, output):
    """Total over JSON-shaped output: malformed output yields a rejection.

    A validated Instance is required. Arbitrary executable Python objects are
    outside this interface; the runner transports strict JSON only.
    """
    errors = []
    n = instance.n_customers
    if type(output) is not dict:
        return CheckResult(False, False, False, None, ("output_not_object",), (), 0)
    routes = output.get("routes")
    if type(routes) is not list:
        return CheckResult(False, False, False, None, ("routes_not_list",), (), 0)
    if len(routes) > n:
        return CheckResult(False, False, False, None, ("too_many_routes",), (), len(routes))
    counts = [0] * (n + 1)
    loads = []
    lengths = []
    for route_index, route in enumerate(routes):
        prefix = f"route_{route_index}"
        if type(route) is not list or not 3 <= len(route) <= n + 2:
            errors.append(f"{prefix}:invalid_route_shape")
            continue
        valid_indices = all(type(node) is int and 0 <= node <= n for node in route)
        if not valid_indices:
            errors.append(f"{prefix}:invalid_node_index")
            continue
        if route[0] != 0 or route[-1] != 0:
            errors.append(f"{prefix}:depot_endpoints_required")
        if 0 in route[1:-1]:
            errors.append(f"{prefix}:interior_depot")
        load = 0
        for node in route[1:-1]:
            if node != 0:
                counts[node] += 1
                load += instance.demands[node]
        loads.append(load)
        if load > instance.capacity:
            errors.append(f"{prefix}:capacity_exceeded")
        # Deliberately recompute from coordinates, not the solver's helpers.
        for left, right in zip(route, route[1:]):
            a = instance.coordinates[left]
            b = instance.coordinates[right]
            lengths.append(math.hypot(a[0] - b[0], a[1] - b[1]))
    missing = [node for node in range(1, n + 1) if counts[node] == 0]
    repeated = [node for node in range(1, n + 1) if counts[node] > 1]
    if missing:
        errors.append("missing_customers:" + ",".join(map(str, missing)))
    if repeated:
        errors.append("duplicate_customers:" + ",".join(map(str, repeated)))
    feasible = not errors
    cost = None
    if feasible:
        try:
            cost = math.fsum(lengths)
        except OverflowError:
            errors.append("recomputed_cost_overflow")
            feasible = False
        if cost is not None and not math.isfinite(cost):
            errors.append("nonfinite_recomputed_cost")
            feasible = False
            cost = None
    claimed = output.get("reported_cost")
    valid_claim = type(claimed) in (int, float)
    if valid_claim:
        try:
            claimed = float(claimed)
            valid_claim = math.isfinite(claimed) and claimed >= 0
        except (OverflowError, ValueError):
            valid_claim = False
    if not valid_claim:
        errors.append("invalid_reported_cost")
    consistent = bool(feasible and valid_claim and math.isclose(
        cost, claimed, rel_tol=REL_COST_TOLERANCE, abs_tol=ABS_COST_TOLERANCE
    ))
    if feasible and valid_claim and not consistent:
        errors.append("reported_cost_mismatch")
    return CheckResult(feasible and consistent, feasible, consistent, cost,
                       tuple(errors), tuple(loads), len(routes))
