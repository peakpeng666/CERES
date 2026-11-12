"""Tiny synthetic examples, independent of historical research data."""
from fractions import Fraction
import math

from . import parent_selection
from .cvrp_selection.experiment import crc, route
from .turn_modes import checker, oracle, solver
from .turn_modes.generate_data import fixtures


def parent_example():
    candidates = [
        {"id": "short", "bins": (2, 3), "fitness": Fraction(3, 4), "source_characters": 10},
        {"id": "long", "bins": (2, 3), "fitness": Fraction(3, 4), "source_characters": 100},
        {"id": "other", "bins": (3, 4), "fitness": Fraction(1, 2), "source_characters": 20},
    ]
    distribution = parent_selection.cluster_distribution(candidates, "softmax", "length", 0.1)
    if not math.isclose(sum(distribution.values()), 1.0) or distribution["short"] <= distribution["long"]:
        raise AssertionError("Parent-selection smoke check failed")
    return {"passed": True, "synthetic": True, "distribution": distribution}


def crc_example():
    prediction = {"score": 0.5, "harm": False, "chosen": "alternative", "sbs": "baseline"}
    gate = crc([dict(prediction) for _ in range(6)], 0.1)
    decision = route(prediction, gate)
    if not gate["safe_endpoint"] or decision != ("baseline", False):
        raise AssertionError("CRC finite-sample fallback smoke check failed")
    return {"passed": True, "synthetic": True, "finite_sample_floor": gate["finite_sample_floor"],
            "selected": decision[0], "safe_endpoint": gate["safe_endpoint"]}


def turn_example():
    instance = fixtures()[1]
    mode, _ = solver.solve(instance, "mode")
    reset, _ = solver.solve(instance, "reset")
    direct = oracle.solve(instance)
    valid = checker.check(instance, mode)["valid"]
    reset_valid = checker.check(instance, reset)["valid"]
    if mode["cost"] != direct["cost"] or mode["cost"] != 3 or not valid or reset_valid:
        raise AssertionError("Incoming-direction mode smoke check failed")
    return {"passed": True, "synthetic": True, "mode_cost": mode["cost"],
            "oracle_cost": direct["cost"], "reset_cost": reset["cost"], "reset_valid": reset_valid}


def run():
    return {"parent_selection": parent_example(), "cvrp_selection_crc": crc_example(),
            "turn_modes": turn_example(), "new_model_calls": 0, "candidate_code_executions": 0}
