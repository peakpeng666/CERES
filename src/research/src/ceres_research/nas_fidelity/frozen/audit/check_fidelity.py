#!/usr/bin/env python3
"""Read-only independent NAS audit; no training and no official-test loader.

Writes reports only under audit/. Run with the experiment .venv Python.
  audit/check_fidelity.py --phase preflight
  audit/check_fidelity.py --phase results --checkpoints all
"""
import argparse
import collections
import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "audit"
ARCHS = [dict(zip(("depth", "width", "kernel"), x))
         for x in itertools.product((2, 3), (8, 16), (3, 5))]
TRAIN_FILES = [f"data_batch_{i}.bin" for i in range(1, 6)]


def aid(a):
    return f"d{a['depth']}_w{a['width']}_k{a['kernel']}"


def digest(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read(p):
    return json.loads(Path(p).read_text())


def jsonl(p):
    return [json.loads(line) for line in Path(p).read_text().splitlines() if line.strip()]


def same_number(a, b, tol=1e-12):
    return a is None and b is None if a is None or b is None else math.isclose(a, b, abs_tol=tol)


def model_cls(torch):
    nn = torch.nn

    class IndependentCNN(nn.Module):
        def __init__(self, arch):
            super().__init__()
            modules = []
            in_channels = 3
            for i in range(arch["depth"]):
                out_channels = arch["width"] * 2 ** i
                modules.append(nn.Conv2d(in_channels, out_channels,
                                         kernel_size=arch["kernel"], padding=arch["kernel"] // 2))
                modules.append(nn.ReLU())
                modules.append(nn.AvgPool2d(kernel_size=2))
                in_channels = out_channels
            modules += [nn.AdaptiveAvgPool2d((1, 1)), nn.Flatten(), nn.Linear(in_channels, 10)]
            self.layers = nn.Sequential(*modules)

        def forward(self, inputs):
            return self.layers(inputs)

    return IndependentCNN


def independent_macs(a):
    return sum((32 // 2 ** i) ** 2 * (a["width"] * 2 ** i)
               * (3 if i == 0 else a["width"] * 2 ** (i - 1)) * a["kernel"] ** 2
               for i in range(a["depth"])) + a["width"] * 2 ** (a["depth"] - 1) * 10


def ranks(v):
    """Average ranks, highest score gets rank 1. Ties are not broken."""
    return [1 + sum(y > x for y in v) + 0.5 * (sum(y == x for y in v) - 1) for x in v]


def spearman(x, y):
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sx, sy = sum((v - mx) ** 2 for v in rx), sum((v - my) ** 2 for v in ry)
    return None if not sx * sy else sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / math.sqrt(sx * sy)


def tau_b(x, y):
    n_concordant = n_discordant = ties_x_only = ties_y_only = 0
    for i, j in itertools.combinations(range(len(x)), 2):
        dx, dy = x[i] - x[j], y[i] - y[j]
        if dx == 0 and dy != 0:
            ties_x_only += 1
        elif dy == 0 and dx != 0:
            ties_y_only += 1
        elif dx * dy > 0:
            n_concordant += 1
        elif dx * dy < 0:
            n_discordant += 1
    nt = n_concordant + n_discordant
    den = math.sqrt((nt + ties_x_only) * (nt + ties_y_only))
    return None if den == 0 else (n_concordant - n_discordant) / den


def run(args):
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    report = {"audit_type": "independent_read_only", "phase": args.phase,
              "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "errors": [], "warnings": [], "checks": [], "official_test_samples_read": 0,
              "training_optimizer_steps": 0}

    def check(value, label):
        report["checks"].append({"check": label, "passed": bool(value)})
        if not value:
            report["errors"].append(label)

    old = ROOT / "evidence/original_p4"
    manifest = read(old / "artifacts/data_manifest.json")
    split = read(old / "artifacts/split.json")
    oldcfg = read(old / "config.json")
    check(digest(old / "artifacts/split.json") == manifest["split_sha256"], "original split SHA256")
    s = {k: set(split[k]) for k in ("train", "validation", "reserved")}
    check([len(split[k]) for k in s] == [4000, 1000, 45000], "split sizes 4000/1000/45000")
    check(all(len(s[k]) == len(split[k]) for k in s), "no within-split duplicate indices")
    check(not (s["train"] & s["validation"] or s["train"] & s["reserved"] or s["validation"] & s["reserved"]), "split disjointness")
    check(set.union(*s.values()) == set(range(50000)), "training archive index coverage")
    check(not any(p.name == "test_batch.bin" for p in ROOT.rglob("test_batch.bin")), "no extracted official test file")
    fence = read(ROOT / "evidence/original_p4_fence.json")
    changed_original = [name for name, expected in fence["files"].items()
                        if not (ROOT.parent / name).is_file() or digest(ROOT.parent / name) != expected]
    check(not changed_original, "original P4 file fence unchanged")
    report["original_p4_changed_files"] = changed_original

    cfg_path = ROOT / "config.json"
    cfg = read(cfg_path) if cfg_path.exists() else None
    if cfg is None:
        report["warnings"].append("config.json not yet present; final preflight pending")
    else:
        for new, original in (("batch_size", "batch_size"), ("lr", "child_lr"), ("weight_decay", "child_weight_decay")):
            check(cfg[new] == oldcfg[original], f"training recipe {new} matches original P4")
        check(cfg["low_epochs"] == 2 and cfg["high_epochs"] == 20, "frozen endpoints 2/20 epochs")
        check(cfg["split_sha256"] == manifest["split_sha256"], "config split digest matches original")
        discovery, confirmation = cfg["discovery_seeds"], cfg["confirmation_seeds"]
        check(len(set(discovery)) == 5 and len(set(confirmation)) == 5 and not (set(discovery) & set(confirmation)), "five disjoint discovery/confirmation seeds")
        expected = {(g, seed, aid(a)) for g, seeds in (("discovery", discovery), ("confirmation", confirmation))
                    for seed in seeds for a in ARCHS}
        actual = [(r["group"], r["seed"], aid(r["architecture"])) for r in cfg["run_order"]]
        check(len(actual) == 80 and set(actual) == expected, "run order contains each of 80 planned trajectories once")

    freeze_path = ROOT / "artifacts/pre_run_freeze.json"
    fh = None
    if freeze_path.exists():
        freeze = read(freeze_path)
        fh = digest(freeze_path)
        changed = [name for name, expected in freeze["files"].items()
                   if not (ROOT / name).is_file() or digest(ROOT / name) != expected]
        check(not changed, "pre-run frozen file hashes")
        report["freeze_sha256"] = fh
        report["frozen_changed_files"] = changed
        check(all(p in freeze["files"] for p in ("config.json", "source/core.py", "source/run_fidelity.py")), "freeze covers config/model/runner")
    else:
        report["warnings"].append("pre-run freeze absent; final preflight pending")

    ready_data = all((ROOT / "data" / n).exists() for n in TRAIN_FILES)
    if ready_data:
        check(all(digest(ROOT / "data" / n) == manifest["extracted_files"][n]["sha256"] for n in TRAIN_FILES), "all five train file hashes match original")
    else:
        report["warnings"].append("training binaries not all available; data hash verification pending")

    # Imports perform no main function, training, or data reads.
    import numpy as np
    import torch
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    IndependentCNN = model_cls(torch)
    modules = []
    for name, path in (("audit_original_p4", old / "run_experiment.py"), ("audit_candidate", ROOT / "source/core.py")):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    report["architecture_checks"] = []
    for a in ARCHS:
        states = []
        for klass in (modules[0].TinyCNN, modules[1].TinyCNN, IndependentCNN):
            torch.manual_seed(918273)
            m = klass(a)
            states.append(m.state_dict())
        match = all(set(states[0]) == set(other) and all(torch.equal(states[0][k], other[k]) for k in states[0]) for other in states[1:])
        check(match, f"{aid(a)} model construction and exact seeded initialization")
        check(modules[0].forward_macs(a) == modules[1].macs(a) == independent_macs(a), f"{aid(a)} analytic MAC count")
        report["architecture_checks"].append({"architecture_id": aid(a), "parameters": sum(v.numel() for v in states[0].values()), "forward_conv_linear_macs": independent_macs(a)})
    report["environment"] = {"python": sys.version, "torch": torch.__version__, "numpy": np.__version__, "threads": torch.get_num_threads()}

    if ready_data:
        maps = [np.memmap(ROOT / "data" / n, mode="r", dtype=np.uint8, shape=(10000, 3073)) for n in TRAIN_FILES]
        # Only selected train/validation labels are read; reserved images are not materialized.
        for key in ("train", "validation"):
            labels = [int(maps[i // 10000][i % 10000, 0]) for i in split[key]]
            check(collections.Counter(labels) == {c: (400 if key == "train" else 100) for c in range(10)}, f"{key} class balance")

    if args.phase == "results":
        check(cfg is not None and fh is not None and ready_data, "results prerequisites complete")
        if cfg is not None and fh is not None and ready_data:
            records = {}
            journal = jsonl(ROOT / "raw/epochs.jsonl")
            journal_by_run = collections.defaultdict(list)
            for row in journal:
                journal_by_run[row["run_id"]].append(row)
            attempts = jsonl(ROOT / "raw/attempts.jsonl")
            report["attempt_status_counts"] = dict(collections.Counter(x["status"] for x in attempts))
            starts_by_run = collections.Counter(x["run_id"] for x in attempts if x["status"] == "started")
            terminal_by_run = collections.Counter(x["run_id"] for x in attempts if x["status"] in ("failed", "completed"))
            report["attempt_known_wall_seconds"] = sum(x.get("wall_seconds", 0.) for x in attempts if x["status"] in ("failed", "completed"))
            report["attempt_known_cpu_seconds"] = sum(x.get("cpu_seconds", 0.) for x in attempts if x["status"] in ("failed", "completed"))
            report["attempt_orphan_starts"] = dict(starts_by_run - terminal_by_run)
            failures = [x for x in attempts if x["status"] == "failed"]
            report["failed_attempts"] = failures
            if failures:
                report["warnings"].append("failed attempts exist and must remain in the cost ledger")
            for row in cfg["run_order"]:
                name = f"{row['group']}_{row['seed']}_{aid(row['architecture'])}"
                path = ROOT / "raw" / (name + ".json")
                check(path.exists(), f"completed result {name}")
                if not path.exists():
                    continue
                r = read(path)
                records[name] = r
                check(not r["timing_complete"] or starts_by_run[name] == 1, f"{name} no false complete timing after restart")
                check(r["run_id"] == name and r["group"] == row["group"] and r["seed"] == row["seed"] and r["architecture"] == row["architecture"] and r["freeze_sha256"] == fh, f"{name} identity")
                check([e["epoch"] for e in r["epochs"]] == list(range(1, 21)), f"{name} complete ordered epochs")
                check(r["epochs"] == journal_by_run[name], f"{name} epoch journal exact consistency")
                check(r["forward_conv_linear_macs_per_image"] == independent_macs(row["architecture"]), f"{name} MACs")
                for e in r["epochs"]:
                    v = e["validation"]
                    check(v["n"] == 1000 and 0 <= v["correct"] <= 1000 and v["accuracy"] == v["correct"] / v["n"] and math.isfinite(v["loss"]) and v["loss"] >= 0,
                          f"{name} epoch {e['epoch']} metric integrity")
                    check(e["optimizer_steps"] == math.ceil(4000 / cfg["batch_size"]) and e["train_presentations"] == 4000 and e["validation_presentations"] == 1000,
                          f"{name} epoch {e['epoch']} accounting")
                check([x["epoch"] for x in r["checkpoints"]] == [2, 20], f"{name} two endpoint checkpoints")
                for cp in r["checkpoints"]:
                    check(digest(ROOT / cp["path"]) == cp["sha256"], f"{name} e{cp['epoch']} checkpoint hash")
                    check(digest(ROOT / cp["predictions_path"]) == cp["predictions_sha256"], f"{name} e{cp['epoch']} predictions hash")
            check(len(records) == 80 and len(journal) == 1600, "80 completed trajectories / 1600 epoch records")
            if len(records) == 80:
                ids = [aid(a) for a in ARCHS]
                def values(group, seeds, epoch):
                    return [[records[f"{group}_{s}_{a}"]["epochs"][epoch - 1]["validation"]["correct"] for a in ids] for s in seeds]
                low = values("discovery", cfg["discovery_seeds"], 2)
                high = values("discovery", cfg["discovery_seeds"], 20)
                conf = values("confirmation", cfg["confirmation_seeds"], 20)
                lo, hi, ref = [np.asarray(x, dtype=np.int64).sum(axis=0).tolist() for x in (low, high, conf)]
                pick = lambda v: min(range(8), key=lambda i: (-v[i], ids[i]))
                pl, ph, pr = pick(lo), pick(hi), pick(ref)
                selection = read(ROOT / "artifacts/selection_freeze.json")
                receipt = read(ROOT / "artifacts/selection_freeze_receipt.json")
                check(receipt["selection_sha256"] == digest(ROOT / "artifacts/selection_freeze.json") and receipt["pre_run_freeze_sha256"] == fh, "selection freeze receipt hashes")
                check(selection["low_selected"] == ids[pl] and selection["high_selected"] == ids[ph], "frozen selections independently recomputed")
                check(all(digest(ROOT / p) == h for p, h in selection["discovery_result_hashes"].items()), "selection discovery hashes unchanged")
                discovery_finishes = [r["completed_at_utc"] for r in records.values() if r["group"] == "discovery"]
                confirmation_starts = [a["at_utc"] for a in attempts if a["group"] == "confirmation" and a["status"] == "started"]
                check(max(discovery_finishes) <= selection["at_utc"] <= min(confirmation_starts), "all discovery precedes selection freeze and confirmation starts")
                delta = [(v[ph] - v[pl]) / 10 for v in conf]
                sd = math.sqrt(sum((x - sum(delta) / 5) ** 2 for x in delta) / 4)
                report["independent_statistics"] = {
                    "architecture_ids": ids, "discovery_low_correct_sums": lo, "discovery_high_correct_sums": hi,
                    "confirmation_correct_sums": ref, "low_selected": ids[pl], "high_selected": ids[ph], "reference_best": ids[pr],
                    "spearman_low_vs_confirmation": spearman(lo, ref), "spearman_high_vs_confirmation": spearman(hi, ref),
                    "kendall_tau_b_low_vs_confirmation": tau_b(lo, ref), "kendall_tau_b_high_vs_confirmation": tau_b(hi, ref),
                    "spearman_low_vs_high_discovery": spearman(lo, hi),
                    "high_minus_low_confirmation_pp_by_seed": delta, "high_minus_low_confirmation_mean_pp": sum(delta) / 5,
                    "high_minus_low_confirmation_sd_pp": sd,
                    "low_top1_empirical_regret_pp": (max(ref) - ref[pl]) / 50,
                    "high_top1_empirical_regret_pp": (max(ref) - ref[ph]) / 50,
                    "interpretation": "fixed validation / fixed eight-architecture space / five confirmation training seeds / 20 epoch empirical reference; not independent test or convergence ground truth"}

                rng = np.random.default_rng(cfg["bootstrap"]["seed"])
                boot = collections.defaultdict(list)
                lowarr, higharr, confarr = [np.asarray(x, dtype=np.int64) for x in (low, high, conf)]
                for _ in range(cfg["bootstrap"]["replicates"]):
                    ia, ib = rng.integers(0, 5, 5), rng.integers(0, 5, 5)
                    bl, bh, br = lowarr[ia].sum(0).tolist(), higharr[ia].sum(0).tolist(), confarr[ib].sum(0).tolist()
                    for key, x, y in (("discovery_2_vs_20", bl, bh), ("discovery_2_vs_confirmation_20", bl, br), ("discovery_20_vs_confirmation_20", bh, br)):
                        boot[key + "_tau"].append(tau_b(x, y))
                        boot[key + "_rho"].append(spearman(x, y))
                    boot["low_regret_pp"].append((max(br) - br[pl]) / 50)
                    boot["high_regret_pp"].append((max(br) - br[ph]) / 50)
                    boot["high_minus_low_pp"].append((br[ph] - br[pl]) / 50)
                intervals = {}
                for k, v in boot.items():
                    valid = sorted(x for x in v if x is not None)
                    def quantile(p):
                        if not valid:
                            return None
                        position = (len(valid) - 1) * p
                        i = math.floor(position)
                        return valid[i] + (position - i) * (valid[min(i + 1, len(valid) - 1)] - valid[i])
                    intervals[k] = {"low": quantile(.025), "high": quantile(.975), "valid_replicates": len(valid)}
                report["independent_bootstrap_intervals"] = intervals
                summary_path = ROOT / "reports/summary.json"
                if summary_path.exists():
                    summary = read(summary_path)
                    for key, x, y in (("discovery_2_vs_20", lo, hi), ("discovery_2_vs_confirmation_20", lo, ref), ("discovery_20_vs_confirmation_20", hi, ref)):
                        z = summary["correlations"][key]
                        check(same_number(z["kendall"]["tau_b"], tau_b(x, y)) and same_number(z["spearman_rho"], spearman(x, y)), f"reported {key} agrees with independent formulas")
                    for k, v in intervals.items():
                        actual = summary["bootstrap_intervals"][k]
                        ok = actual["valid_replicates"] == v["valid_replicates"]
                        for endpoint in ("low", "high"):
                            ok = ok and ((actual[endpoint] is None and v[endpoint] is None) or (actual[endpoint] is not None and v[endpoint] is not None and math.isclose(actual[endpoint], v[endpoint], abs_tol=1e-12)))
                        check(ok, f"reported bootstrap {k} agrees with independent 10000-resample implementation")
                    for g in ("discovery", "confirmation"):
                        group_runs = [r for r in records.values() if r["group"] == g]
                        ee = [e for r in group_runs for e in r["epochs"]]
                        expected_cost = {"runs": 40, "epochs": 800, "optimizer_steps": 25600, "train_presentations": 3200000, "validation_presentations": 800000,
                                         "train_forward_conv_linear_macs": sum(independent_macs(a) for a in ARCHS) * 5 * 20 * 4000,
                                         "validation_forward_conv_linear_macs": sum(independent_macs(a) for a in ARCHS) * 5 * 20 * 1000}
                        for k, v in expected_cost.items():
                            check(summary["costs"][g][k] == v, f"reported {g} {k} agrees with audit")
                        for k in ("train_wall_seconds", "train_cpu_seconds", "validation_wall_seconds", "validation_cpu_seconds"):
                            check(math.isclose(summary["costs"][g][k], sum(e[k] for e in ee), abs_tol=1e-9), f"reported {g} {k} agrees with audit")
                else:
                    report["warnings"].append("reports/summary.json unavailable; independent statistics computed but report comparison pending")

            if args.checkpoints == "all":
                indexes = split["validation"]
                packed = np.stack([maps[i // 10000][i % 10000] for i in indexes])
                inputs = torch.tensor(packed[:, 1:].copy(), dtype=torch.float32).reshape(-1, 3, 32, 32) / 255
                labels = torch.tensor(packed[:, 0].copy(), dtype=torch.long)
                endpoints = []
                for name, r in records.items():
                    for cp in r["checkpoints"]:
                        model = IndependentCNN(r["architecture"])
                        state = torch.load(ROOT / cp["path"], map_location="cpu", weights_only=True)
                        check(state["run_id"] == name and state["epoch"] == cp["epoch"] and state["freeze_sha256"] == fh, f"{name} e{cp['epoch']} loaded state identity")
                        model.load_state_dict(state["model_state_dict"], strict=True)
                        model.eval()
                        pred, loss_sum = [], 0.
                        with torch.inference_mode():
                            for i in range(0, len(labels), 73):
                                logits = model(inputs[i:i + 73])
                                pred.extend(logits.argmax(dim=1).tolist())
                                loss_sum += torch.nn.functional.cross_entropy(logits, labels[i:i + 73], reduction="sum").item()
                        expected = r["epochs"][cp["epoch"] - 1]["validation"]
                        saved = read(ROOT / cp["predictions_path"])
                        correct = int((np.asarray(pred) == labels.numpy()).sum())
                        check(pred == saved["predictions"] and correct == expected["correct"] and math.isclose(loss_sum / 1000, expected["loss"], abs_tol=2e-6), f"{name} e{cp['epoch']} independent all-1000 predictions/loss")
                        optimizer_steps = [int(v["step"]) for v in state["optimizer_state_dict"]["state"].values()]
                        check(optimizer_steps and set(optimizer_steps) == {cp["epoch"] * 32}, f"{name} e{cp['epoch']} Adam state steps")
                        endpoints.append({"run_id": name, "epoch": cp["epoch"], "correct": correct, "n": 1000, "loss": loss_sum / 1000})
                report["independent_checkpoint_endpoints"] = endpoints
                report["independent_inference_batch_size"] = 73
                report["audit_validation_presentations"] = len(endpoints) * 1000

    report["passed"] = not report["errors"]
    report["complete_preflight"] = bool(cfg is not None and fh is not None and ready_data and report["passed"])
    report["wall_seconds"] = time.perf_counter() - start_wall
    report["cpu_seconds"] = time.process_time() - start_cpu
    output = AUDIT / (args.output or f"{args.phase}_audit.json")
    if output.parent.resolve() != AUDIT.resolve():
        raise ValueError("Audit output must stay in audit directory")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"passed": report["passed"], "complete_preflight": report["complete_preflight"], "errors": report["errors"], "warnings": report["warnings"], "output": str(output), "wall_seconds": report["wall_seconds"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("preflight", "results"), default="preflight")
    parser.add_argument("--checkpoints", choices=("none", "all"), default="none")
    parser.add_argument("--output", help="JSON report filename under audit/")
    sys.exit(run(parser.parse_args()))
