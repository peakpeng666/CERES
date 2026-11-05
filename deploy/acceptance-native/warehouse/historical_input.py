"""Bounded, hash-pinned data-only recovery. Never extract executable archive files."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

ARCHIVE_SHA256 = 'fc1793db9a74ed08926d8cab69688453f546ac8a264e3846d9ea2d641868f84c'
ARCHIVE_SIZE = 46998880
HISTORICAL_SOURCE = 'ddb5113e641ba63846431d4e3114efaf85f2fc89'
SOURCE = '3acf840d1a0c16ab1cfcca15744e4ea6609ddb35'
SELECTED = {
 'evidence/container-tb4-warehouse-headless/export.json': (56157, '189b9ff0e7aaa7ceb2086ecfbe92d8b0e9f4a878b69495d2ac34321ef05c2be8'),
 'evidence/container-tb4-warehouse-headless/sim-follow_path.json': (274286, 'c5ba7189b700a6712c8771ef72c0b81827f1818a9fae7c52cf091708040d3b91'),
 'evidence/native-tb4-warehouse/export.json': (56164, 'e225f76c64b54235367f24b90ed85d8ffdaa1bf22edf7bf64aa362241574898d'),
 'evidence/native-tb4-warehouse/sim-follow_path.json': (1003114, '82574ca207ca336a358149e88be4227c021e8a31ed2354d533736a4a07772cfe'),
 'evidence/native-tb4-session/world-assets.json': (13391, '096dec32f302fff613f12b6816757b665818bb4503946cf9a63c26ea62bda63a'),
}

def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def checked_json(name, data):
    size, digest = SELECTED[name]
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('historical_member_identity_mismatch')
    return json.loads(data.decode('utf-8'))

def from_directory(root):
    root = Path(root).resolve()
    result = {}
    for name in SELECTED:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root): raise ValueError('historical_path_alias')
        result[name] = checked_json(name, path.read_bytes())
    return result

def from_archive(archive):
    archive = Path(archive)
    if archive.stat().st_size != ARCHIVE_SIZE: raise ValueError('archive_size_mismatch')
    with archive.open('rb') as f: digest = hashlib.file_digest(f, 'sha256').hexdigest()
    if digest != ARCHIVE_SHA256: raise ValueError('archive_identity_mismatch')
    proc = subprocess.Popen(['zstd', '-dc', '--', str(archive)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selected = {}; count = total = 0; seen = set()
    try:
        with tarfile.open(fileobj=proc.stdout, mode='r|') as tar:
            for member in tar:
                count += 1; total += member.size
                path = PurePosixPath(member.name)
                if member.name in seen: raise ValueError('archive_duplicate_member')
                seen.add(member.name)
                if (path.is_absolute() or '..' in path.parts or member.issym() or member.islnk()
                        or not (member.isdir() or member.isfile()) or count > 10000 or total > 2_000_000_000):
                    raise ValueError('archive_member_rejected')
                if member.name in SELECTED:
                    if member.name in selected or member.size != SELECTED[member.name][0]: raise ValueError('archive_member_rejected')
                    selected[member.name] = checked_json(member.name, tar.extractfile(member).read())
        if proc.wait(timeout=30) != 0 or set(selected) != set(SELECTED): raise ValueError('archive_incomplete')
        return selected
    finally:
        if proc.stdout: proc.stdout.close()
        if proc.poll() is None: proc.kill(); proc.wait()
