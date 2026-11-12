"""CVRPLIB commands, separate from the historical synthetic CRC replay."""
import json
from pathlib import Path
import time
import datetime

from .audit import audit_panel
from .io import contract_for, digest, parse_vrp, sha
from .runner import run_payload

REPO = Path(__file__).resolve().parents[5]
CASES = Path(__file__).resolve().parents[3] / "configs/cvrplib_cases.json"


def reserve_run_key(registry, key, output):
    """Atomic, persistent claim. Failed attempts also consume the scientific key.

    The registry is project-wide, not per output directory. A new experiment
    must use an explicitly different registry; never silently overwrite one.
    """
    registry = Path(registry)
    registry.mkdir(parents=True, exist_ok=True)
    name = digest(key)
    with (registry / (name + ".json")).open("x") as stream:
        json.dump({"key": key, "output": str(Path(output).resolve()),
                   "claimed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}, stream, indent=2)
    return name


def configure(parser):
    sub = parser.add_subparsers(dest="cvrplib_command", required=True)
    audit = sub.add_parser("audit", help="Read-only 53-input/reference and base audit; no optimization")
    audit.add_argument("--repo", type=Path, default=REPO)
    audit.add_argument("--output", type=Path, required=True)
    one = sub.add_parser("run-one", help="A single acceptance call; no batch or paid-model entrypoint")
    one.add_argument("--instance", type=Path, required=True)
    one.add_argument("--manifest", type=Path, default=CASES)
    one.add_argument("--algorithm", choices=['savings', 'T', 'TRS', 'TSR', 'pyvrp', 'ortools', 'nearest_neighbor', 'seeded_sweep', 'savings_vnd', 'multistart_vnd', 'random_cheapest', 'random_regret2', 'worst_cheapest', 'worst_regret2', 'ga_random_keys', 'pso_random_keys', 'de_random_keys'], required=True)
    one.add_argument("--seed", type=int, required=True)
    one.add_argument("--budget", type=float, choices=[1, 5], required=True)
    one.add_argument("--capped-x", action="store_true", help="Separately labelled derived X contract")
    one.add_argument("--output", type=Path, required=True)
    one.add_argument("--run-registry", type=Path, default=REPO / "experiments/runs/cvrplib-run-registry",
                     help="Shared atomic run-key registry; retain failed attempt claims")
    score = sub.add_parser("score", help="Independently score a saved run; references remain outside solver")
    score.add_argument("--repo", type=Path, default=REPO)
    score.add_argument("--manifest", type=Path, default=CASES)
    score.add_argument("--result", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)


def run(args):
    if args.cvrplib_command == "score":
        from .evaluate import evaluate_saved
        result = evaluate_saved(args.repo, args.manifest, args.result)
        with args.output.open('x') as f:
            json.dump(result, f, indent=2)
            f.write('\n')
        print(json.dumps({'eligible_for_gap':result['eligible_for_gap'],
                          'gap_percent':result['gap_percent']}))
        return 0 if result['eligible_for_gap'] else 1
    if args.cvrplib_command == "audit":
        print(json.dumps(audit_panel(args.repo, args.output), indent=2))
        return 0
    started = time.monotonic()
    args.output.mkdir(parents=True, exist_ok=False)
    def record(value):
        (args.output / "result.json").write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    try:
        p = parse_vrp(args.instance.read_text())
        manifest = json.loads(args.manifest.read_text())
        case = next(c for c in manifest["cases"] if c["case_id"] == p["name"])
        if sha(args.instance) != case["instance_sha256"]:
            raise ValueError("input differs from verified manifest")
        contract = contract_for(case, p, args.capped_x)
        payload = {k: p[k] for k in ("coordinates", "demands", "capacity")}
        payload.update(K=contract["K"], algorithm=args.algorithm, seed=args.seed, budget_seconds=args.budget)
        source_dir = Path(__file__).parent
        source_digest = digest({str(f.relative_to(source_dir)): sha(f)
                 for f in sorted(source_dir.rglob("*.py"))})
        run_key = reserve_run_key(args.run_registry, {"instance_sha256": sha(args.instance),
            "algorithm": args.algorithm, "seed": args.seed, "budget_seconds": args.budget,
            "contract": contract, "solver_source_sha256": source_digest}, args.output)
        preparation_wall = time.monotonic()-started
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        record({"status": "DUPLICATE_RUN_KEY" if isinstance(exc, FileExistsError) else "PARSE_ERROR", "feasible": False, "cost": None, "error": str(exc),
                "purpose": "acceptance_only", "algorithm": args.algorithm, "seed": args.seed,
                "budget_seconds": args.budget, "input": str(args.instance)})
        return 2
    try:
        r = run_payload(payload)
    except (OSError, ValueError, RuntimeError) as exc:
        r = {"schema":"ceres-cvrplib-result-v1", "purpose":"acceptance_only",
             "status": "INFRA_ERROR", "error": str(exc), "feasible": False,
             "cost": None, "routes":None, "algorithm":payload['algorithm'],
             "seed":payload['seed'], "budget_seconds":payload['budget_seconds'],
             "payload_sha256":digest(payload)}
    source_dir = Path(__file__).parent
    final_source_digest=digest({str(f.relative_to(source_dir)):sha(f)
                               for f in sorted(source_dir.rglob('*.py'))})
    if final_source_digest!=source_digest:
        r.update(status='INFRA_ERROR',error='solver sources changed during the run',feasible=False,cost=None,routes=None)
    r.update(run_key=run_key, run_id=args.output.name, case_id=p["name"], contract=contract, instance_sha256=sha(args.instance),
             case_manifest_sha256=sha(args.manifest), parse_setup_wall_seconds=preparation_wall,
             full_wall_seconds=time.monotonic()-started, gap=None,
             gap_note="reference evaluation is a separate post-run operation",
             solver_source_sha256=source_digest, solver_source_end_sha256=final_source_digest)
    record(r)
    print(json.dumps({k: r.get(k) for k in ("case_id", "status", "algorithm", "seed", "cost", "process_wall_seconds")}))
    return 0 if r["feasible"] else 1
