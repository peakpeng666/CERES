"""Explicit NAS subcommands; defaults never download or train."""
from pathlib import Path
import json


def configure(parser):
    sub = parser.add_subparsers(dest="nas_command", required=True)
    sub.add_parser("sources", help="Verify unchanged research source; standard library only")
    p = sub.add_parser("restore", help="Restore all three pinned local ZIPs into a new directory")
    for option in ("code", "discovery", "confirm"):
        p.add_argument("--" + option + "-zip", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p = sub.add_parser("import-data", help="Verify local official binary archive and extract only five training batches")
    p.add_argument("--archive", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p = sub.add_parser("verify", help="Check restored evidence; no optional imports or inference")
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--data", type=Path)
    sub.add_parser("smoke", help="Optional non-scoring synthetic CNN forward check; no optimizer steps")
    p = sub.add_parser("recheck", help="Recompute existing statistics; optional all-160-endpoint inference")
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--checkpoints", choices=("none", "all"), default="none")
    p = sub.add_parser("train", help="Explicit costly new 80-trajectory / 1600-epoch reproduction; gated")
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--archive", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--execute-training", action="store_true")


def run(args):
    from . import control
    if args.nas_command == "sources":
        result = control.verify_sources()
    elif args.nas_command == "restore":
        result = control.restore_evidence({"code": args.code_zip, "discovery": args.discovery_zip,
                                           "confirm": args.confirm_zip}, args.output)
    elif args.nas_command == "import-data":
        result = control.import_data(args.archive, args.output)
    elif args.nas_command == "verify":
        result = control.verify_evidence(args.root)
        if args.data:
            result["data"] = control.verify_data(args.data)
    elif args.nas_command == "smoke":
        result = control.smoke()
    elif args.nas_command == "recheck":
        result = control.recheck(args.root, args.data, args.output, args.checkpoints)
    else:
        result = control.train(args.root, args.archive, args.output, args.execute_training)
    print(json.dumps(result, indent=2))
    return 0
