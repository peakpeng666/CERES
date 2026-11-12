#!/usr/bin/env python3
"""Read-only original-VRPLIB/input audit. Does not import or execute PyVRP.

Ignores comments (which can disclose reference values) and does not open reference
or historical solution files. K comes only from the frozen task definition.
The primary matrices are independently recomputed Decimal80 HALF_UP. A secondary
all-cell compatibility check compares intended floor(math.hypot + .5) integers;
this is a metric equivalence diagnostic, not the route validation scorer.
"""
from __future__ import annotations
import argparse
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
import math
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decimal_matrix(coordinates):
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_UP
        return [
            [int(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2).sqrt().to_integral_value(rounding=ROUND_HALF_UP)) for b in coordinates]
            for a in coordinates
        ]


def parse_vrp(text, k):
    """Strict reader for this panel's explicit-coordinate, single-depot CVRP."""
    header, coords, demands, depots = {}, {}, {}, []
    section = None
    seen_sections = set()
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line == "EOF":
            section = "EOF"
            continue
        if section == "EOF":
            raise ValueError(f"content after EOF at line {line_no}")
        if line in {"NODE_COORD_SECTION", "DEMAND_SECTION", "DEPOT_SECTION"}:
            if line in seen_sections:
                raise ValueError(f"duplicate section {line}")
            seen_sections.add(line)
            section = line
            continue
        if section is None:
            if ":" not in line:
                raise ValueError(f"malformed header at line {line_no}")
            key, value = [part.strip() for part in line.split(":", 1)]
            if key == "COMMENT":
                continue  # Deliberately do not retain public answer metadata.
            if key in header:
                raise ValueError(f"duplicate header {key}")
            header[key] = value
        elif section == "NODE_COORD_SECTION":
            fields = line.split()
            if len(fields) != 3:
                raise ValueError(f"bad coordinate at line {line_no}")
            node, x, y = fields
            node = int(node)
            if node in coords:
                raise ValueError("duplicate coordinate ID")
            pair = [Decimal(x), Decimal(y)]
            if not all(v.is_finite() for v in pair):
                raise ValueError("nonfinite coordinate")
            coords[node] = pair
        elif section == "DEMAND_SECTION":
            fields = line.split()
            if len(fields) != 2:
                raise ValueError(f"bad demand at line {line_no}")
            node, demand = map(int, fields)
            if node in demands or demand < 0:
                raise ValueError("duplicate ID or negative demand")
            demands[node] = demand
        elif section == "DEPOT_SECTION":
            depots.extend(map(int, line.split()))
        else:
            raise ValueError(f"unknown section {section}")
    if header.get("TYPE") != "CVRP" or header.get("EDGE_WEIGHT_TYPE") != "EUC_2D":
        raise ValueError("requires explicit CVRP/EUC_2D")
    n = int(header["DIMENSION"])
    if set(coords) != set(range(1, n + 1)) or set(demands) != set(coords):
        raise ValueError("node IDs not exactly 1..DIMENSION")
    if len(depots) != 2 or depots[-1] != -1 or depots[0] not in coords:
        raise ValueError("requires exactly one depot followed by -1")
    depot = depots[0]
    if demands[depot] != 0:
        raise ValueError("nonzero depot demand")
    ids = [depot] + sorted(node for node in coords if node != depot)
    capacity = int(header["CAPACITY"])
    if capacity <= 0 or type(k) is not int or k < 0:
        raise ValueError("invalid capacity/K")
    return {
        "instance_id": header["NAME"], "coordinates": [coords[node] for node in ids],
        "demands": [demands[node] for node in ids], "capacity": capacity, "K": k,
        "original_node_ids": ids,
    }


