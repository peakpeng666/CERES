"""Compare reruns with pinned frozen outputs, excluding only declared timing fields."""
import csv
import json
from pathlib import Path

from .archive import ArchiveError, verify_bundle

CRC_TIMING_KEYS = {
    "training_time", "calibration_time", "inference_time",
    "mean_new_feature_and_inference_cpu_seconds",
    "new_feature_extraction_cpu_seconds_once_per_instance",
    "new_feature_extraction_wall_seconds_once_per_instance",
    "new_training_cpu_seconds_8_models", "new_training_wall_seconds_8_models",
    "new_calibration_prediction_and_gating_cpu_seconds", "new_test_prediction_cpu_seconds",
    "new_preflight_cpu_seconds", "new_analysis_cpu_seconds_before_write",
    "new_analysis_wall_seconds_before_write",
}
TURN_TIMING_KEYS = {"wall_s", "cpu_s", "preprocess_wall_s"}


def without_keys(value, keys):
    if isinstance(value, dict):
        return {key: without_keys(item, keys) for key, item in value.items() if key not in keys}
    if isinstance(value, list):
        return [without_keys(item, keys) for item in value]
    return value


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require_equal(left, right, description):
    if left != right:
        raise ArchiveError(f"Result comparison failed: {description}")


def compare(data_root: Path, output_root: Path, manifest: dict):
    for name, spec in manifest["experiments"].items():
        verify_bundle(data_root / name, spec)
    checks = []
    old = data_root / "parent-selection" / "results"
    new = output_root / "parent-selection"
    for name in ("RESULTS.json", "EXHAUSTIVE_VALIDATION.json", "selection_metrics.csv", "parent_probabilities.csv"):
        require_equal((old / name).read_bytes(), (new / name).read_bytes(), "parent-selection/" + name)
        checks.append({"file": "parent-selection/" + name, "equality": "byte-for-byte"})
    old = data_root / "cvrp-selection-crc" / "results"
    new = output_root / "cvrp-selection-crc"
    for name in ("decision_fingerprint.json", "performance_matrix.json", "leakage_tests.json", "folds.json", "summary.json"):
        excluded = CRC_TIMING_KEYS | {"environment"} if name == "summary.json" else CRC_TIMING_KEYS
        require_equal(without_keys(load_json(old / name), excluded),
                      without_keys(load_json(new / name), excluded), "cvrp-selection-crc/" + name)
        checks.append({"file": "cvrp-selection-crc/" + name, "equality": "all non-timing/non-environment fields"})
    def csv_rows(path):
        with path.open(newline="") as stream:
            return [{key: value for key, value in row.items()
                     if key not in {"feature_cpu_seconds", "inference_cpu_seconds"}}
                    for row in csv.DictReader(stream)]
    old_rows, new_rows = csv_rows(old / "out_of_fold.csv"), csv_rows(new / "out_of_fold.csv")
    require_equal(len(new_rows), 240, "CVRP out-of-fold row count")
    require_equal(old_rows, new_rows, "CVRP all 240 decisions and numeric metrics")
    checks.append({"file": "cvrp-selection-crc/out_of_fold.csv", "equality": "240 rows, excluding two new timing columns"})
    old = data_root / "turn-modes" / "results/formal"
    new = output_root / "turn-modes"
    expected = sorted(path.name for path in old.glob("g*.json"))
    require_equal(len(expected), 36, "pinned turn instance count")
    require_equal(sorted(path.name for path in new.glob("g*.json")), expected, "rerun turn instance identities")
    for name in [*expected, "summary.json"]:
        require_equal(without_keys(load_json(old / name), TURN_TIMING_KEYS),
                      without_keys(load_json(new / name), TURN_TIMING_KEYS), "turn-modes/" + name)
        checks.append({"file": "turn-modes/" + name, "equality": "all non-timing fields including complete route witnesses"})
    return {"passed": True, "checks": checks, "parent_result_files_byte_identical": 4,
            "cvrp_decision_rows_identical": 240, "turn_complete_witnesses_identical": 36,
            "crc_excluded_timing_keys": sorted(CRC_TIMING_KEYS),
            "crc_excluded_csv_columns": ["feature_cpu_seconds", "inference_cpu_seconds"],
            "crc_excluded_environment_metadata": ["summary.json:environment"],
            "turn_excluded_timing_keys": sorted(TURN_TIMING_KEYS),
            "timing_equality_not_claimed": True}
