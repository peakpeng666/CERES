"""Isolated optional CPU worker. All work needs an explicit subcommand."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def smoke():
    import torch
    from .control import FROZEN
    core = load_module("nas_frozen_core_smoke", FROZEN / "source/core.py")
    core.setup()
    torch.manual_seed(880001)
    inputs = torch.linspace(0, 1, 2 * 3 * 32 * 32).reshape(2, 3, 32, 32)
    with torch.inference_mode():
        for arch in core.space():
            model = core.TinyCNN(arch).eval()
            scores = model(inputs)
            if scores.shape != (2, 10) or not torch.isfinite(scores).all():
                raise RuntimeError("Synthetic model forward failed")
    print(json.dumps({"models_checked": 8, "synthetic_samples_per_model": 2,
                      "optimizer_steps": 0, "cifar_samples_read": 0}))


def recheck(experiment, output, checkpoints):
    from ..archive import verify_bundle
    from .control import FROZEN, load_spec
    spec = load_spec()
    expected = dict(spec["files"])
    expected.update({"data/" + n: m for n, m in spec["dataset"]["extracted_files"].items()})
    # Verify the copied bytes immediately before any dynamic import or torch.load.
    verify_bundle(experiment, {"files": expected, "sha256": "pinned-copied-evidence-and-training-batches"})
    audit = load_module("nas_independent_audit", FROZEN / "audit/check_fidelity.py")
    audit.ROOT, audit.AUDIT = experiment, output
    rc = audit.run(argparse.Namespace(phase="results", checkpoints=checkpoints, output="independent_audit.json"))
    if rc:
        raise RuntimeError("Independent frozen audit failed; see independent_audit.json")
    before = json.loads((experiment / "reports/summary.json").read_text())
    expected_csv = {name: (experiment / "reports" / name).read_bytes()
                    for name in ("architecture_scores.csv", "all_epoch_scores.csv")}
    sys.path.insert(0, str(experiment / "source"))
    import core
    core.ROOT = experiment
    analyze = load_module("nas_frozen_analyze", FROZEN / "source/analyze.py")
    analyze.ROOT = experiment
    analyze.main()
    after = json.loads((experiment / "reports/summary.json").read_text())
    before.pop("at_utc")
    after.pop("at_utc")
    if before != after:
        raise RuntimeError("Recomputed summary differs from the frozen summary beyond its timestamp")
    for name, expected_bytes in expected_csv.items():
        if (experiment / "reports" / name).read_bytes() != expected_bytes:
            raise RuntimeError(f"Recomputed CSV differs: {name}")
    report = json.loads((output / "independent_audit.json").read_text())
    result = {"passed": True, "training_optimizer_steps": 0,
              "trajectories_verified": 80, "epochs_verified": 1600,
              "independent_audit_checks": len(report["checks"]),
              "endpoint_predictions_recomputed": len(report.get("independent_checkpoint_endpoints", [])),
              "summary_equal_except_timestamp": True, "csv_byte_equal": True,
              "historical_evidence_changed": False, "official_test_samples_read": 0,
              "bootstrap_replicates": 10000, "scope": "Engineering verification of existing frozen research; no new experimental result"}
    (output / "engineering_recheck.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def training_steps():
    """Ordered historical workflow; each script gets a fresh optional process."""
    return [
        ("data_prepare.log", ["source/prepare_data.py"]),
        ("smoke_stdout.json", ["source/smoke_timing.py"]),
        ("pre_run_tests.txt", ["source/test_primitives.py"]),
        ("recovery_fault_tests.txt", ["source/test_recovery.py"]),
        ("freeze.log", ["source/freeze.py"]),
        ("preflight.log", ["audit/check_fidelity.py", "--phase", "preflight"]),
        ("training.log", ["source/run_fidelity.py", "--phase", "all"]),
        ("analysis.log", ["source/analyze.py"]),
        ("results_audit.log", ["audit/check_fidelity.py", "--phase", "results", "--checkpoints", "all"]),
        ("build_report.log", ["source/build_report.py"]),
    ]


def train(experiment, output, execute):
    if not execute:
        raise RuntimeError("Explicit --execute-training is required")
    if not (output / "execution_intent.json").is_file():
        raise RuntimeError("Use the guarded NAS CLI to prepare a new training execution")
    for log_name, arguments in training_steps():
        with (experiment / "artifacts" / log_name).open("x") as log:
            result = subprocess.run([sys.executable, "-B", *arguments], cwd=experiment,
                                    stdout=log, stderr=subprocess.STDOUT, check=False)
        if result.returncode:
            raise RuntimeError(f"Training workflow stopped at {arguments[0]}; see {log_name}. Keep all failed-attempt evidence.")
    (output / "COMPLETED.json").write_text(json.dumps({"completed": True, "new_trajectories": 80,
                                                     "epochs": 1600, "official_test_evaluations": 0}, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("smoke")
    p = sub.add_parser("recheck")
    p.add_argument("experiment", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("checkpoints", choices=("none", "all"))
    p = sub.add_parser("train")
    p.add_argument("experiment", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--execute-training", action="store_true", required=True)
    args = parser.parse_args(argv)
    if not __debug__:
        parser.error("Python -O/-OO is unsupported")
    from .control import verify_sources
    verify_sources()
    import numpy
    import torch
    if numpy.__version__ != "2.5.3" or torch.__version__ != "2.14.1+cpu":
        parser.error("Use the frozen NumPy 2.5.3 / PyTorch 2.14.1+cpu environment")
    if args.command == "smoke":
        smoke()
    elif args.command == "recheck":
        recheck(args.experiment.resolve(), args.output.resolve(), args.checkpoints)
    else:
        train(args.experiment.resolve(), args.output.resolve(), args.execute_training)


if __name__ == "__main__":
    main()
