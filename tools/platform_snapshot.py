#!/usr/bin/env python3
"""Create, verify and safely unpack component snapshots without altering live data.

Archives are Release assets, not Git blobs. Restore always uses a new directory;
it never installs packages or overwrites the current workspace or /opt/ros.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import sqlite3
import subprocess
import tarfile
import tempfile


REDACTED = b"[REDACTED-AUTH]"
PATTERNS = [
    (re.compile(rb'(?i)(<authorization_key>)[^<]*(</authorization_key>)'), rb'\1' + REDACTED + rb'\2'),
    (re.compile(rb'''(?i)(["'](?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization_key)["']\s*:\s*["'])[^"'\r\n]+(["'])'''), rb'\1' + REDACTED + rb'\2'),
    (re.compile(rb'("key"\s*:\s*")[0-9a-fA-F]{32}(")'), rb'\1' + REDACTED + rb'\2'),
    (re.compile(rb'(?i)(\b(?:password|passwd|access_token|refresh_token|api_key|authorization_key)\s*=\s*)[^\s"\'<>]+'), rb'\1' + REDACTED),
    (re.compile(rb'(?i)(\bBearer\s+)[A-Za-z0-9._~-]{16,}'), rb'\1' + REDACTED),
    (re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,}|xox[baprs]-[A-Za-z0-9-]{20,})\b'), REDACTED),
    (re.compile(rb'(https?://)[^\s/:@]+:[^\s/@]+@'), rb'\1' + REDACTED + b'@'),
]
PRIVATE_KEY = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')
SKIP_PARTS = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.git'}
SKIP_NAMES = {'.env', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519', 'Cookies', 'Login Data', 'Web Data'}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def redact(data: bytes) -> tuple[bytes, int]:
    if PRIVATE_KEY.search(data):
        raise ValueError('Private key material detected; publication stopped')
    count = 0
    for pattern, replacement in PATTERNS:
        data, n = pattern.subn(replacement, data)
        count += n
    return data, count


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def database_snapshot(source: Path, destination: Path) -> dict:
    """Include committed WAL transactions; scrub copies and remove freed pages."""
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            tables = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            counts = {t: dst.execute(f'SELECT count(*) FROM {identifier(t)}').fetchone()[0] for t in tables}
            redactions = 0
            for t in tables:
                columns = [r[1] for r in dst.execute(f'PRAGMA table_info({identifier(t)})')]
                for row in dst.execute(f'SELECT rowid, * FROM {identifier(t)}').fetchall():
                    changes = {}
                    for name, value in zip(columns, row[1:]):
                        if isinstance(value, str):
                            clean, n = redact(value.encode())
                            if n:
                                changes[name] = clean.decode()
                                redactions += n
                    if changes:
                        assignments = ','.join(f'{identifier(c)}=?' for c in changes)
                        dst.execute(f'UPDATE {identifier(t)} SET {assignments} WHERE rowid=?', [*changes.values(), row[0]])
            dst.commit()
            dst.execute('PRAGMA journal_mode=DELETE')
            dst.execute('VACUUM')
            assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert counts == {t: dst.execute(f'SELECT count(*) FROM {identifier(t)}').fetchone()[0] for t in tables}
    return {'method': 'sqlite-online-backup', 'tables': counts, 'redactions': redactions, 'integrity_check': 'ok'}


def safe_name(name: str) -> str:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name:
        raise ValueError(f'Unsafe archive path: {name}')
    return str(p)


def safe_link(name: str, target: str) -> str:
    if not target or PurePosixPath(target).is_absolute() or '\\' in target:
        raise ValueError(f'Unsafe link: {name}')
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    if resolved == '..' or resolved.startswith('../'):
        raise ValueError(f'Link escapes snapshot: {name}')
    return target


def walk(source: Path):
    if source.is_symlink() or source.is_file():
        yield source
        return
    for folder, dirs, files in os.walk(source, followlinks=False):
        for n in list(dirs):
            p = Path(folder) / n
            if p.is_symlink():
                yield p
                dirs.remove(n)
        for n in files:
            yield Path(folder) / n


def create(spec_file: Path, output: Path) -> dict:
    spec = json.loads(spec_file.read_text())
    output.mkdir(parents=True, exist_ok=False)
    mappings = [(Path(s['path']).absolute(), s['prefix']) for c in spec['components'] for s in c['sources']]
    exact_mappings = dict(mappings)
    mappings = [m for m in mappings if m[0].is_dir() and not m[0].is_symlink()]
    mappings.extend((Path(m['path']), m['prefix']) for m in spec.get('link_roots', []))
    mappings.sort(key=lambda m: -len(str(m[0])))
    excluded, assets = [], []
    manifest_path = output / 'files.jsonl'
    with manifest_path.open('w') as manifest, tempfile.TemporaryDirectory(prefix='ceres-snapshot-') as tmp:
        for component in spec['components']:
            name = component['name']
            archive = output / f'{name}.tar.zst'
            seen, count, size, redactions = set(), 0, 0, 0
            with archive.open('wb') as af:
                proc = subprocess.Popen(['zstd', '-T4', '-6', '-q'], stdin=subprocess.PIPE, stdout=af)
                try:
                    with tarfile.open(fileobj=proc.stdin, mode='w|') as tar:
                        for source in component['sources']:
                            # Installed dependency source is immutable. Its auth
                            # variable names and public test fixtures are not live
                            # credentials; audit it separately instead of rewriting
                            # Python/JS code with log-redaction expressions.
                            sanitize = source.get('sanitize', component.get('sanitize'))
                            base = Path(source['path']).absolute()
                            for path in sorted(walk(base)):
                                rel = path.relative_to(base) if base.is_dir() and not base.is_symlink() else Path()
                                arc = safe_name(str(PurePosixPath(source['prefix']) / rel.as_posix()))
                                if arc in seen:
                                    continue
                                seen.add(arc)
                                reason = None
                                if SKIP_PARTS.intersection(path.parts) or path.name in SKIP_NAMES or path.name.startswith('.env.'):
                                    reason = 'cache-or-authentication-file'
                                elif path.name.endswith(('-wal', '-shm', '.pyc', '.pid', '.tty')):
                                    reason = 'transient-file; committed SQLite WAL is merged by backup'
                                elif any(str(path).startswith(p) for p in component.get('exclude_prefixes', [])):
                                    reason = 'excluded-scope'
                                elif '/browser/' in str(path) and 'profile' in str(path):
                                    reason = 'browser-profile'
                                if reason:
                                    excluded.append({'path': arc, 'reason': reason})
                                    continue
                                row = {'component': name, 'path': arc}
                                info = tarfile.TarInfo(arc)
                                info.mtime = int(path.lstat().st_mtime)
                                info.uid = info.gid = 0
                                info.mode = 0o644 | (path.lstat().st_mode & 0o111)
                                if path.is_symlink():
                                    target = os.readlink(path)
                                    absolute = Path(os.path.abspath(path.parent / target))
                                    mapped = exact_mappings.get(absolute)
                                    if mapped is None:
                                        for orig, dest in mappings:
                                            if absolute == orig or orig in absolute.parents:
                                                mapped = str(PurePosixPath(dest) / absolute.relative_to(orig).as_posix())
                                                break
                                    if mapped is None:
                                        # uv's minor-version interpreter alias can point at
                                        # the pinned patch-version directory in this snapshot.
                                        resolved = absolute.resolve()
                                        mapped = exact_mappings.get(resolved)
                                        if mapped is None:
                                            for orig, dest in mappings:
                                                if resolved == orig or orig in resolved.parents:
                                                    mapped = str(PurePosixPath(dest) / resolved.relative_to(orig).as_posix())
                                                    break
                                    if mapped is not None:
                                        target = posixpath.relpath(mapped, posixpath.dirname(arc))
                                    elif os.path.isabs(target):
                                        excluded.append({'path': arc, 'reason': 'external-absolute-symlink', 'target': target})
                                        continue
                                    info.type, info.linkname = tarfile.SYMTYPE, safe_link(arc, target)
                                    tar.addfile(info)
                                    row.update(type='symlink', target=target)
                                else:
                                    data = path.read_bytes()
                                    row.update(type='file', source_sha256=hashlib.sha256(data).hexdigest())
                                    n = 0
                                    if sanitize and data.startswith(b'SQLite format 3\0'):
                                        dest = Path(tmp) / f'{name}-{count}.sqlite3'
                                        try:
                                            row['database'] = database_snapshot(path, dest)
                                            data = dest.read_bytes()
                                            n = row['database']['redactions']
                                        except sqlite3.DatabaseError as error:
                                            # gz-transport ships intentionally corrupt SQLite fixtures.
                                            # Preserve those fixtures, never silently accept a broken live DB.
                                            if '/diagnostics/bridge/' not in str(path) or '/test/' not in str(path):
                                                raise ValueError(f'Cannot snapshot database: {path}') from error
                                            _, found = redact(data)
                                            if found:
                                                raise ValueError(f'Credentials in corrupt fixture: {path}') from error
                                            row['database'] = {'method': 'unchanged-upstream-test-fixture', 'integrity_check': str(error)}
                                        finally:
                                            if dest.exists():
                                                dest.unlink()
                                    elif sanitize and b'\0' not in data[:8192]:
                                        data, n = redact(data)
                                    info.size = len(data)
                                    tar.addfile(info, io.BytesIO(data))
                                    row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), redactions=n)
                                    size += len(data)
                                    redactions += n
                                manifest.write(json.dumps(row, ensure_ascii=False) + '\n')
                                count += 1
                    proc.stdin.close()
                    if proc.wait() != 0:
                        raise RuntimeError(f'Compression failed: {name}')
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait()
            if archive.stat().st_size >= 2 * 1024**3:
                raise ValueError(f'Split component below GitHub 2 GiB limit: {name}')
            assets.append({'name': archive.name, 'sha256': sha(archive), 'bytes': archive.stat().st_size,
                           'files': count, 'uncompressed_bytes': size, 'redactions': redactions})
            print(json.dumps(assets[-1]), flush=True)
    subprocess.run(['zstd', '-T4', '-6', '-q', '--keep', str(manifest_path)], check=True)
    mf = output / 'files.jsonl.zst'
    assets.append({'name': mf.name, 'sha256': sha(mf), 'bytes': mf.stat().st_size})
    (output / 'exclusions.json').write_text(json.dumps(excluded, ensure_ascii=False, indent=2) + '\n')
    ef = output / 'exclusions.json'
    assets.append({'name': ef.name, 'sha256': sha(ef), 'bytes': ef.stat().st_size})
    catalog = {**spec['metadata'], 'schema': 'urp.platform-snapshot/1', 'assets': assets,
               'exclusions_count': len(excluded), 'archive_layout': 'project/, host/, python/; unpack in a new directory only'}
    (output / 'snapshot.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(f"{a['sha256']}  {a['name']}\n" for a in assets) + f"{sha(output / 'snapshot.json')}  snapshot.json\n")
    return catalog


@contextlib.contextmanager
def opened_tar(path: Path):
    if path.name.endswith('.tar.gz'):
        with tarfile.open(path, mode='r|gz') as tar:
            yield tar
        return
    proc = subprocess.Popen(['zstd', '-d', '-c', '-q', str(path)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode='r|') as tar:
            yield tar
        proc.stdout.close()
        if proc.wait() != 0:
            raise ValueError(f'Archive decompression failed: {path.name}')
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()


def verify(catalog_path: Path, destination: Path | None = None, components: list[str] | None = None,
           assets_dir: Path | None = None) -> dict:
    catalog = json.loads(catalog_path.read_text())
    if catalog.get('schema') != 'urp.platform-snapshot/1':
        raise ValueError('Unsupported snapshot schema')
    base = assets_dir or catalog_path.parent
    for a in catalog['assets']:
        safe_name(a['name'])
        if '/' in a['name'] or sha(base / a['name']) != a['sha256']:
            raise ValueError(f"Checksum mismatch: {a['name']}")
    if destination:
        destination.mkdir(parents=True, exist_ok=False)
    total = 0
    for a in catalog['assets']:
        component = a['name'].removesuffix('.tar.zst').removesuffix('.tar.gz')
        if not a['name'].endswith(('.tar.zst', '.tar.gz')) or (components and component not in components):
            continue
        seen = set()
        with opened_tar(base / a['name']) as tar:
            for member in tar:
                name = safe_name(member.name)
                if name in seen:
                    raise ValueError(f'Duplicate member: {name}')
                seen.add(name)
                if not member.isfile() and not member.issym():
                    raise ValueError(f'Unsupported member: {name}')
                if member.issym():
                    safe_link(name, member.linkname)
                if destination:
                    path = destination / name
                    if not path.resolve().is_relative_to(destination.resolve()):
                        raise ValueError(f'Path escapes destination: {name}')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if path.exists() or path.is_symlink():
                        raise ValueError(f'Restore would overwrite: {name}')
                    if member.issym():
                        path.symlink_to(member.linkname)
                    else:
                        import shutil
                        with path.open('xb') as f:
                            shutil.copyfileobj(tar.extractfile(member), f)
                        path.chmod(0o644 | (member.mode & 0o111))
                total += 1
        if len(seen) != a['files']:
            raise ValueError(f"File count mismatch: {a['name']}")
    return {'valid': True, 'assets': len(catalog['assets']), 'archive_members': total,
            'restored_to': str(destination) if destination else None}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    create_p = sub.add_parser('create')
    create_p.add_argument('--spec', type=Path, required=True)
    create_p.add_argument('--output', type=Path, required=True)
    for cmd in ('verify', 'restore'):
        x = sub.add_parser(cmd)
        x.add_argument('--catalog', type=Path, required=True)
        x.add_argument('--assets-dir', type=Path, help='Use a Git-tracked catalog to verify separately downloaded assets')
        if cmd == 'restore':
            x.add_argument('--destination', type=Path, required=True)
            x.add_argument('--components', nargs='+')
    args = p.parse_args()
    if args.command == 'create':
        create(args.spec, args.output)
    else:
        print(json.dumps(verify(args.catalog, getattr(args, 'destination', None), getattr(args, 'components', None), args.assets_dir)))


if __name__ == '__main__':
    main()
