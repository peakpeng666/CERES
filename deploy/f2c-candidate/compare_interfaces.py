#!/usr/bin/env python3
"""Compare official OR-Tools archive headers/interfaces without extracting binaries."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

def inspect(path, expected):
    if path.is_symlink():
        raise ValueError('Archive may not be a symbolic link')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != expected:
        raise ValueError('Archive SHA256 mismatch')
    files = {}
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if not parts or member.name.startswith('/') or '..' in parts:
                raise ValueError('Unsafe archive member')
            name = '/'.join(parts[1:])
            selected = name.startswith('include/') or (
                name.startswith('lib/cmake/') and name.endswith('.cmake'))
            if not selected or member.isdir():
                continue
            if not member.isfile() or name in files or member.size > 32 * 1024 * 1024:
                raise ValueError('Unexpected interface archive member')
            files[name] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    if not any(n.startswith('include/ortools/') for n in files):
        raise ValueError('Missing OR-Tools headers')
    return files

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--reference-sha256', required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--candidate-sha256', required=True)
    args = parser.parse_args()
    a = inspect(args.reference, args.reference_sha256)
    b = inspect(args.candidate, args.candidate_sha256)
    changed = sorted(n for n in a.keys() & b.keys() if a[n] != b[n])
    missing = sorted(a.keys() - b.keys())
    added = sorted(b.keys() - a.keys())
    print(json.dumps({'identical_count': sum(a[n] == b[n] for n in a.keys() & b.keys()),
                      'changed': changed, 'missing': missing, 'added': added,
                      'abi_compatibility_proven': False}, indent=2))
    return int(bool(changed or missing or added))

if __name__ == '__main__':
    raise SystemExit(main())
