"""Fingerprint optional planners without importing their native Python bindings.

Package versions alone do not identify rebuilt wheels. Inspect the import target,
Python wrappers, native artifacts (including wheel-bundled shared libraries), and
distribution metadata on every check. Cache bytes only while the file's full stat
identity is unchanged; this is drift detection, not a hostile-filesystem sandbox.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import os
from functools import lru_cache
from pathlib import Path

from . import paths


def _stat_identity(stat: os.stat_result) -> tuple:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=512)
def _hash_version(filename: str, identity: tuple) -> str:
    """Stream large libraries once per observed version, not once per job check."""
    with open(filename, 'rb') as stream:
        if _stat_identity(os.fstat(stream.fileno())) != identity:
            raise OSError(f'执行依赖在核对时改变：{filename}')
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if _stat_identity(os.fstat(stream.fileno())) != identity:
            raise OSError(f'执行依赖在核对时改变：{filename}')
    return digest


def _fingerprint(path: Path) -> str | None:
    try:
        resolved = path.resolve(strict=True)
        identity = _stat_identity(resolved.stat())
    except FileNotFoundError:
        return None
    digest = _hash_version(str(resolved), identity)
    # Also reject replacement/retargeting while hashing, including a cache hit.
    if path.resolve(strict=True) != resolved or _stat_identity(resolved.stat()) != identity:
        raise OSError(f'执行依赖在核对时改变：{path}')
    return digest


def _is_native(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(('.so', '.pyd', '.dll', '.dylib')) or '.so.' in name


def _package_files(root: Path) -> set[Path]:
    # os.walk with onerror avoids silently accepting a partially readable tree.
    def failed(exc):
        raise exc

    files, visited = set(), set()
    for directory, directories, names in os.walk(root, onerror=failed, followlinks=True):
        stat = Path(directory).stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity in visited:
            directories[:] = []
            continue
        visited.add(identity)
        directories.sort()
        for name in names:
            path = Path(directory) / name
            if path.suffix == '.py' or _is_native(path):
                files.add(path)
    return files


def native_planners_manifest() -> dict:
    out = {}
    for name in ('ompl', 'fields2cover'):
        # Only top-level find_spec: dotted lookups can execute parent __init__.py
        # and loading optional native bindings in the gateway can be unsafe.
        spec = importlib.util.find_spec(name)
        origin = Path(spec.origin).absolute() if spec and spec.origin else None
        roots = [Path(p).absolute() for p in (spec.submodule_search_locations or ())] if spec else []
        files = {origin} if origin else set()
        for root in roots:
            files.update(_package_files(root))
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            distribution = None
        if distribution is not None:
            # RECORD locates native dependencies outside the import package too,
            # such as ompl.libs/libboost*.so. Missing listed files stay explicit.
            for entry in distribution.files or ():
                entry = Path(entry)
                if _is_native(entry) or entry.name in ('METADATA', 'RECORD', 'WHEEL'):
                    files.add(Path(distribution.locate_file(entry)).absolute())
        acceptance_manifest = None
        checked_binary = None
        if name == 'fields2cover':
            acceptance_manifest = paths.LAB / 'vendor' / 'fields2cover-manifest.json'
            files.add(acceptance_manifest)
            if origin:
                # Match f2c_coverage.available's accepted binary, even when absent.
                checked_binary = origin.parent / '_fields2cover_python.so'
                files.add(checked_binary)
        fingerprints = {paths.rel(p): _fingerprint(p) for p in sorted(files)}
        has_native = any(_is_native(p) and fingerprints[paths.rel(p)] is not None for p in files)
        state = ('not_installed' if spec is None and distribution is None else
                 'package_missing' if spec is None else
                 'native_missing' if not has_native else 'present')
        # "present" deliberately does not claim importability or F2C acceptance;
        # those checks remain the planner adapter's responsibility.
        out[name] = {'state': state, 'origin': str(origin) if origin else None,
                     'package_roots': sorted(str(p) for p in roots),
                     'files': fingerprints}
        if name == 'fields2cover':
            out[name].update(acceptance_manifest=paths.rel(acceptance_manifest),
                             checked_binary=paths.rel(checked_binary) if checked_binary else None)
    return out
