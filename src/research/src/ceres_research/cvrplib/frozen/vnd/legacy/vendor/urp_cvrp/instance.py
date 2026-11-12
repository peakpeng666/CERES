"""Strict input contract for a small, symmetric Euclidean CVRP pilot."""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

SPEC_VERSION = "euclidean-cvrp-v1"
MAX_CUSTOMERS = 1000


class InstanceError(ValueError):
    """Input fails the declared CVRP task contract."""


def finite_number(value, name):
    if type(value) not in (int, float):
        raise InstanceError(f"{name}: expected a finite number, excluding bool")
    try:
        numeric = float(value)
    except (ValueError, OverflowError) as exc:
        raise InstanceError(f"{name}: number cannot be represented") from exc
    if not math.isfinite(numeric):
        raise InstanceError(f"{name}: non-finite value")
    return numeric


@dataclass(frozen=True)
class Instance:
    instance_id: str
    coordinates: tuple
    demands: tuple
    capacity: int
    split: str
    generation_seed: int | None
    family: str

    @property
    def n_customers(self):
        return len(self.demands) - 1

    def to_dict(self):
        return {
            "spec_version": SPEC_VERSION,
            "instance_id": self.instance_id,
            "coordinates": [list(point) for point in self.coordinates],
            "demands": list(self.demands),
            "capacity": self.capacity,
            "depot": 0,
            "objective": "sum_unrounded_euclidean_distance",
            "fleet": "unlimited_identical_vehicles",
            "split": self.split,
            "generation_seed": self.generation_seed,
            "family": self.family,
        }

    @property
    def sha256(self):
        return hashlib.sha256(canonical_json(self.to_dict()).encode()).hexdigest()


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def from_dict(raw):
    if type(raw) is not dict:
        raise InstanceError("instance: expected an object")
    if raw.get("spec_version") != SPEC_VERSION:
        raise InstanceError("unsupported task specification")
    if raw.get("objective") != "sum_unrounded_euclidean_distance":
        raise InstanceError("objective must use unrounded Euclidean distance")
    if raw.get("fleet") != "unlimited_identical_vehicles":
        raise InstanceError("only unlimited identical vehicles are supported")
    if type(raw.get("depot")) is not int or raw["depot"] != 0:
        raise InstanceError("depot index must be integer 0")
    capacity = raw.get("capacity")
    if type(capacity) is not int or capacity <= 0:
        raise InstanceError("capacity must be a positive integer")
    coords, demands = raw.get("coordinates"), raw.get("demands")
    if type(coords) is not list or type(demands) is not list:
        raise InstanceError("coordinates and demands must be lists")
    if not 1 <= len(coords) <= MAX_CUSTOMERS + 1 or len(coords) != len(demands):
        raise InstanceError("invalid or unequal coordinate/demand dimensions")
    points = []
    for index, point in enumerate(coords):
        if type(point) is not list or len(point) != 2:
            raise InstanceError(f"coordinate {index}: expected exactly two numbers")
        points.append(tuple(finite_number(x, f"coordinate {index}") for x in point))
    for index, demand in enumerate(demands):
        if type(demand) is not int or not 0 <= demand <= capacity:
            raise InstanceError(f"demand {index}: require integer 0 <= demand <= capacity")
    if demands[0] != 0:
        raise InstanceError("depot demand must be zero")
    # Reject finite coordinates whose subtraction or distance overflows, and
    # bounds whose worst legal total length would overflow float arithmetic.
    max_distance = 0.0
    for i, a in enumerate(points):
        for b in points[:i]:
            distance = math.hypot(a[0] - b[0], a[1] - b[1])
            if not math.isfinite(distance):
                raise InstanceError("pairwise distance overflow")
            max_distance = max(max_distance, distance)
    if not math.isfinite(max_distance * max(1, 2 * (len(points) - 1))):
        raise InstanceError("potential total distance overflow")
    identifier = raw.get("instance_id")
    if type(identifier) is not str or not identifier or len(identifier) > 200:
        raise InstanceError("instance_id must be a nonempty string of <= 200 characters")
    split = raw.get("split")
    if split not in ("dev", "frozen_test", "unit_test"):
        raise InstanceError("unrecognized data split")
    seed = raw.get("generation_seed")
    if seed is not None and (type(seed) is not int or seed < 0):
        raise InstanceError("generation_seed must be null or a nonnegative integer")
    family = raw.get("family")
    if type(family) is not str or not family:
        raise InstanceError("family must be a nonempty string")
    return Instance(identifier, tuple(points), tuple(demands), capacity, split, seed, family)


def load_instance(path):
    with Path(path).open(encoding="utf-8") as handle:
        return from_dict(json.load(handle))
