#!/usr/bin/env python3
"""Inventory CERES and optionally clear only reproducible backend Python caches.

Default is read-only. Never follows symlinks, opens credentials, prunes Git,
deletes environments, or removes research inputs / outputs / runtime sessions.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {'.aws', '.agents', '.codex', '.v2c', '.video_agent'}
DEPENDENCIES = {'.git', '.venv', 'venv', 'node_modules'}
CACHES = {'__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache'}
BACKENDS = ('src/lab/ceres_lab', 'src/lab/ros', 'src/lab/tests', 'src/lab/e2e',
            'src/backend/app', 'src/backend/tests', 'tools')


def audit(clean=False):
    tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'))
    roots, links, caches, skipped = {}, [], [], []
    for base, dirs, files in os.walk(ROOT, followlinks=False):
        parent = Path(base)
        for name in dirs[:]:
            path = parent / name
            rel = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                links.append({'path': rel, 'target': os.readlink(path), 'exists': path.exists()})
                dirs.remove(name)
            elif name in PRIVATE | DEPENDENCIES:
                skipped.append(rel)
                dirs.remove(name)
            elif name in CACHES:
                paths = [p for p in path.rglob('*') if p.is_file() and not p.is_symlink()]
                safe = (any(rel.startswith(prefix + '/') for prefix in BACKENDS) or
                        rel in ('src/lab/.pytest_cache', 'src/backend/.pytest_cache'))
                # Include tracked symlinks and empty placeholders in the guard,
                # even though only regular files contribute to byte counts.
                safe = safe and not any(p == rel or p.startswith(rel + '/') for p in tracked)
                row = {'path': rel, 'files': len(paths), 'bytes': sum(p.stat().st_size for p in paths),
                       'eligible': safe, 'removed': False}
                if clean and safe:
                    shutil.rmtree(path)
                    row['removed'] = True
                caches.append(row)
                dirs.remove(name)
        for name in files:
            path = parent / name
            rel = path.relative_to(ROOT)
            if path.is_symlink():
                links.append({'path': rel.as_posix(), 'target': os.readlink(path), 'exists': path.exists()})
                continue
            row = roots.setdefault(rel.parts[0], {'files': 0, 'bytes': 0, 'tracked_files': 0})
            try:
                size = path.stat().st_size
            except FileNotFoundError:  # running services may replace a temporary file
                continue
            row['files'] += 1
            row['bytes'] += size
            row['tracked_files'] += rel.as_posix() in tracked
    return {'utc': datetime.now(timezone.utc).isoformat(), 'scope': 'metadata only, symlinks not traversed',
            'roots': roots, 'links': links, 'broken_links': [r for r in links if not r['exists']],
            'caches': caches, 'skipped': skipped,
            'removed_bytes': sum(r['bytes'] for r in caches if r['removed'])}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--clean-cache', action='store_true', help='Remove only allow-listed backend Python caches')
    p.add_argument('--output', type=Path, help='Save full inventory here')
    args = p.parse_args()
    result = audit(args.clean_cache)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps({k: result[k] for k in ('utc', 'broken_links', 'removed_bytes')}, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
