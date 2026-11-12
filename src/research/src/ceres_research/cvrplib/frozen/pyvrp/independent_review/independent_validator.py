#!/usr/bin/env python3
"""Independent CVRP route verification; standard library only, no optimiser code.

Input schema: coordinates (depot first), nonnegative integral demands (depot 0),
positive integral capacity, nonnegative integral K. Routes are lists of original
input node IDs, closed at depot 0. [0, 0] is an allowed empty slot. Vehicle use
means nonempty routes, not stored slot count. No mid-route depot/reload is allowed.

All geometric arithmetic is Decimal with 80 significant digits. Each edge is
rounded separately with ROUND_HALF_UP before summation. No binary sqrt/hypot,
precomputed matrix, package objective, or historical cost is trusted.

Usage: python independent_validator.py INPUT.json SAVED_SOLUTION.json
SAVED_SOLUTION must contain "routes" and "cost" (reported integer cost).
API: validate_solution(problem_dict, closed_routes, reported_cost=None).
For formal results a reported cost must be supplied and independently matched.
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import json
from pathlib import Path


PRECISION = 80
METRIC = "per_edge_Decimal80_sqrt_ROUND_HALF_UP"


def _decimal(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"{label}: expected a finite numeric value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}: expected a finite numeric value") from None
    if not result.is_finite():
        raise ValueError(f"{label}: expected a finite numeric value")
    return result


def _integer(value, label, minimum=0):
    result = _decimal(value, label)
    if result != result.to_integral_value() or result < minimum:
        raise ValueError(f"{label}: expected integer >= {minimum}")
    return int(result)


def _parse_problem(problem):
    if not isinstance(problem, dict):
        raise ValueError("input must be an object")
    raw_coordinates = problem.get("coordinates")
    raw_demands = problem.get("demands")
    if not isinstance(raw_coordinates, list) or not raw_coordinates:
        raise ValueError("coordinates must be a nonempty list")
    if not isinstance(raw_demands, list) or len(raw_demands) != len(raw_coordinates):
        raise ValueError("demands must match coordinates length")
    coordinates = []
    for idx, coordinate in enumerate(raw_coordinates):
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
            raise ValueError(f"coordinates[{idx}] must be a pair")
        coordinates.append(tuple(_decimal(x, f"coordinates[{idx}]") for x in coordinate))
    demands = [_integer(d, f"demands[{idx}]") for idx, d in enumerate(raw_demands)]
    if demands[0] != 0:
        raise ValueError("depot demand must be zero")
    capacity = _integer(problem.get("capacity"), "capacity", 1)
    k = _integer(problem.get("K"), "K")
    return coordinates, demands, capacity, k


def rounded_edge(a, b):
    """Recompute a single nonnegative Euclidean edge with Decimal80 HALF_UP."""
    with localcontext() as ctx:
        ctx.prec = PRECISION
        ctx.rounding = ROUND_HALF_UP
        dx = _decimal(a[0], "x") - _decimal(b[0], "x")
        dy = _decimal(a[1], "y") - _decimal(b[1], "y")
        distance = (dx * dx + dy * dy).sqrt()
        return int(distance.to_integral_value(rounding=ROUND_HALF_UP))


def validate_solution(problem, routes, reported_cost=None):
    """Return diagnostics without repairing, padding, sorting or optimising routes.

    valid is False for any feasibility/encoding/cost error. cost can be a numeric
    diagnostic for an infeasible but structurally well-formed route set; consumers
    MUST gate statistical use on valid and reported_cost_checked, never cost alone.
    """
    result = {
        "valid": False, "feasible": False, "errors": [], "metric": METRIC,
        "precision": PRECISION, "cost": None, "reported_cost_checked": False,
        "reported_cost_matches": None, "route_costs": [], "loads": [],
        "slot_count": len(routes) if isinstance(routes, list) else None,
        "nonempty_routes": None, "missing_customers": [], "duplicate_customers": [],
    }
    errors = result["errors"]
    try:
        coordinates, demands, capacity, k = _parse_problem(problem)
    except (ValueError, ArithmeticError) as exc:
        errors.append("INPUT_ERROR: " + str(exc))
        return result
    result.update({"n_customers": len(coordinates) - 1, "capacity": capacity, "K": k})
    if not isinstance(routes, list):
        errors.append("ROUTES_NOT_LIST")
        return result

    counts = Counter()
    nonempty = 0
    well_formed = True
    total = 0
    for idx, route in enumerate(routes):
        if not isinstance(route, list) or len(route) < 2:
            errors.append(f"ROUTE_FORMAT: route {idx} must be list of length >= 2")
            well_formed = False
            result["loads"].append(None)
            result["route_costs"].append(None)
            continue
        if any(type(node) is not int or node < 0 or node >= len(coordinates) for node in route):
            errors.append(f"NODE_ID: route {idx} has non-integer/bool/out-of-range node")
            well_formed = False
            result["loads"].append(None)
            result["route_costs"].append(None)
            continue
        if route[0] != 0 or route[-1] != 0:
            errors.append(f"NOT_CLOSED: route {idx} must start and end at depot 0")
            well_formed = False
        internal = route[1:-1]
        if 0 in internal:
            errors.append(f"INTERNAL_DEPOT: route {idx} reloads or repeats depot")
            well_formed = False
        customers = [node for node in internal if node != 0]
        nonempty += bool(customers)
        counts.update(customers)
        load = sum(demands[node] for node in customers)
        result["loads"].append(load)
        if load > capacity:
            errors.append(f"CAPACITY: route {idx} load {load} exceeds {capacity}")
        route_cost = sum(rounded_edge(coordinates[a], coordinates[b]) for a, b in zip(route, route[1:]))
        result["route_costs"].append(route_cost)
        total += route_cost
    result["nonempty_routes"] = nonempty
    if nonempty > k:
        errors.append(f"VEHICLE_LIMIT: {nonempty} nonempty routes exceeds {k}")
    missing = [node for node in range(1, len(coordinates)) if counts[node] == 0]
    duplicates = [node for node in range(1, len(coordinates)) if counts[node] > 1]
    result["missing_customers"] = missing
    result["duplicate_customers"] = duplicates
    if missing:
        errors.append("MISSING_CUSTOMERS: " + ",".join(map(str, missing)))
    if duplicates:
        errors.append("DUPLICATE_CUSTOMERS: " + ",".join(map(str, duplicates)))
    if well_formed:
        result["cost"] = total
    result["feasible"] = not errors

    if reported_cost is not None:
        result["reported_cost_checked"] = True
        try:
            reported = _integer(reported_cost, "reported_cost")
        except ValueError as exc:
            result["reported_cost_matches"] = False
            errors.append("REPORTED_COST_ERROR: " + str(exc))
        else:
            result["reported_cost_matches"] = result["cost"] == reported
            if not result["reported_cost_matches"]:
                errors.append(f"COST_MISMATCH: recomputed {result['cost']}, reported {reported}")
    result["valid"] = not errors
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("solution", type=Path)
    args = parser.parse_args()
    try:
        problem = json.loads(args.input.read_text(), parse_float=Decimal)
        solution = json.loads(args.solution.read_text(), parse_float=Decimal)
        if not isinstance(solution, dict) or "routes" not in solution or "cost" not in solution or solution["cost"] is None:
            raise ValueError("solution must contain routes and non-null reported cost")
        result = validate_solution(problem, solution["routes"], solution["cost"])
    except (OSError, ValueError, TypeError) as exc:
        result = {"valid": False, "errors": [str(exc)]}
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
