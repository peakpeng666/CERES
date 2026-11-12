"""Restore only SHA-256-pinned local ZIP members. No download or code execution."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile


class ArchiveError(ValueError):
    """Archive is missing, unsafe, or differs from the release allowlist."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") not in ("ceres-archive-allowlist-v1", "urp-archive-allowlist-v1"):
        raise ArchiveError("Unsupported archive manifest schema")
    return manifest


def safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or "\\" in name or path.is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))
            or ":" in path.parts[0]):
        raise ArchiveError(f"Unsafe archive member: {name!r}")
    return path


def check_catalog(bundle: zipfile.ZipFile) -> None:
    members = bundle.infolist()
    names = [member.filename for member in members]
    if len(names) != len(set(names)):
        raise ArchiveError("Duplicate ZIP member names are not allowed")
    for member in members:
        safe_relative(member.filename.rstrip("/"))
        if stat.S_ISLNK(member.external_attr >> 16):
            raise ArchiveError("ZIP symlinks are not allowed")


def open_pinned_zip(local_zip: Path, spec: dict) -> zipfile.ZipFile:
    identity = (local_zip.stat().st_size, sha256(local_zip))
    if identity == (spec["bytes"], spec["sha256"]):
        return zipfile.ZipFile(local_zip)
    container = spec.get("container")
    if container and identity == (container["bytes"], container["sha256"]):
        with zipfile.ZipFile(local_zip) as outer:
            check_catalog(outer)
            safe_relative(container["member"])
            try:
                info = outer.getinfo(container["member"])
            except KeyError as error:
                raise ArchiveError("Pinned container is missing the experiment ZIP") from error
            if info.file_size != spec["bytes"]:
                raise ArchiveError("Nested ZIP size mismatch")
            nested = outer.read(info)
            if hashlib.sha256(nested).hexdigest() != spec["sha256"]:
                raise ArchiveError("Nested ZIP hash mismatch")
        return zipfile.ZipFile(io.BytesIO(nested))
    raise ArchiveError("ZIP identity mismatch; use the exact pinned Drive archive")


def verify_bundle(root: Path, spec: dict) -> dict:
    if root.is_symlink():
        raise ArchiveError("Archive root must not be a symlink")
    root = root.resolve()
    if not root.is_dir():
        raise ArchiveError("Archive data is unavailable. Follow docs/DATA_RESTORE.md first.")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ArchiveError(f"Symlink is not allowed: {path.relative_to(root)}")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ArchiveError(f"Special files are not allowed: {path.relative_to(root)}")
    expected = set(spec["files"])
    if actual != expected:
        raise ArchiveError(f"Archive member set differs from allowlist: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    for name, metadata in spec["files"].items():
        rel = safe_relative(name)
        path = root.joinpath(*rel.parts)
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root.parent):
            raise ArchiveError(f"Symlink is not allowed: {name}")
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ArchiveError(f"Required archive file is missing: {name}")
        if path.stat().st_size != metadata["bytes"] or sha256(path) != metadata["sha256"]:
            raise ArchiveError(f"Archive file failed SHA-256 verification: {name}")
    return {"verified": True, "files": len(spec["files"]), "archive_sha256": spec["sha256"]}


def restore(local_zip: Path, destination: Path, spec: dict) -> dict:
    """Atomically restore an allowlisted bundle into a new destination directory."""
    if destination.exists() or destination.is_symlink():
        raise ArchiveError("Destination already exists; restoration never overwrites data")
    if not local_zip.is_file():
        raise ArchiveError("Expected a local downloaded ZIP file, not a URL")
    with open_pinned_zip(local_zip, spec) as bundle:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".ceres-restore-", dir=destination.parent))
        try:
            check_catalog(bundle)
            for relative, metadata in spec["files"].items():
                rel = safe_relative(relative)
                member_name = f"{spec['zip_prefix']}/{relative}"
                try:
                    info = bundle.getinfo(member_name)
                except KeyError as error:
                    raise ArchiveError(f"Missing allowlisted ZIP member: {relative}") from error
                if info.file_size != metadata["bytes"]:
                    raise ArchiveError(f"ZIP member size mismatch: {relative}")
                data = bundle.read(info)
                if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
                    raise ArchiveError(f"ZIP member hash mismatch: {relative}")
                target = temporary.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            result = verify_bundle(temporary, spec)
            # Claim the final name atomically. Concurrent restores cannot merge or
            # replace it. A failed process may leave an empty directory to inspect.
            destination.mkdir(exist_ok=False)
            os.rename(temporary, destination)
            return result
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
