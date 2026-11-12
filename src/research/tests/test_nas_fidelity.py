"""Standard-library-only NAS boundaries. No training or optional imports."""
import hashlib
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from ceres_research.archive import ArchiveError
from ceres_research.nas_fidelity import control, worker


class NASContract(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dest = self.root / "restored"

    def bundles(self, unsafe=None):
        paths, specs, files = {}, [], {}
        for key in ("code", "discovery", "confirm"):
            path = self.root / (key + ".zip")
            data, name = key.encode(), key + ".txt"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("nas/" + name, data)
                if key == "code":
                    if unsafe == "traversal": z.writestr("../escape", b"bad")
                    if unsafe == "duplicate": z.writestr("nas/" + name, data)
                    if unsafe == "symlink":
                        i = zipfile.ZipInfo("nas/link")
                        i.create_system = 3
                        i.external_attr = (stat.S_IFLNK | 0o777) << 16
                        z.writestr(i, "outside")
            metadata = {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
            files.update(metadata)
            specs.append({"key": key, "bytes": path.stat().st_size, "sha256": control.sha256(path),
                          "zip_prefix": "nas", "files": metadata})
            paths[key] = path
        return paths, {"archives": specs, "files": files}

    def test_restore_all_three_without_execution(self):
        paths, spec = self.bundles()
        r = control.restore_evidence(paths, self.dest, spec)
        self.assertEqual(r["files"], 3)
        self.assertFalse(r["training_started"] or r["downloaded"])
        self.assertEqual({p.name for p in self.dest.iterdir()}, {"code.txt", "discovery.txt", "confirm.txt"})

    def test_missing_archive(self):
        paths, spec = self.bundles()
        del paths["confirm"]
        with self.assertRaises(ArchiveError): control.restore_evidence(paths, self.dest, spec)
        self.assertFalse(self.dest.exists())

    def test_corrupt_archive_no_partial_destination(self):
        paths, spec = self.bundles()
        paths["confirm"].write_bytes(b"bad")
        with self.assertRaises(ArchiveError): control.restore_evidence(paths, self.dest, spec)
        self.assertFalse(self.dest.exists())
        self.assertFalse(list(self.root.glob(".nas-stage-*")))

    def test_bad_member_hash(self):
        paths, spec = self.bundles()
        spec["archives"][0]["files"]["code.txt"]["sha256"] = "0" * 64
        with self.assertRaises(ArchiveError): control.restore_evidence(paths, self.dest, spec)
        self.assertFalse(self.dest.exists())

    def test_unsafe_archive_members(self):
        for unsafe in ("traversal", "duplicate", "symlink"):
            with self.subTest(unsafe=unsafe):
                paths, spec = self.bundles(unsafe)
                with self.assertRaises(ArchiveError): control.restore_evidence(paths, self.dest, spec)
                self.assertFalse(self.dest.exists())
                self.assertFalse((self.root / "escape").exists())

    def test_existing_destination_kept(self):
        paths, spec = self.bundles()
        self.dest.mkdir()
        marker = self.dest / "keep"
        marker.write_text("keep")
        with self.assertRaises(ArchiveError): control.restore_evidence(paths, self.dest, spec)
        self.assertEqual(marker.read_text(), "keep")

    def test_symlink_ancestor(self):
        real, alias = self.root / "real", self.root / "alias"
        real.mkdir(); alias.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ArchiveError): control.check_output(alias / "new")
        self.assertEqual(list(real.iterdir()), [])

    def test_output_overlap(self):
        for output, source in ((self.root / "evidence/out", self.root / "evidence"),
                               (self.root / "new", self.root / "new/in")):
            with self.assertRaises(ArchiveError): control.check_output(output, [source])

    def test_source_output(self):
        with self.assertRaises(ArchiveError): control.check_output(control.PROJECT_ROOT / "src/new-output")

    def test_training_gate_before_other_work(self):
        with patch.object(control, "load_spec", side_effect=AssertionError("not reached")), \
             patch.object(control, "_run_worker", side_effect=AssertionError("not reached")):
            with self.assertRaisesRegex(ArchiveError, "--execute-training"):
                control.train(self.root / "absent", self.root / "missing", self.dest)
        self.assertFalse(self.dest.exists())

    def test_worker_training_gate(self):
        with patch.object(worker.subprocess, "run", side_effect=AssertionError("not run")):
            with self.assertRaisesRegex(RuntimeError, "--execute-training"): worker.train(self.root, self.root, False)
            with self.assertRaisesRegex(RuntimeError, "guarded NAS CLI"): worker.train(self.root, self.root, True)

    def test_cifar_archive_wrong_size(self):
        wrong = self.root / "wrong.tar.gz"
        wrong.write_bytes(b"wrong archive")
        with self.assertRaises(ArchiveError): control.import_data(wrong, self.dest)
        self.assertFalse(self.dest.exists())

    def test_hard_binary_schema(self):
        spec = control.load_spec()
        self.assertEqual(spec["dataset"]["archive_bytes"], 170052171)
        self.assertEqual(spec["dataset"]["archive_md5"], "c32a1d4ab5d03f1284b67883e8d87530")
        self.assertEqual(len(spec["files"]), 683)
        self.assertEqual(set(spec["dataset"]["extracted_files"]), set(control.TRAIN_FILES))
        self.assertEqual(spec["dataset"]["split_sha256"], "8ad2ccf80dfd3a0b175a24ca0941ff8169da2a055fd9602bb9b0660a357b362a")
        self.assertFalse(any("test_batch.bin" in n for n in spec["files"]))

    def test_frozen_source_unchanged(self):
        self.assertEqual(control.verify_sources()["unchanged_research_files"], 12)

    def test_training_order(self):
        steps = [args[0] for _, args in worker.training_steps()]
        self.assertLess(steps.index("source/prepare_data.py"), steps.index("source/freeze.py"))
        self.assertLess(steps.index("source/test_recovery.py"), steps.index("source/freeze.py"))
        self.assertLess(steps.index("source/freeze.py"), steps.index("source/run_fidelity.py"))
        self.assertEqual(worker.training_steps()[6][1], ["source/run_fidelity.py", "--phase", "all"])

    def test_imports_without_optional_packages(self):
        code = ("import sys; from ceres_research.nas_fidelity import control,worker,cli; "
                "assert 'torch' not in sys.modules and 'numpy' not in sys.modules; control.verify_sources()")
        r = subprocess.run([sys.executable, "-S", "-c", code], cwd=control.PROJECT_ROOT,
                           env={"PYTHONPATH": str(control.PROJECT_ROOT / "src")}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_training_without_flag(self):
        r = subprocess.run([sys.executable, "-S", str(control.PROJECT_ROOT / "scripts/ceres.py"), "nas", "train",
                            "--root", "absent", "--archive", "absent", "--output", str(self.dest)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--execute-training", r.stderr)
        self.assertFalse(self.dest.exists())

    def test_optimized_python_refused(self):
        r = subprocess.run([sys.executable, "-O", str(control.PROJECT_ROOT / "scripts/ceres.py"), "nas", "sources"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unsupported", r.stderr)


class NASFailureBoundaries(unittest.TestCase):
    def test_member_verification_rejects_extra_data(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "batch.bin").write_bytes(b"ok")
            spec = {"dataset": {"archive_sha256": "unused", "extracted_files": {
                "batch.bin": {"bytes": 2, "sha256": hashlib.sha256(b"ok").hexdigest()}}}}
            control.verify_data(root, spec)
            (root / "test_batch.bin").write_bytes(b"forbidden")
            with self.assertRaises(ArchiveError): control.verify_data(root, spec)

    def test_archive_hash_and_md5_both_checked(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "archive"
            p.write_bytes(b"sample")
            spec = {"archive_bytes": 6, "archive_sha256": "0" * 64, "archive_md5": "bad"}
            with self.assertRaisesRegex(ArchiveError, "SHA-256"): control.check_archive(p, spec)
            spec["archive_sha256"] = control.sha256(p)
            with self.assertRaisesRegex(ArchiveError, "MD5"): control.check_archive(p, spec)

    def test_optional_environment_failure_no_training(self):
        from types import SimpleNamespace
        failure = SimpleNamespace(returncode=1, stderr="ModuleNotFoundError: torch", stdout="")
        with patch.object(control.subprocess, "run", return_value=failure):
            with self.assertRaisesRegex(RuntimeError, "optional environment"): control.optional_environment()

    def test_training_workflow_stops_at_first_failure(self):
        from types import SimpleNamespace
        for failed_index in range(len(worker.training_steps())):
            with self.subTest(failed_index=failed_index), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / "execution_intent.json").write_text("{}")
                (root / "artifacts").mkdir()
                outcomes = [SimpleNamespace(returncode=0)] * failed_index + [SimpleNamespace(returncode=1)]
                with patch.object(worker.subprocess, "run", side_effect=outcomes) as run:
                    with self.assertRaisesRegex(RuntimeError, "stopped at"):
                        worker.train(root, root, True)
                    self.assertEqual(run.call_count, failed_index + 1)
                self.assertFalse((root / "COMPLETED.json").exists())
