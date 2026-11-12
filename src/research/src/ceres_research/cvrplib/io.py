"""Strict input contract. Comments and reference values never reach workers."""
import hashlib
import json
import math
import re
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def parse_vrp(text):
    header, sections = {}, {}
    section = None
    ended = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ended:
            raise ValueError("content after EOF")
        if line == "EOF":
            ended = True
            continue
        if line.endswith("_SECTION"):
            if line not in {"NODE_COORD_SECTION", "DEMAND_SECTION", "DEPOT_SECTION"}:
                raise ValueError("unsupported section " + line)
            if line in sections:
                raise ValueError("duplicate section " + line)
            section = line
            sections[section] = []
        elif section:
            sections[section].append(line.split())
        else:
            key, sep, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if not sep:
                raise ValueError("malformed header")
            if key == "COMMENT":
                continue
            if key in header:
                raise ValueError("duplicate header " + key)
            if key not in {"NAME", "TYPE", "DIMENSION", "EDGE_WEIGHT_TYPE", "CAPACITY"}:
                raise ValueError("unsupported header " + key)
            header[key] = val
    if header.get("TYPE") != "CVRP" or header.get("EDGE_WEIGHT_TYPE") != "EUC_2D":
        raise ValueError("only CVRP/EUC_2D supported")
    if set(sections) != {"NODE_COORD_SECTION", "DEMAND_SECTION", "DEPOT_SECTION"}:
        raise ValueError("missing section")
    try:
        n, capacity = int(header["DIMENSION"]), int(header["CAPACITY"])
        coords, demands = {}, {}
        for row in sections["NODE_COORD_SECTION"]:
            if len(row) != 3:
                raise ValueError("invalid coordinate row")
            node = int(row[0])
            if node in coords:
                raise ValueError("duplicate coordinate ID")
            coords[node] = [float(x) for x in row[1:]]
        for row in sections["DEMAND_SECTION"]:
            if len(row) != 2:
                raise ValueError("invalid demand row")
            node, demand = map(int, row)
            if node in demands:
                raise ValueError("duplicate demand ID")
            demands[node] = demand
        depots = [int(x) for row in sections["DEPOT_SECTION"] for x in row]
        if len(depots) != 2 or depots[-1] != -1 or depots[0] not in coords:
            raise ValueError("exactly one depot with -1 terminator required")
        if n < 2 or set(coords) != set(range(1, n + 1)) or set(demands) != set(coords):
            raise ValueError("IDs must cover 1..DIMENSION exactly")
        ids = [depots[0]] + sorted(set(coords) - {depots[0]})
        result = {"coordinates": [coords[i] for i in ids],
                  "demands": [demands[i] for i in ids], "capacity": capacity}
        validate_problem(result)
        return {**result, "name": header["NAME"], "dimension": n,
                "original_node_ids": ids, "metric": "EUC_2D"}
    except (KeyError, OverflowError) as exc:
        raise ValueError("missing/invalid input field: " + str(exc)) from exc


def validate_problem(p):
    xy, demands, capacity = p["coordinates"], p["demands"], p["capacity"]
    if not isinstance(xy, list) or not 2 <= len(xy) <= 10000:
        raise ValueError("coordinates length outside 2..10000")
    if not isinstance(demands, list) or len(demands) != len(xy):
        raise ValueError("demand/coordinate length mismatch")
    for pair in xy:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(
            type(x) not in (float, int) or not math.isfinite(x) or abs(x) > 10**9 for x in pair
        ):
            raise ValueError("invalid/nonfinite/out-of-range coordinate")
    if type(capacity) is not int or not 0 < capacity <= 10**9:
        raise ValueError("invalid capacity")
    if any(type(d) is not int or d < 0 or d > capacity for d in demands) or demands[0] != 0:
        raise ValueError("invalid demand or depot demand")


def matrix(points):
    return [[math.floor(math.hypot(a[0]-b[0], a[1]-b[1]) + .5)
             for b in points] for a in points]


def parse_sol(text):
    """CVRPLIB SOL customer indices are 1-based after removing the depot."""
    routes, cost = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"Route\s+#(\d+)\s*:\s*(.*)", line)
        if match and cost is None:
            if int(match[1]) != len(routes) + 1:
                raise ValueError("nonconsecutive SOL routes")
            nodes = [int(x) for x in match[2].split()]
            if not nodes or any(x <= 0 for x in nodes):
                raise ValueError("empty/invalid SOL route")
            routes.append([0, *nodes, 0])
        elif re.fullmatch(r"Cost\s+\d+(?:\.0+)?", line) and cost is None:
            cost = int(float(line.split()[1]))
        else:
            raise ValueError("malformed SOL line")
    if cost is None or not routes:
        raise ValueError("SOL requires routes and Cost")
    return routes, cost


def validate_payload(payload):
    fields = {"coordinates", "demands", "capacity", "K", "algorithm", "seed", "budget_seconds"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError("worker accepts reference-free fields only")
    validate_problem(payload)
    if type(payload["K"]) is not int or not 1 <= payload["K"] <= len(payload["demands"])-1:
        raise ValueError("invalid K")
    if type(payload["seed"]) is not int or not 0 <= payload["seed"] < 2**32:
        raise ValueError("seed must be uint32")
    b = payload["budget_seconds"]
    if type(b) not in (int, float) or not math.isfinite(b) or not 0 < b <= 60:
        raise ValueError("budget outside (0,60]")
    if payload["algorithm"] not in {"savings", "T", "TRS", "TSR", "pyvrp", "ortools", "nearest_neighbor", "seeded_sweep", "savings_vnd", "multistart_vnd", "random_cheapest", "random_regret2", "worst_cheapest", "worst_regret2", "ga_random_keys", "pso_random_keys", "de_random_keys"}:
        raise ValueError("unknown algorithm")


def contract_for(case, problem, capped_x=False):
    contract = case["contract"]
    if not contract.get("eligible"):
        raise ValueError("fleet contract pending")
    if capped_x:
        if case["source_family"] != "X":
            raise ValueError("capped-X requires X family")
        return {"id": "derived-capped-X", "K": case["k_name_metadata"], "fixed_cost": 0}
    k = contract["vehicle_limit"]
    return {"id": contract["id"], "K": len(problem["demands"])-1 if k is None else k,
            "fixed_cost": 0}