def audit(root, original_root):
    tasks = json.loads((root / "freeze/TASKS.json").read_text())
    original_tasks = json.loads((original_root / "freeze/TASKS.json").read_text())
    schedule = json.loads((root / "freeze/SCHEDULE.json").read_text())
    schedule_expected = [(t["selection_slot"], t["instance_id"], seed) for t in tasks for seed in [0, 1, 2]]
    schedule_observed = [(s["selection_slot"], s["instance_id"], s["seed"]) for s in schedule]
    errors = []
    if tasks != original_tasks:
        errors.append("frozen task definitions changed")
    if len(tasks) != 16 or [t["selection_slot"] for t in tasks] != list(range(1, 17)):
        errors.append("incorrect task count/order")
    if schedule_expected != schedule_observed or len({s["call_id"] for s in schedule}) != 48:
        errors.append("incorrect 48-call schedule or duplicate call IDs")
    if any(s["max_runtime_seconds"] != 1.0 for s in schedule):
        errors.append("runtime budget differs")
    rows = []
    for task in tasks:
        name = task["instance_id"]
        original_vrp_bytes = (original_root / "input" / f"{name}.vrp").read_bytes()
        copied_vrp_bytes = (root / "input" / f"{name}.vrp").read_bytes()
        original_json_bytes = (original_root / "input" / f"{name}.json").read_bytes()
        copied_json_bytes = (root / "input" / f"{name}.json").read_bytes()
        parsed = parse_vrp(original_vrp_bytes.decode("utf-8"), task["K"])
        normalized = json.loads(copied_json_bytes, parse_float=Decimal)
        normalized_coords = [[Decimal(str(x)), Decimal(str(y))] for x, y in normalized["coordinates"]]
        vrp_matrix = decimal_matrix(parsed["coordinates"])
        json_matrix = decimal_matrix(normalized_coords)
        intended_float_matrix = [[math.floor(math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1])) + 0.5) for b in normalized_coords] for a in normalized_coords]
        n = len(parsed["coordinates"])
        bad_decimal = [[i, j] for i in range(n) for j in range(n) if vrp_matrix[i][j] != json_matrix[i][j]]
        bad_float = [[i, j] for i in range(n) for j in range(n) if vrp_matrix[i][j] != intended_float_matrix[i][j]]
        checks = {
            "vrp_copy_bytes_identical": copied_vrp_bytes == original_vrp_bytes,
            "json_copy_bytes_identical": copied_json_bytes == original_json_bytes,
            "instance_id": parsed["instance_id"] == normalized["instance_id"] == name,
            "coordinates": parsed["coordinates"] == normalized_coords,
            "demands": parsed["demands"] == normalized["demands"],
            "capacity": parsed["capacity"] == normalized["capacity"],
            "K_from_frozen_task": task["K"] == normalized["K"],
            "depot_is_original_node_1": parsed["original_node_ids"][0] == 1,
            "all_pair_decimal_equal": not bad_decimal,
            "all_pair_intended_float_equal": not bad_float,
            "matrix_symmetric": all(vrp_matrix[i][j] == vrp_matrix[j][i] for i in range(n) for j in range(n)),
            "diagonal_zero": all(vrp_matrix[i][i] == 0 for i in range(n)),
        }
        row = {
            "instance_id": name, "selection_slot": task["selection_slot"], "valid": all(checks.values()),
            "n": n, "directed_cells": n*n, "checks": checks,
            "vrp_sha256": digest(copied_vrp_bytes), "json_sha256": digest(copied_json_bytes),
            "matrix_sha256": digest(json.dumps(vrp_matrix, separators=(",", ":")).encode()),
            "matrix_sha256_encoding": "JSON array of rows with separators=(',',':'), UTF-8, no trailing newline",
            "decimal_mismatch_cells": bad_decimal, "float_mismatch_cells": bad_float,
        }
        rows.append(row)
        if not row["valid"]:
            errors.append(f"input audit failed for {name}")
    return {"valid": not errors, "errors": errors, "tasks": len(tasks), "scheduled_calls": len(schedule),
            "total_directed_cells": sum(r["directed_cells"] for r in rows),
            "no_optimizer_executed": True, "no_external_packages_imported": True,
            "reference_or_historical_routes_not_opened": True,
            "actual_PyVRP_data_matrix_checked": False,
            "model_matrix_caveat": "This verifies data and intended integer metric, not an installed PyVRP model; require adapter model matrices to match these hashes before formal solving.",
            "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--original-root", type=Path, default=Path("/workspace/shared/public_route_order_transfer_20261008"))
    args = parser.parse_args()
    result = audit(args.root, args.original_root)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
