"""Small offline safety boundary around the unchanged NAS research scripts.

No imports of NumPy/PyTorch, downloads, model calls or training at import time.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from ..archive import ArchiveError, restore, safe_relative, sha256, verify_bundle

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = PROJECT_ROOT / "configs/nas_fidelity.json"
FROZEN = Path(__file__).resolve().parent / "frozen"
TRAIN_FILES = tuple(f"data_batch_{i}.bin" for i in range(1, 6))


def load_spec():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    if spec.get("schema") not in ("ceres-nas-fidelity-v1", "urp-nas-fidelity-v1"):
        raise ArchiveError("Unsupported NAS manifest schema")
    if [a["key"] for a in spec["archives"]] != ["code", "discovery", "confirm"]:
        raise ArchiveError("Expected the three frozen NAS archives")
    combined = {}
    for archive in spec["archives"]:
        for name, meta in archive["files"].items():
            safe_relative(name)
            if name in combined:
                raise ArchiveError("Overlapping NAS archive members")
            combined[name] = meta
    if combined != spec["files"]:
        raise ArchiveError("NAS combined member manifest mismatch")
    dataset = spec["dataset"]
    if set(dataset["extracted_files"]) != set(TRAIN_FILES) or any(
        v["bytes"] != 10000 * 3073 for v in dataset["extracted_files"].values()
    ):
        raise ArchiveError("CIFAR binary schema must contain five 10000 x 3073 batches")
    return spec


def checked_path(path):
    """Reject symlinks before resolving, including all existing ancestors."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ArchiveError(f"Symlinks are not allowed: {part}")
    return path


def check_output(path, inputs=()):
    path = checked_path(path)
    if path.exists():
        raise ArchiveError("Output already exists; use a new directory")
    for source in inputs:
        source = checked_path(source)
        if path == source or path.is_relative_to(source) or source.is_relative_to(path):
            raise ArchiveError("Output cannot overlap input evidence")
    if path == PROJECT_ROOT or PROJECT_ROOT.is_relative_to(path):
        raise ArchiveError("Output cannot contain the project source")
    if path.is_relative_to(PROJECT_ROOT):
        top = path.relative_to(PROJECT_ROOT).parts[0]
        if top not in ("data", "outputs", "review_evidence"):
            raise ArchiveError("Output cannot be inside release source/configuration")
    return path


@contextmanager
def staged_destination(destination, inputs=()):
    """Build privately, validate, then reserve and publish one new local directory."""
    destination = check_output(destination, inputs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".nas-stage-", dir=destination.parent))
    try:
        yield temporary
        # mkdir is exclusive; competing writers cannot merge or overwrite.
        destination.mkdir(exist_ok=False)
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def verify_sources(spec=None):
    spec = spec or load_spec()
    for name, meta in spec["frozen_source"].items():
        p = checked_path(FROZEN / name)
        if not p.is_file() or p.stat().st_size != meta["bytes"] or sha256(p) != meta["sha256"]:
            raise ArchiveError(f"Frozen research source changed: {name}")
    return {"unchanged_research_files": len(spec["frozen_source"])}


def restore_evidence(archives, destination, spec=None):
    spec = spec or load_spec()
    if set(archives) != {a["key"] for a in spec["archives"]}:
        raise ArchiveError("Provide all three pinned local ZIPs")
    inputs = [checked_path(p) for p in archives.values()]
    with staged_destination(destination, inputs) as stage:
        merged = stage / "merged"
        merged.mkdir()
        for part in spec["archives"]:
            restored = stage / part["key"]
            restore(checked_path(archives[part["key"]]), restored, part)
            for name in part["files"]:
                target = merged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.rename(restored / name, target)
            shutil.rmtree(restored)
        verify_bundle(merged, {"files": spec["files"], "sha256": "three-pinned-NAS-archives"})
        for child in list(merged.iterdir()):
            os.rename(child, stage / child.name)
        merged.rmdir()
    return {"restored": True, "files": len(spec["files"]), "training_started": False,
            "downloaded": False, "official_test_extracted": False}


def verify_evidence(root, spec=None):
    spec = spec or load_spec()
    root = checked_path(root)
    result = verify_bundle(root, {"files": spec["files"], "sha256": "three-pinned-NAS-archives"})
    result.update(verify_sources(spec))
    freeze = json.loads((root / "artifacts/pre_run_freeze.json").read_text())
    for name, expected in freeze["files"].items():
        safe_relative(name)
        if sha256(root / name) != expected:
            raise ArchiveError(f"Pre-run freeze mismatch: {name}")
    return result


def check_archive(archive, dataset):
    archive = checked_path(archive)
    if not archive.is_file() or archive.stat().st_size != dataset["archive_bytes"]:
        raise ArchiveError("Official CIFAR binary archive byte length mismatch")
    if sha256(archive) != dataset["archive_sha256"]:
        raise ArchiveError("Official CIFAR binary archive SHA-256 mismatch")
    md5 = hashlib.md5()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            md5.update(block)
    if md5.hexdigest() != dataset["archive_md5"]:
        raise ArchiveError("Official CIFAR binary archive MD5 mismatch")
    return archive


