"""Generate development instances only; never materialize a final test split."""

import argparse
from pathlib import Path
import json
import random

from .instance import SPEC_VERSION, canonical_json, from_dict

GENERATOR_VERSION = "synthetic-dev-generator-v1"


def generate_instance(n, family, seed, capacity=30):
    if type(n) is not int or not 0 <= n <= 1000:
        raise ValueError("n must be an integer between zero and 1000")
    if family not in ("uniform", "clustered"):
        raise ValueError("unsupported development family")
    if type(seed) is not int or seed < 0:
        raise ValueError("explicit nonnegative integer seed required")
    if type(capacity) is not int or capacity < 10:
        raise ValueError("generator capacity must be an integer >= 10")
    rng = random.Random(seed)
    points = [[50.0, 50.0]]
    centers = [(25, 25), (75, 25), (50, 75)]
    for _ in range(n):
        if family == "uniform":
            point = [rng.uniform(0, 100), rng.uniform(0, 100)]
        else:
            x, y = rng.choice(centers)
            point = [min(100, max(0, rng.gauss(x, 7))),
                     min(100, max(0, rng.gauss(y, 7)))]
        points.append([round(x, 6) for x in point])
    demands = [0] + [rng.randint(1, 10) for _ in range(n)]
    return from_dict({
        "spec_version": SPEC_VERSION,
        "instance_id": f"dev-{family}-n{n}-s{seed}",
        "coordinates": points,
        "demands": demands,
        "capacity": capacity,
        "depot": 0,
        "objective": "sum_unrounded_euclidean_distance",
        "fleet": "unlimited_identical_vehicles",
        "split": "dev",
        "generation_seed": seed,
        "family": family,
    })


def generate_from_config(config, destination):
    if config.get("split") != "dev":
        raise ValueError("this tool only generates development instances")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    instances, used_seeds = [], set()
    index = 0
    for family in config["families"]:
        for n in config["customer_counts"]:
            for replicate in range(config["instances_per_cell"]):
                seed = config["generation_seed_base"] + index
                index += 1
                if seed in used_seeds:
                    raise ValueError("duplicate generation seed")
                used_seeds.add(seed)
                instance = generate_instance(n, family, seed, config["capacity"])
                filename = instance.instance_id + ".json"
                serialized = json.dumps(instance.to_dict(), indent=2, allow_nan=False) + "\n"
                path = destination / filename
                if path.exists() and path.read_text() != serialized:
                    raise FileExistsError(f"refuse to overwrite differing instance: {filename}")
                path.write_text(serialized)
                instances.append({"instance_id": instance.instance_id, "file": filename,
                                  "sha256": instance.sha256, "generation_seed": seed,
                                  "family": family, "n_customers": n, "replicate": replicate})
    manifest = {
        "schema_version": "dev-manifest-v1", "generator_version": GENERATOR_VERSION,
        "split": "dev", "seed_namespace": "2026100400..2026100499",
        "config": config, "instances": instances,
        "frozen_test": {"status": "not_generated_not_evaluated",
                        "reserved_seed_namespace": "2026101400..2026101499",
                        "note": "Seed separation is bookkeeping, not a secure blind-test barrier. Final protocol remains to be preregistered."},
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with open(args.config) as handle:
        config = json.load(handle)
    manifest = generate_from_config(config, args.output)
    print(f"Generated {len(manifest['instances'])} development instances; no final test data generated.")


if __name__ == "__main__":
    main()
