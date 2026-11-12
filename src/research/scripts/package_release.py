#!/usr/bin/env python3
"""Create a source-only review ZIP. Never publishes, initializes Git, or uploads."""
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {"README.md", ".gitignore", "requirements.txt", "pyproject.toml"}
DIRECTORIES = {"src", "tests", "scripts", "configs", "docs"}
ALLOWED_SUFFIXES = {".py", ".md", ".json", ".txt", ".toml"}
ARCHIVE_NAME = "ceres-research-v0.2.0-rc.1-source.zip"
RECEIPT_NAME = "source_package_receipt_v0_2_0_rc1.json"


def source_files():
    files = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if relative.parts[0] not in TOP | DIRECTORIES:
            continue
        if "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            raise RuntimeError(f"Refusing source symlink: {relative}")
        if path.is_file():
            if relative.name not in TOP and path.suffix not in ALLOWED_SUFFIXES:
                raise RuntimeError(f"Unexpected source-package file type: {relative}")
            files.append(path)
    return files


def remove_owned(path, identity):
    """Failure cleanup must never unlink a pre-existing or replaced path."""
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if not path.is_symlink() and (current.st_dev, current.st_ino) == identity:
        path.unlink()


def build_release():
    directory = ROOT / "review_evidence"
    destination = directory / ARCHIVE_NAME
    receipt_path = directory / RECEIPT_NAME
    # These checks make errors explicit. Exclusive creation below, rather than
    # a prior exists() check, is what prevents concurrent writers and symlinks.
    if directory.is_symlink():
        raise RuntimeError("Review output directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    for path in (destination, receipt_path):
        if path.is_symlink():
            raise RuntimeError(f"Output must not be a symlink: {path.name}")
        if path.exists():
            raise RuntimeError(f"Output already exists; refusing to overwrite: {path.name}")
    manifest = {str(path.relative_to(ROOT)): {
        "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
    } for path in source_files()}
    owned = {}
    try:
        with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as bundle:
            identity = os.fstat(bundle.fp.fileno())
            owned[destination] = (identity.st_dev, identity.st_ino)
            # Reserve both names before writing the package. An existing receipt
            # (including one created in a race) cannot be followed or overwritten.
            with receipt_path.open("x", encoding="utf-8") as receipt_stream:
                identity = os.fstat(receipt_stream.fileno())
                owned[receipt_path] = (identity.st_dev, identity.st_ino)
                for name in manifest:
                    bundle.write(ROOT / name, "ceres-research/" + name)
                bundle.writestr("ceres-research/SOURCE_PACKAGE_MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
                bundle.close()  # Finish the ZIP central directory before hashing.
                receipt = {"filename": destination.name, "version": "0.2.0rc1",
                           "bytes": destination.stat().st_size,
                           "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                           "source_files": len(manifest), "contains_raw_data": False,
                           "published": False, "repository_created": False}
                receipt_stream.write(json.dumps(receipt, indent=2) + "\n")
                receipt_stream.flush()
                os.fsync(receipt_stream.fileno())
        return receipt
    except BaseException:
        for path, identity in owned.items():
            remove_owned(path, identity)
        raise


def main():
    receipt = build_release()
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
