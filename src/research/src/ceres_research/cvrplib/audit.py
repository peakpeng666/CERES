"""Read-only full-panel audit, with archived independently authored checking."""
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
import importlib.util
import json
import math
from pathlib import Path
import re
import statistics
import zipfile

from .io import digest, matrix, parse_sol, parse_vrp, sha

FROZEN = Path(__file__).with_name("frozen")


def load_frozen(name, relative):
    spec = importlib.util.spec_from_file_location(name, FROZEN / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_frozen("cvrplib_independent_validator", "pyvrp/independent_review/independent_validator.py")
independent_input = load_frozen("cvrplib_independent_input", "pyvrp/independent_review/audit_inputs.py")


def lattice(p):
    xy = [[Fraction(str(v)) for v in point] for point in p["coordinates"]]
    den = math.lcm(*(v.denominator for pair in xy for v in pair))
    return [[int(v*den) for v in pair] for pair in xy]


def base_signature(p):
    """Canonical depot-relative integer coordinates, D4, uniform scale, ID order.

    Capacity and fleet are intentionally excluded. A second squared-distance
    signature below screens arbitrary rotations, not just grid symmetries.
    """
    exact = lattice(p)
    x0, y0 = exact[0]
    xy = [(x-x0, y-y0) for x, y in exact]
    scale = math.gcd(*(abs(v) for pair in xy for v in pair)) or 1
    forms = []
    for swap in (False, True):
        for sx in (-1, 1):
            for sy in (-1, 1):
                rows = []
                for (x, y), demand in zip(xy[1:], p["demands"][1:]):
                    x, y = (y, x) if swap else (x, y)
                    rows.append((sx*x//scale, sy*y//scale, demand))
                forms.append(sorted(rows))
    return digest(min(forms))


def distance_signature(p):
    xy, ds = lattice(p), p["demands"]
    d2 = [[(a[0]-b[0])**2+(a[1]-b[1])**2 for b in xy] for a in xy]
    scale = math.gcd(*(v for row in d2 for v in row)) or 1
    signatures = []
    for i, row in enumerate(d2):
        signatures.append(digest([i == 0, ds[i], sorted(
            (j == 0, ds[j], v//scale) for j, v in enumerate(row))]))
    return digest(sorted(signatures))


def audit_panel(repo, output):
    repo, output = Path(repo).resolve(), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    root = repo / "data/benchmarks/CVRPLIB"
    originals = json.loads((root / "SOURCE_MANIFEST.json").read_text())["files"]
    source = {Path(r["path"]).name: r for r in originals}
    restored = json.loads((root / "reference_solutions/SOURCE.json").read_text())
    extra = {r["file"]: r for r in restored["files"]}
    tasks = json.loads((FROZEN / "pyvrp/freeze/TASKS.json").read_text())
    small16 = {r["instance_id"]: r for r in tasks}
    draft_zip = repo / "acceptance/20261014/input/preparation.zip"
    if sha(draft_zip) != "8d6fe912f3bab57bd5f7948a706a34444a1b18de622a1514f2a1ad6e94e2f1fe":
        raise ValueError("preparation ZIP hash mismatch")
    with zipfile.ZipFile(draft_zip) as z:
        draft = json.loads(z.read("case_list.json"))
    paths = sorted((root / "instances").glob("*.vrp"))
    if {p.stem for p in paths} != {r["case_id"] for r in draft["cases"]}:
        raise ValueError("panel membership differs from preparation")
    cases, checks, bases, rotations = [], [], defaultdict(list), defaultdict(list)
    for path in paths:
        raw = path.read_text()
        p = parse_vrp(raw)
        if p["name"] != path.stem or p["dimension"] != int(re.search(r"-n(\d+)", path.stem)[1]):
            raise ValueError("filename/header mismatch: " + path.stem)
        if sha(path) != source[path.name]["sha256"]:
            raise ValueError("original source hash mismatch: " + path.name)
        n = p["dimension"] - 1
        family = path.stem[0]
        ref_path = root / "reference_solutions" / (path.stem + ".sol")
        expected = source.get(ref_path.name, extra.get(ref_path.name))
        if sha(ref_path) != expected["sha256"]:
            raise ValueError("reference source hash mismatch: " + path.stem)
        routes, ref_cost = parse_sol(ref_path.read_text())
        k_ref, k_name = len(routes), int(path.stem.split("-k")[1])
        lb = (sum(p["demands"]) + p["capacity"]-1)//p["capacity"]
        if family == "X":
            contract = {"id": "standard-X-unlimited", "vehicle_limit": None,
                        "evidence": "Uchoa et al. 2017 section 3.3.2; k metadata only"}
            k = n
        else:
            # Original file + feasible reference + mathematical demand lower bound,
            # not a fleet limit inferred solely from the filename.
            comment_k = re.search(r"(?:No of trucks|Min no of trucks)\s*:\s*(\d+)", raw, re.I)
            if not comment_k or int(comment_k[1]) != k_ref or lb != k_ref:
                raise ValueError("unresolved non-X fleet contract: " + path.stem)
            if path.stem in small16 and small16[path.stem]["K"] != k_ref:
                raise ValueError("historical K differs: " + path.stem)
            contract = {"id": "historical-small16-at-most-K" if path.stem in small16
                        else "classic-fixed-K-equivalent-cap",
                        "vehicle_limit": k_ref,
                        "evidence": "original COMMENT truck count; feasible reference route count; "
                        "ceil(total demand/Q)=K proves at-most and exactly K equivalent; "
                        "Uchoa et al. 2017 tables 1,3-6 and section 3.3.2"}
            k = k_ref
        contract.update(eligible=True, fixed_cost=0, distance_cost=1)
        independent = independent_input.parse_vrp(raw, k)
        if (independent["demands"] != p["demands"] or independent["capacity"] != p["capacity"] or
            independent["coordinates"] != [[Decimal(str(v)) for v in row] for row in p["coordinates"]] or
            independent["original_node_ids"] != p["original_node_ids"]):
            raise ValueError("independent parser mismatch: " + path.stem)
        dm = independent_input.decimal_matrix(independent["coordinates"])
        if dm != matrix(p["coordinates"]):
            raise ValueError("all-pair rounding mismatch: " + path.stem)
        checked = validator.validate_solution(independent, routes, ref_cost)
        if not checked["valid"]:
            raise ValueError("reference invalid: " + path.stem + repr(checked))
        sig = base_signature(p)
        bases[sig].append(path.stem)
        rotations[distance_signature(p)].append(path.stem)
        demands = p["demands"][1:]
        case = {"case_id": path.stem, "n_customers": n, "dimension": p["dimension"],
                "size_stratum": "small" if n < 100 else "medium" if n < 300 else "large",
                "source_family": family, "instance_path": str(path.relative_to(repo)),
                "instance_sha256": sha(path), "source_evidence": source[path.name]["sources"],
                "k_name_metadata": k_name, "demand_lower_bound_routes": lb, "capacity": p["capacity"],
                "contract": contract, "base_id": sig, "historical_small16": small16.get(path.stem),
                "problem_sha256": digest({"coordinates": p["coordinates"], "demands": p["demands"],
                    "capacity": p["capacity"], "metric": "EUC_2D", "contract": contract}),
                "matrix_sha256": digest(dm), "prior_research_usage": "restored from historical archives; not unseen test data",
                "source_distribution_description": "unknown; family alone is not an empirical spatial label",
                "empirical_features": {"demand_mean": statistics.mean(demands),
                    "demand_cv": statistics.pstdev(demands)/statistics.mean(demands),
                    "total_demand": sum(demands), "demand_capacity_ratio": sum(demands)/p["capacity"],
                    "x_range": [min(x for x,y in p["coordinates"]), max(x for x,y in p["coordinates"])],
                    "y_range": [min(y for x,y in p["coordinates"]), max(y for x,y in p["coordinates"])]},
                "reference": {"path": str(ref_path.relative_to(repo)), "sha256": sha(ref_path),
                    "cost": ref_cost, "routes": k_ref, "validated": True, "eligible_for_gap": True,
                    "label": "official_opt_yes_feasible_reverified" if ref_path.name in extra else "historical_feasible_reference",
                    "optimality_proved_here": False,
                    "source": expected}}
        cases.append(case)
        checks.append({"case_id": path.stem, "independent_parser_match": True,
                       "directed_matrix_cells": p["dimension"]**2, "all_pair_rounding_match": True,
                       "reference_validation": checked})
    if len(bases) != len(rotations):
        raise ValueError("additional arbitrary-rotation candidate duplicate needs explicit review")
    result = {"schema": "ceres-cvrplib-panel-v1", "status": "DATA_VERIFIED_NOT_EXPERIMENT_RESULTS",
              "case_count": len(cases), "base_count": len(bases),
              "base_method": "depot-relative D4 + translation + uniform scale + customer permutation, demand retained, capacity excluded; independent squared-distance screen",
              "duplicate_groups": [v for v in bases.values() if len(v)>1],
              "customer_range": [min(c["n_customers"] for c in cases), max(c["n_customers"] for c in cases)],
              "strata": dict(Counter(c["size_stratum"] for c in cases)),
              "family_counts": dict(Counter(c["source_family"] for c in cases)),
              "size_by_family": dict(Counter(c["size_stratum"]+"/"+c["source_family"] for c in cases)),
              "cases": cases}
    (output/"case_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    (output/"reference_validation.json").write_text(json.dumps({"valid": True, "checks": checks}, indent=2)+"\n")
    return {k: v for k,v in result.items() if k != "cases"}