def import_data(archive, destination, spec=None):
    spec = spec or load_spec()
    archive = check_archive(archive, spec["dataset"])
    with staged_destination(destination, [archive]) as stage:
        with tarfile.open(archive, "r:gz") as bundle:
            for name in TRAIN_FILES:
                member = bundle.getmember("cifar-10-batches-bin/" + name)
                expected = spec["dataset"]["extracted_files"][name]
                if not member.isfile() or member.size != expected["bytes"]:
                    raise ArchiveError(f"CIFAR training member schema mismatch: {name}")
                # Never extractall: only the five known training files are read.
                with bundle.extractfile(member) as src, (stage / name).open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        verify_data(stage, spec)
    return {"restored_training_batches": 5, "official_test_extracted": False,
            "official_test_sample_reads": 0, "training_started": False}


def verify_data(root, spec=None):
    spec = spec or load_spec()
    return verify_bundle(checked_path(root), {"files": spec["dataset"]["extracted_files"],
                                             "sha256": spec["dataset"]["archive_sha256"]})


def optional_environment():
    """Check in an isolated process; old modules never import optional packages."""
    code = ("import json,numpy,torch; "
            "assert torch.__version__=='2.14.1+cpu', 'requires frozen CPU torch 2.14.1+cpu'; "
            "assert numpy.__version__=='2.5.3', 'requires frozen NumPy 2.5.3'; "
            "print(json.dumps({'torch':torch.__version__,'numpy':numpy.__version__}))")
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("NAS optional environment unavailable or mismatched. See docs/NAS_FIDELITY.md. "
                           + result.stderr.strip())
    return json.loads(result.stdout)


def _run_worker(command, *arguments):
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    result = subprocess.run([sys.executable, "-B", "-m", "ceres_research.nas_fidelity.worker",
                             command, *map(str, arguments)], env=env, check=False)
    if result.returncode:
        raise RuntimeError(f"NAS {command} failed (exit {result.returncode}); retain output for diagnosis")


def smoke():
    verify_sources()
    optional_environment()
    _run_worker("smoke")
    return {"synthetic_only": True, "new_training_runs": 0, "optimizer_steps": 0,
            "cifar_samples_read": 0, "research_claim": False}


def _copy_legacy(root, parent):
    legacy = parent / "CERES/experiments/architecture_search"
    shutil.copytree(root / "evidence/original_p4", legacy)
    return legacy


def recheck(root, data, output, checkpoints="none"):
    if checkpoints not in ("none", "all"):
        raise ArchiveError("Checkpoints must be none or all")
    spec = load_spec()
    verify_evidence(root, spec)
    verify_data(data, spec)
    optional_environment()
    output = check_output(output, [root, data])
    output.mkdir(parents=True, exist_ok=False)
    # Work only on independent copies; original frozen outputs stay unchanged.
    work = output / "work"
    experiment = work / "experiment"
    shutil.copytree(root, experiment)
    shutil.copytree(data, experiment / "data")
    _copy_legacy(root, work)
    _run_worker("recheck", experiment, output, checkpoints)
    verify_evidence(root, spec)
    verify_data(data, spec)
    return json.loads((output / "engineering_recheck.json").read_text())


def train(root, archive, output, execute=False):
    # This gate is deliberately first: an ordinary command cannot load data,
    # launch an optional process, create an output or start training.
    if execute is not True:
        raise ArchiveError("Training is disabled. Explicit --execute-training is required for 80 trajectories / 1600 epochs.")
    spec = load_spec()
    verify_evidence(root, spec)
    archive = check_archive(archive, spec["dataset"])
    environment = optional_environment()
    output = check_output(output, [root, archive])
    output.mkdir(parents=True, exist_ok=False)
    experiment = output / "experiment"
    experiment.mkdir()
    for folder in ("source", "audit", "evidence"):
        shutil.copytree(root / folder, experiment / folder)
    # Only code from audit/, not stale results, is needed for a new execution.
    for p in (experiment / "audit").iterdir():
        if p.is_file() and p.suffix != ".py":
            p.unlink()
    for name in ("config.json", "PROTOCOL_中文.md", "requirements.lock.txt"):
        shutil.copyfile(root / name, experiment / name)
    for name in ("data", "artifacts", "raw", "checkpoints", "reports"):
        (experiment / name).mkdir()
    shutil.copyfile(archive, experiment / "data/cifar-10-binary.tar.gz")
    _copy_legacy(root, output)
    receipt = {"kind": "explicit_new_reproduction", "environment": environment,
               "historical_pre_run_freeze_sha256": sha256(root / "artifacts/pre_run_freeze.json"),
               "training_runs": 80, "epochs": 1600,
               "note": "Fresh run, fixed original protocol. Historical authorization strings in unchanged source are provenance, not standing authorization."}
    (output / "execution_intent.json").write_text(json.dumps(receipt, indent=2) + "\n")
    _run_worker("train", experiment, output, "--execute-training")
    verify_evidence(root, spec)
    return {"new_training_completed": True, "output": str(output), "historical_evidence_changed": False}
