#!/usr/bin/env python3
"""Restore selected preserved records to a new directory; never install host files."""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

from platform_snapshot import opened_tar, safe_link, safe_name


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def download(asset, cache):
    path = cache / (asset['release_tag'] + '__' + safe_name(asset['name']))
    if path.exists():
        if path.stat().st_size != asset['bytes'] or digest(path) != asset['sha256']:
            raise ValueError(f'Cached asset does not match the index: {path.name}')
        return path
    part = path.with_name(path.name + '.part')
    try:
        with urllib.request.urlopen(asset['url'], timeout=60) as response, part.open('xb') as stream:
            shutil.copyfileobj(response, stream, 4 * 1024 * 1024)
        if part.stat().st_size != asset['bytes'] or digest(part) != asset['sha256']:
            raise ValueError(f'Downloaded asset does not match the index: {asset["name"]}')
        part.rename(path)
    finally:
        part.unlink(missing_ok=True)
    return path


def restore(index_path, destination, cache, prefixes=(), list_only=False):
    index = json.loads(index_path.read_text())
    cache.mkdir(parents=True, exist_ok=True)
    mapping = download(index['map'], cache)
    with gzip.open(mapping, 'rt', encoding='utf-8') as stream:
        rows = [json.loads(line) for line in stream]
    by_path = {row['path']: row for row in rows}
    assets = {(a['release_tag'], a['name']): a for a in index['assets']}
    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    dependencies = []
    selected = 0
    for row in rows:
        if prefixes and not any(row['path'].startswith(p) for p in prefixes):
            continue
        selected += 1
        target_name = safe_name(row['path'])
        canonical = row
        visited = set()
        while canonical['backup'] == 'delta-alias':
            key = canonical['canonical_path']
            if key in visited:
                raise ValueError('Cyclic preservation alias')
            visited.add(key)
            canonical = by_path[key]
        kind = canonical['backup']
        if kind == 'host-dependency':
            dependencies.append({'path': target_name, 'dependency': canonical['dependency']})
            continue
        if kind == 'delta':
            key = (index['release_tag'], canonical['asset'])
            member = canonical['archive_path']
            expected = canonical.get('published_sha256')
        elif kind in {'existing-release', 'existing-asset'}:
            prior = canonical['existing']
            key = (prior['release'], prior['asset'])
            member = None if kind == 'existing-asset' else prior['archive_path']
            expected = prior.get('published_sha256') or prior.get('sha256')
        else:
            raise ValueError(f'Unknown preservation reference: {kind}')
        if key not in assets:
            raise ValueError(f'Missing pinned asset: {key}')
        groups[key][member].append((target_name, expected))
    if not selected:
        raise ValueError('No preserved paths match the requested prefixes')
    if list_only:
        return {'selected_paths': selected, 'host_dependencies': dependencies,
                'assets': [assets[key]['name'] for key in groups],
                'download_bytes': sum(assets[key]['bytes'] for key in groups)}
    if not destination.is_absolute():
        raise ValueError('Destination must be absolute')
    destination.mkdir(parents=True, exist_ok=False)
    destination = destination.resolve()
    restored = 0

    def target(name):
        path = destination / safe_name(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.parent.resolve().is_relative_to(destination):
            raise ValueError('Restoration would escape its destination')
        if path.exists() or path.is_symlink():
            raise ValueError(f'Refusing to overwrite restored content: {name}')
        return path

    for key, wanted in groups.items():
        archive = download(assets[key], cache)
        remaining = set(wanted)
        if None in wanted:
            for name, expected in wanted[None]:
                path = target(name)
                shutil.copyfile(archive, path)
                if digest(path) != expected:
                    raise ValueError(f'Restored asset checksum mismatch: {name}')
                restored += 1
            remaining.remove(None)
        if remaining:
            with opened_tar(archive) as tar:
                for member in tar:
                    if member.name not in remaining:
                        continue
                    data = None
                    if member.isfile():
                        data = tar.extractfile(member).read()
                    elif not member.issym():
                        raise ValueError('Unsupported preserved member type')
                    for name, expected in wanted[member.name]:
                        path = target(name)
                        if member.issym():
                            link = safe_link(name, member.linkname)
                            path.symlink_to(link)
                        else:
                            if hashlib.sha256(data).hexdigest() != expected:
                                raise ValueError(f'Restored member checksum mismatch: {name}')
                            with path.open('xb') as stream:
                                stream.write(data)
                            path.chmod(member.mode & 0o777)
                        restored += 1
                    remaining.remove(member.name)
            if remaining:
                raise ValueError(f'Pinned archive is missing {len(remaining)} required members')
        print(f'{assets[key]["name"]}: restored and verified', flush=True)
    report = {'selected_paths': selected, 'restored_paths': restored,
              'host_dependencies': dependencies,
              'scope': 'Published copies; authentication is redacted. Host files are never installed.'}
    (destination / 'preservation-restore.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=Path(__file__).resolve().parents[1]
                        / 'setup/2026-10-26/local-preservation/recovery-index.json')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--prefix', action='append', default=[])
    parser.add_argument('--list', action='store_true', dest='list_only')
    args = parser.parse_args()
    if not args.list_only and args.destination is None:
        parser.error('--destination is required for restoration')
    print(json.dumps(restore(args.index, args.destination, args.cache, args.prefix,
                             args.list_only), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
