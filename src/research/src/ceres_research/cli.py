"""Data recovery, verification and replay for CERES research modules."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .archive import ArchiveError, load_manifest, restore, verify_bundle

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "configs" / "archives.json"
WORKSPACE_ROOT = PROJECT_ROOT.parents[1]
IN_WORKSPACE = (WORKSPACE_ROOT / "experiments").is_dir() and (WORKSPACE_ROOT / "data").is_dir()
DEFAULT_DATA_ROOT = WORKSPACE_ROOT / "data/research" if IN_WORKSPACE else PROJECT_ROOT / "data"
DEFAULT_OUTPUT_ROOT = WORKSPACE_ROOT / "experiments/runs/replay" if IN_WORKSPACE else PROJECT_ROOT / "outputs"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ceres-research", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("smoke", help="Run three synthetic examples without historical data")
    comparison = sub.add_parser("compare", help="Compare all three full reruns with pinned results")
    comparison.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    comparison.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    imported = sub.add_parser("restore", help="Import allowlisted files from a pinned local ZIP")
    imported.add_argument("experiment", choices=("parent-selection", "cvrp-selection-crc", "turn-modes"))
    imported.add_argument("--zip", type=Path, required=True)
    imported.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    for command in ("verify", "replay"):
        item = sub.add_parser(command)
        item.add_argument("experiment", choices=("parent-selection", "cvrp-selection-crc", "turn-modes"))
        item.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
        if command == "replay":
            item.add_argument("--output", type=Path, required=True, help="New output directory; never reuse frozen results")
    from .nas_fidelity import cli as nas_cli
    nas_cli.configure(sub.add_parser("nas", help="Optional NAS frozen-evidence recovery and recheck"))
    from .cvrplib import cli as cvrplib_cli
    cvrplib_cli.configure(sub.add_parser("cvrplib", help="Verified CVRPLIB inputs and isolated single-case acceptance"))
    args = parser.parse_args(argv)
    if not __debug__:
        parser.error("Python -O/-OO is unsupported: inherited research checks require assertions")
    try:
        if args.command == "cvrplib":
            return cvrplib_cli.run(args)
        if args.command == "nas":
            return nas_cli.run(args)
        if args.command == "smoke":
            from .smoke import run
            print(json.dumps(run(), indent=2))
            return 0
        if args.command == "compare":
            from .verification import compare
            print(json.dumps(compare(args.data_root, args.output_root, load_manifest(MANIFEST)), indent=2))
            return 0
        spec = load_manifest(MANIFEST)["experiments"][args.experiment]
        root = args.data_root.resolve() / args.experiment
        if args.command == "restore":
            result = restore(args.zip, root, spec)
            print(json.dumps(result, indent=2))
            return 0
        verification = verify_bundle(root, spec)
        if args.command == "verify":
            print(json.dumps(verification, indent=2))
            return 0
        if args.output.is_symlink():
            raise ArchiveError("Output must not be a symlink")
        output = args.output.resolve()
        if output.exists() or output.is_symlink():
            raise ArchiveError("Output already exists; supply a new output directory")
        if output.is_relative_to(args.data_root.resolve()):
            raise ArchiveError("Output cannot be inside restored evidence")
        for folder in ("src", "tests", "configs", "docs", "scripts"):
            if output.is_relative_to(PROJECT_ROOT / folder):
                raise ArchiveError("Output cannot be inside release source/configuration")
        # Atomically reserve this run's output name before inherited writers use
        # exist_ok=True. Never allow two release commands to share a destination.
        output.mkdir(parents=True, exist_ok=False)
        if args.experiment == "parent-selection":
            from . import parent_selection
            parent_selection.ROOT = root
            parent_selection.run(output)
        elif args.experiment == "cvrp-selection-crc":
            from .cvrp_selection import experiment, preflight
            experiment.ROOT = preflight.ROOT = root
            result = experiment.run(output)
            print(json.dumps({key: result[key] for key in ("n_instances", "historical_real_solver_runs",
                             "new_solver_runs", "new_api_calls", "primary_crc_all_safe_fallback")}, indent=2))
        else:
            if sys.platform == "win32":
                raise ArchiveError("Formal turn replay requires Unix signal timers; use Linux or macOS")
            from .turn_modes import run
            run.ROOT = root
            run.replay("formal", output)
        # Read-only evidence must remain identical after replay.
        verify_bundle(root, spec)
        return 0
    except (ArchiveError, OSError, RuntimeError, AssertionError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
