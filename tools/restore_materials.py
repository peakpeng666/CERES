#!/usr/bin/env python3
"""Restore untracked materials from the historical complete CERES.zip, after verification.

This is separate from the platform Release restore and the Drive copy registry.
The default manifest preserves the fingerprint recorded in the 2026-10-13 history;
it does not assert that an archive is currently available or restored.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ('data', 'papers', 'experiments', 'literature/教材', 'src/repos',
            'src/backend/backend_data')
SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'dist'}


def archive_fingerprint(manifest):
    document = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or document.get('schema') != 'urp.materials-archive/1':
        raise ValueError('Expected an urp.materials-archive/1 historical ZIP manifest')
    archive = document.get('archive')
    if (not isinstance(archive, dict) or type(archive.get('bytes')) is not int
            or archive['bytes'] <= 0 or not isinstance(archive.get('sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', archive['sha256'])):
        raise ValueError('Archive manifest requires a positive byte size and a lowercase SHA256')
    return archive


def material_destination(relative):
    """Reject existing links and non-directory parents before any file is opened."""
    destination = ROOT
    for index, part in enumerate(relative.parts):
        destination /= part
        if destination.is_symlink():
            raise SystemExit(f'Symbolic link in material destination: {relative}')
        if index < len(relative.parts) - 1 and destination.exists() and not destination.is_dir():
            raise SystemExit(f'Local parent is not a directory: {relative}')
    if ROOT.resolve() not in destination.resolve().parents:
        raise SystemExit('Destination escapes the project')
    return destination


def same_material(archive, member, destination):
    if not destination.is_file() or destination.stat().st_size != member.file_size:
        return False
    with archive.open(member) as original, destination.open('rb') as local:
        while True:
            block = original.read(1024 * 1024)
            if block != local.read(1024 * 1024):
                return False
            if not block:
                return True


def plan_materials(archive, includes, tracked):
    plan, seen, file_paths = [], set(), set()
    root_seen = False
    matched = existing = tracked_count = 0
    for member in archive.infolist():
        original = PurePosixPath(member.filename)
        if '..' in original.parts or original.is_absolute() or '\\' in member.filename:
            raise SystemExit('Unsafe archive member')
        if not original.parts or original.parts[0] != 'CERES':
            continue
        if len(original.parts) == 1:
            mode = member.external_attr >> 16
            if (stat.S_IFMT(mode) not in (0, stat.S_IFDIR)
                    or not (member.is_dir() or stat.S_ISDIR(mode))):
                raise SystemExit('CERES archive root is not a directory')
            root_seen = True
            continue
        root_seen = True
        relative = PurePosixPath(*original.parts[1:])
        name = str(relative)
        if SKIP.intersection(relative.parts) or not any(
                name == prefix or name.startswith(prefix + '/') for prefix in includes):
            continue
        if name in seen:
            raise SystemExit(f'Duplicate material target: {relative}')
        seen.add(name)
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise SystemExit(f'Symbolic link archive material: {relative}')
        if member.is_dir() or stat.S_ISDIR(mode):
            continue
        if name in PREFIXES:
            raise SystemExit(f'Material root must be a directory: {relative}')
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
            raise SystemExit(f'Unsupported archive material type: {relative}')
        file_paths.add(name)
        matched += 1
        if name in tracked:
            tracked_count += 1
            continue
        destination = material_destination(relative)
        if destination.exists():
            if not same_material(archive, member, destination):
                raise SystemExit(f'Local material differs from the archive: {relative}')
            existing += 1
        else:
            plan.append((member, relative))
    if not root_seen:
        raise SystemExit('Archive has no CERES/ root directory')
    for name in seen:
        if any(str(parent) in file_paths for parent in PurePosixPath(name).parents):
            raise SystemExit(f'Archive file conflicts with a child path: {name}')
    if not matched:
        raise SystemExit('No material files match the requested include paths')
    summary = {'files': len(plan), 'bytes': sum(member.file_size for member, _ in plan),
               'archive_sha256': 'PASS', 'matched_files': matched,
               'existing_files': existing, 'tracked_files_skipped': tracked_count,
               'status': 'planned' if plan else 'already-present' if existing else 'tracked-only'}
    return plan, summary


def restore_plan(archive, plan, summary):
    if shutil.disk_usage(ROOT).free < summary['bytes']:
        raise SystemExit('Insufficient free space')
    completed = 0
    for member, relative in plan:
        created = False
        try:
            destination = material_destination(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination = material_destination(relative)
            with archive.open(member) as reader, destination.open('xb') as writer:
                created = True
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                mode = (member.external_attr >> 16) & 0o777
                if mode:
                    if hasattr(os, 'fchmod'):
                        os.fchmod(writer.fileno(), mode)
                    else:
                        destination.chmod(mode)
            completed += 1
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            residual = f'new partial file may remain: {relative}' if created else 'no file was created for this target'
            raise SystemExit(f'Restore failed after {completed} completed files; {residual}; '
                             f'{type(exc).__name__}: {exc}. Existing files were not replaced.') from exc
    print(json.dumps({**summary, 'status': 'restored'}, ensure_ascii=False))
    print('Materials restored')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'setup/materials-archive.json',
                        help='Historical ZIP fingerprint; defaults to setup/materials-archive.json')
    parser.add_argument('--include', nargs='+', default=PREFIXES)
    parser.add_argument('--list', action='store_true', help='Inspect without extracting')
    args = parser.parse_args()
    includes = []
    for prefix in args.include:
        path = PurePosixPath(prefix)
        if path.is_absolute() or '..' in path.parts or not any(
                prefix == allowed or prefix.startswith(allowed + '/') for allowed in PREFIXES):
            parser.error('Include paths must be inside the research material directories')
        includes.append(str(path))
    try:
        expected = archive_fingerprint(args.manifest)
    except (OSError, ValueError) as exc:
        parser.error(f'Cannot load historical archive manifest {args.manifest}: {exc}. '
                     'For the platform Release, use tools/platform_snapshot.py; '
                     'storage.json is a location registry, not a ZIP fingerprint.')
    try:
        source = args.archive.open('rb')
    except OSError as exc:
        parser.error(f'Cannot read historical archive {args.archive}: {exc}')
    # Hold one open file across fingerprinting and ZIP reads; a renamed replacement
    # at args.archive must not become a second, unverified source.
    with source:
        if os.fstat(source.fileno()).st_size != expected['bytes']:
            raise SystemExit('Archive size differs from the historical manifest')
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(block)
        if digest.hexdigest() != expected['sha256']:
            raise SystemExit('Archive SHA-256 differs from the historical manifest')
        source.seek(0)
        tracked = set(subprocess.check_output(
            ['git', '-C', str(ROOT), 'ls-files', '-z']).decode().split('\0'))
        try:
            with zipfile.ZipFile(source) as archive:
                plan, summary = plan_materials(archive, includes, tracked)
                if args.list or not plan:
                    print(json.dumps(summary, ensure_ascii=False))
                    if not args.list:
                        print('Materials already present; no files written' if summary['existing_files']
                              else 'Only Git-managed materials matched; no files written')
                    return
                restore_plan(archive, plan, summary)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise SystemExit(f'Historical ZIP preflight failed: {type(exc).__name__}: {exc}') from exc


if __name__ == '__main__':
    main()
