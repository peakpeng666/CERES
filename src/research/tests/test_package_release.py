"""The source packager must never overwrite a ZIP or receipt, even in a race."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/package_release.py"
SPEC = importlib.util.spec_from_file_location("release_packager_under_test", SCRIPT)
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


class PackageReleaseContract(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "README.md").write_text("Synthetic source-only test package.\n")
        self.directory = self.root / "review_evidence"
        self.directory.mkdir()
        self.archive = self.directory / packager.ARCHIVE_NAME
        self.receipt = self.directory / packager.RECEIPT_NAME
        patched = patch.object(packager, "ROOT", self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def test_existing_zip_preserved(self):
        self.archive.write_bytes(b"keep archive")
        with self.assertRaises(RuntimeError):
            packager.build_release()
        self.assertEqual(self.archive.read_bytes(), b"keep archive")
        self.assertFalse(self.receipt.exists())

    def test_dangling_zip_symlink_rejected(self):
        target = self.root / "untouched.zip"
        self.archive.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            packager.build_release()
        self.assertTrue(self.archive.is_symlink())
        self.assertFalse(target.exists())

    def test_existing_receipt_preserved(self):
        self.receipt.write_text("keep receipt")
        with self.assertRaises(RuntimeError):
            packager.build_release()
        self.assertEqual(self.receipt.read_text(), "keep receipt")
        self.assertFalse(self.archive.exists())

    def test_receipt_symlink_existing_target_preserved(self):
        target = self.root / "marker.txt"
        target.write_text("keep marker")
        self.receipt.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            packager.build_release()
        self.assertEqual(target.read_text(), "keep marker")
        self.assertFalse(self.archive.exists())

    def test_dangling_receipt_symlink_rejected(self):
        target = self.root / "missing.json"
        self.receipt.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            packager.build_release()
        self.assertFalse(target.exists())
        self.assertFalse(self.archive.exists())

    def test_output_directory_symlink_rejected(self):
        self.directory.rmdir()
        target = self.root / "outside"
        target.mkdir()
        self.directory.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            packager.build_release()
        self.assertEqual(list(target.iterdir()), [])

    def test_two_competing_builds_only_one_succeeds(self):
        original = zipfile.ZipFile
        barrier = threading.Barrier(2)

        def synchronized_open(*args, **kwargs):
            barrier.wait(timeout=10)
            return original(*args, **kwargs)

        def attempt():
            try:
                packager.build_release()
                return "success"
            except FileExistsError:
                return "already_exists"

        with patch.object(packager.zipfile, "ZipFile", synchronized_open):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(outcomes, ["success", "already_exists"])
        with original(self.archive) as bundle:
            self.assertIsNone(bundle.testzip())
        self.assertEqual(json.loads(self.receipt.read_text())["sha256"],
                         hashlib.sha256(self.archive.read_bytes()).hexdigest())

    def test_receipt_race_preserves_other_file_and_cleans_own_zip(self):
        original = Path.open
        receipt = self.receipt

        def competing_open(path, mode="r", *args, **kwargs):
            if path == receipt and mode == "x":
                with original(path, "w") as other:
                    other.write("another writer owns this receipt")
            return original(path, mode, *args, **kwargs)

        with patch.object(Path, "open", competing_open):
            with self.assertRaises(FileExistsError):
                packager.build_release()
        self.assertFalse(self.archive.exists())
        self.assertEqual(self.receipt.read_text(), "another writer owns this receipt")

    def test_failed_write_cleans_only_owned_artifacts(self):
        with patch.object(zipfile.ZipFile, "write", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(OSError):
                packager.build_release()
        self.assertFalse(self.archive.exists())
        self.assertFalse(self.receipt.exists())

    def test_success_receipt_matches_archive(self):
        result = packager.build_release()
        self.assertEqual(result, json.loads(self.receipt.read_text()))
        self.assertEqual(result["sha256"], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        with zipfile.ZipFile(self.archive) as bundle:
            self.assertIsNone(bundle.testzip())
            self.assertEqual(bundle.read("ceres-research/README.md"), (self.root / "README.md").read_bytes())
