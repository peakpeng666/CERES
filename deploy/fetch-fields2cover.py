#!/usr/bin/env python3
"""Restore only the pinned F2C wheel, never a historical environment or binary tree.

Requires Python 3.10+ and zstd. The archive and wheel each have a repository-pinned
SHA256. This is binary recovery, not a claim of reproducible upstream C++ builds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ASSET = 'compiled-and-wheels.tar.zst'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, digest: str, size: int | None = None) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Expected a regular file, not a symlink: {path}')
    if size is not None and path.stat().st_size != size:
        raise ValueError(f'Archive size mismatch: {path}')
    if sha256(path) != digest:
        raise ValueError(f'SHA256 mismatch; refusing to use or overwrite: {path}')


def manifests(root: Path):
    wheel = json.loads((root / 'src/lab/vendor/fields2cover-manifest.json').read_text())
    snapshot = json.loads((root / 'setup/platform-snapshot.json').read_text())
    relative = PurePosixPath(wheel['wheel'])
    if relative.is_absolute() or '..' in relative.parts or relative.parts[:2] != ('vendor', 'wheels'):
        raise ValueError('Unsafe wheel path in manifest')
    if snapshot['repository'] != 'peakpeng666/CERES':
        raise ValueError('Unexpected snapshot repository')
    asset = next(a for a in snapshot['assets'] if a['name'] == ASSET)
    return wheel, snapshot, asset, relative


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme != 'https':
            raise ValueError('Refusing a non-HTTPS download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, destination: Path, size: int, digest: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.download-', dir=destination.parent)
    partial = Path(temporary)
    try:
        opener = urllib.request.build_opener(HTTPSRedirect())
        with os.fdopen(fd, 'wb') as out, opener.open(url, timeout=120) as source:
            count = 0
            while block := source.read(1024 * 1024):
                count += len(block)
                if count > size:
                    raise ValueError('Download exceeds pinned archive size')
                out.write(block)
        verify(partial, digest, size)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def extract_wheel(archive: Path, member_name: str, destination: Path, digest: str) -> None:
    """Read one exact regular member through a pipe; never use extract/extractall."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.wheel-', dir=destination.parent)
    partial = Path(temporary)
    os.close(fd)
    process = subprocess.Popen(['zstd', '-dc', '--', str(archive)], stdout=subprocess.PIPE)
    found = False
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as tar:
            for member in tar:
                if member.name != member_name:
                    continue
                if found or not member.isfile() or member.size > 256 * 1024 * 1024:
                    raise ValueError('Duplicate, non-regular or oversized wheel archive member')
                found = True
                with tar.extractfile(member) as source, partial.open('wb') as out:
                    shutil.copyfileobj(source, out)
        # Drain tar padding before waiting, avoiding a blocked zstd writer.
        with open(os.devnull, 'wb') as discard:
            shutil.copyfileobj(process.stdout, discard)
        if process.wait() != 0:
            raise ValueError('zstd decompression failed')
        if not found:
            raise ValueError(f'Wheel not present in verified archive: {member_name}')
        verify(partial, digest)
        os.chmod(partial, 0o644)
        os.replace(partial, destination)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        process.wait()
        partial.unlink(missing_ok=True)


def obtain(root: Path, archive: Path | None = None, check_only: bool = False) -> Path:
    wheel, snapshot, asset, relative = manifests(root)
    target = root / 'src/lab' / relative
    if not target.resolve().is_relative_to((root / 'src/lab').resolve()):
        raise ValueError('Wheel destination escapes src/lab through a symlink')
    if target.exists() or target.is_symlink():
        verify(target, wheel['wheel_sha256'])
        return target
    if check_only:
        raise ValueError(f'Optional wheel is absent: {target}')
    if not shutil.which('zstd'):
        raise ValueError('zstd is required to read the verified Release archive')
    if archive is None:
        archive = root / '.local-state/deploy/downloads' / snapshot['release_tag'] / ASSET
        if not archive.exists():
            tag = urllib.parse.quote(snapshot['release_tag'], safe='')
            url = f'https://github.com/peakpeng666/URP/releases/download/{tag}/{ASSET}'
            print(f'Downloading pinned binary archive ({asset["bytes"]} bytes): {url}')
            download(url, archive, asset['bytes'], asset['sha256'])
    verify(archive, asset['sha256'], asset['bytes'])
    extract_wheel(archive, f'project/src/lab/{relative}', target, wheel['wheel_sha256'])
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Use this already-downloaded archive; never download')
    parser.add_argument('--check', action='store_true', help='Check the local wheel only; do not download or install')
    args = parser.parse_args()
    try:
        wheel = obtain(ROOT, args.archive, args.check)
    except (OSError, ValueError, KeyError, StopIteration, tarfile.TarError) as exc:
        print(f'Fields2Cover unavailable: {exc}', file=sys.stderr)
        return 1
    print(f'Fields2Cover wheel SHA256 verified: {wheel}')
    print('Only the wheel was restored; original C++ build provenance remains incomplete.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
