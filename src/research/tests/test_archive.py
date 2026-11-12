import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from ceres_research.archive import ArchiveError, load_manifest, restore, safe_relative, verify_bundle


class ArchiveContract(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def archive(self, extra=None):
        path = self.root / "input.zip"
        data = b'{"synthetic": true}\n'
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("bundle/data/example.json", data)
            if extra:
                bundle.writestr(*extra)
        spec = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "zip_prefix": "bundle", "files": {"data/example.json": {
                    "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}}
        return path, spec

    def test_manifest_schema_names_accept_both_generations(self):
        path = self.root / "manifest.json"
        for schema in ("urp-archive-allowlist-v1", "ceres-archive-allowlist-v1"):
            with self.subTest(schema=schema):
                path.write_text(json.dumps({"schema": schema}))
                self.assertEqual(load_manifest(path)["schema"], schema)
        path.write_text(json.dumps({"schema": "unknown-archive-v1"}))
        with self.assertRaises(ArchiveError):
            load_manifest(path)

    def test_restore_only_allowlisted_members(self):
        path, spec = self.archive(("bundle/ignored.py", "raise RuntimeError('do not execute')"))
        destination = self.root / "restored"
        self.assertTrue(restore(path, destination, spec)["verified"])
        self.assertFalse((destination / "ignored.py").exists())
        self.assertEqual(len(list(destination.rglob("*.json"))), 1)

    def test_hash_mismatch_fails_without_partial_output(self):
        path, spec = self.archive()
        spec["sha256"] = "0" * 64
        with self.assertRaises(ArchiveError):
            restore(path, self.root / "restored", spec)
        self.assertFalse((self.root / "restored").exists())

    def test_existing_destination_is_preserved(self):
        path, spec = self.archive()
        destination = self.root / "restored"
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(ArchiveError):
            restore(path, destination, spec)
        self.assertEqual(marker.read_text(), "keep")

    def test_traversal_member_is_rejected(self):
        path, spec = self.archive(("../outside.json", "bad"))
        with self.assertRaises(ArchiveError):
            restore(path, self.root / "restored", spec)
        self.assertFalse((self.root / "outside.json").exists())

    def test_member_hash_mismatch_cleans_temporary(self):
        path, spec = self.archive()
        spec["files"]["data/example.json"]["sha256"] = "0" * 64
        with self.assertRaises(ArchiveError):
            restore(path, self.root / "restored", spec)
        self.assertEqual(list(self.root.glob(".ceres-restore-*")), [])

    def test_zip_symlink_rejected(self):
        member = zipfile.ZipInfo("bundle/link")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        path, spec = self.archive((member, "../../outside"))
        with self.assertRaises(ArchiveError):
            restore(path, self.root / "restored", spec)

    def test_missing_or_changed_restored_file_rejected(self):
        path, spec = self.archive()
        destination = self.root / "restored"
        restore(path, destination, spec)
        (destination / "data/example.json").write_text("changed")
        with self.assertRaises(ArchiveError):
            verify_bundle(destination, spec)

    def test_unpinned_extra_file_rejected(self):
        path, spec = self.archive()
        destination = self.root / "restored"
        restore(path, destination, spec)
        (destination / "data/unpinned.json").write_text('{"slot_id": "override"}')
        with self.assertRaisesRegex(ArchiveError, "extra="):
            verify_bundle(destination, spec)

    def test_unpinned_symlink_rejected(self):
        path, spec = self.archive()
        destination = self.root / "restored"
        restore(path, destination, spec)
        (destination / "extra").symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ArchiveError, "Symlink"):
            verify_bundle(destination, spec)

    def test_unsafe_paths_rejected(self):
        for name in ("", "../file", "/file", "folder/../file", "C:/file", "a\\b", "a//b", "./file"):
            with self.subTest(name=name), self.assertRaises(ArchiveError):
                safe_relative(name)

    def test_pinned_nested_archive_is_supported(self):
        inner, spec = self.archive()
        outer = self.root / "combined.zip"
        with zipfile.ZipFile(outer, "w") as bundle:
            bundle.write(inner, "selected/input.zip")
            bundle.writestr("excluded/unrelated.txt", "not restored")
        spec["container"] = {"bytes": outer.stat().st_size,
                             "sha256": hashlib.sha256(outer.read_bytes()).hexdigest(),
                             "member": "selected/input.zip"}
        destination = self.root / "restored"
        self.assertTrue(restore(outer, destination, spec)["verified"])
        self.assertFalse((destination / "excluded").exists())

    def test_nested_hash_mismatch_rejected(self):
        inner, spec = self.archive()
        outer = self.root / "combined.zip"
        with zipfile.ZipFile(outer, "w") as bundle:
            bundle.write(inner, "selected/input.zip")
        spec["container"] = {"bytes": outer.stat().st_size,
                             "sha256": hashlib.sha256(outer.read_bytes()).hexdigest(),
                             "member": "selected/input.zip"}
        spec["sha256"] = "0" * 64
        with self.assertRaises(ArchiveError):
            restore(outer, self.root / "restored", spec)


if __name__ == "__main__":
    unittest.main()
