"""Offline, lossless runtime transfer. Raw data stays outside Git; identities and hashes travel with it.

Stop both backends first. SQLite backups include WAL contents; an execution-owner lock prevents exporting a live
workbench. Restore always creates a new directory. Absolute paths inside evidence remain provenance, never rewritten.
This is a data format, not an environment installer. It deliberately excludes compiled caches and credential files.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import uuid
import zipfile

SCHEMA = 'urp.runtime-bundle/1'
CHUNK = 1 << 20
MAX_MANIFEST = 32 << 20
MAX_FILES = 200_000
SKIP_DIRS = {'native', '__pycache__', '.pytest_cache'}
MACHINE_DIRS = {'diagnostics/bridge/tools'}  # downloaded debugger binaries, libraries and system docs
PRIVATE_NAMES = {'credentials.json', 'llm.json', '.env', 'id_rsa', 'id_ed25519'}


class TransferError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(CHUNK), b''):
            h.update(chunk)
    return h.hexdigest()


def _json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise TransferError(f'重复的 JSON 键：{key}')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda s: (_ for _ in ()).throw(TransferError(s)))


def _name(name):
    parts = PurePosixPath(name).parts
    if (not parts or name != '/'.join(parts) or any(p in ('.', '..') or ':' in p or '\\' in p
            or p.endswith((' ', '.')) or p.split('.')[0].upper() in
            {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
            for p in parts) or name.startswith('/') or '\x00' in name):
        raise TransferError(f'不能跨平台恢复的路径：{name}')
    if parts[0] not in ('runtime', 'archive') and name != 'dataset.json':
        raise TransferError(f'数据包路径超出范围：{name}')
    if parts[0] in ('runtime', 'archive') and len(parts) < 2:
        raise TransferError(f'数据根必须是目录：{name}')
    if any(p.lower() in PRIVATE_NAMES or p.lower().startswith('.env.') for p in parts):
        raise TransferError(f'凭据或环境密钥不能放入数据包：{name}')
    return parts


def database_info(path):
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise TransferError(f'SQLite 校验失败：{path}')
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        tables = {}
        for kind, name, _, _ in schema:
            if kind == 'table':
                quoted = '"' + name.replace('"', '""') + '"'
                tables[name] = db.execute(f'SELECT count(*) FROM {quoted}').fetchone()[0]
        return {'user_version': db.execute('PRAGMA user_version').fetchone()[0],
                'schema_sha256': hashlib.sha256(canonical(schema)).hexdigest(), 'rows': tables}


@contextlib.contextmanager
def _owner_lock(db):
    lock = Path(str(Path(db).resolve()) + '.jobs.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a+b') as f:
        try:
            if sys.platform == 'win32':
                import msvcrt
                f.seek(0)
                if not f.read(1):
                    f.write(b'\0'); f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise TransferError(f'数据仍在使用，请先停止内置及容器后端：{db}') from exc
        try:
            yield
        finally:
            if sys.platform == 'win32':
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def _backup(source, dest):
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
        dst.execute('PRAGMA journal_mode=DELETE')
    return database_info(dest)


def _provenance():
    from . import paths
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=paths.REPO, timeout=3, stderr=subprocess.DEVNULL).decode().strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=paths.REPO, timeout=3, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = None, None
        packaged = paths.REPO.parent / 'backend-manifest.json'
        if packaged.is_file():
            meta = _json(packaged.read_bytes())
            commit, dirty = meta.get('source_commit'), meta.get('source_dirty')
    lock = paths.LAB / 'uv.lock'
    files = {p.relative_to(paths.LAB).as_posix(): digest(p) for p in sorted((paths.LAB / 'urp_lab').rglob('*.py'))}
    return {'commit': commit, 'source_dirty': dirty, 'lab_lock_sha256': digest(lock) if lock.is_file() else None,
            'lab_source_sha256': hashlib.sha256(canonical(files)).hexdigest(), 'python': sys.version.split()[0]}


def export_bundle(runtime, bundle, archive_db=None):
    runtime, bundle = Path(runtime).expanduser().resolve(), Path(bundle).expanduser().resolve()
    if not runtime.is_dir():
        raise TransferError(f'运行目录不存在：{runtime}')
    if bundle.exists() or bundle.is_relative_to(runtime):
        raise TransferError('输出必须是运行目录以外、尚不存在的文件')
    archive_db = Path(archive_db).expanduser().resolve() if archive_db else None
    if archive_db and not archive_db.is_file():
        raise TransferError(f'档案数据库不存在：{archive_db}')
    bundle.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        stack.enter_context(_owner_lock(runtime / 'lab.sqlite3'))
        if archive_db:
            stack.enter_context(_owner_lock(archive_db))
        staging = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.urp-export-', dir=bundle.parent)))
        os.chmod(staging, 0o700)
        entries, excluded, sources = [], [], []
        for directory, dirs, files in os.walk(runtime, followlinks=False):
            base = Path(directory)
            for name in dirs[:]:
                p = base / name
                if name in SKIP_DIRS or p.relative_to(runtime).as_posix() in MACHINE_DIRS or p.is_symlink():
                    excluded.append({'path': 'runtime/' + p.relative_to(runtime).as_posix(), 'reason': 'machine_cache_or_symlink'})
                    dirs.remove(name)
            for name in sorted(files):
                p = base / name
                rel = 'runtime/' + p.relative_to(runtime).as_posix()
                if p.is_symlink() or name.endswith(('-wal', '-shm', '.jobs.lock', '.lock', '.pyc')):
                    excluded.append({'path': rel, 'reason': 'sqlite_sidecar_lock_or_symlink'})
                    continue
                if not p.is_file():
                    raise TransferError(f'不支持的文件类型：{p}')
                _name(rel)
                sources.append((rel, p))
        if archive_db:
            sources.append(('archive/research.sqlite3', archive_db))
        identity = runtime.parent / 'dataset.json'
        dataset = _json(identity.read_bytes()) if identity.is_file() else {
            'schema': 'urp.dataset/1', 'dataset_id': str(uuid.uuid4()), 'created_utc': utc_now()}
        _dataset(dataset)
        (staging / 'dataset.json').write_bytes(canonical(dataset))
        sources.append(('dataset.json', staging / 'dataset.json'))
        if len(sources) > MAX_FILES:
            raise TransferError('文件数量超过数据包上限')
        seen = set()
        out = staging / 'bundle.zip'
        with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
            for n, (rel, p) in enumerate(sorted(sources)):
                if rel.casefold() in seen:
                    raise TransferError(f'文件名大小写冲突：{rel}')
                seen.add(rel.casefold())
                meta = {'path': rel}
                if p.suffix in ('.sqlite', '.sqlite3', '.db'):
                    copied = staging / f'db-{n}.sqlite3'
                    meta['sqlite'] = _backup(p, copied)
                    meta['source_sha256'] = digest(p)
                    p = copied
                before = p.stat()
                meta.update(bytes=before.st_size, sha256=digest(p))
                z.write(p, rel)
                after = p.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise TransferError(f'导出期间数据被改写：{p}')
                entries.append(meta)
            manifest = {'schema': SCHEMA, 'bundle_id': str(uuid.uuid4()), 'created_utc': utc_now(), 'dataset': dataset,
                        'source': _provenance(), 'source_locations': {'runtime': str(runtime), 'archive_db': str(archive_db) if archive_db else None},
                        'files': entries, 'excluded': excluded,
                        'contract': 'Offline data only. Preserve failures, units, seeds and provenance. No credentials or compiled environments.'}
            raw = canonical(manifest)
            if len(raw) > MAX_MANIFEST:
                raise TransferError('清单超过数据包上限')
            z.writestr('manifest.json', raw)
        # Verify what was actually written before publishing a local bundle.
        verify_bundle(out)
        os.chmod(out, 0o600)
        # Hard-link is an atomic no-overwrite publication on the same filesystem (including NTFS).
        os.link(out, bundle)
        return {'path': str(bundle), 'sha256': digest(bundle), 'bytes': bundle.stat().st_size,
                'bundle_id': manifest['bundle_id'], 'dataset': dataset, 'files': len(entries),
                'unpacked_bytes': sum(e['bytes'] for e in entries), 'excluded': len(excluded)}


def _preflight(z):
    members = z.infolist()
    if len(members) > MAX_FILES + 1 or sum(m.filename == 'manifest.json' for m in members) != 1:
        raise TransferError('数据包清单缺失、重复或文件过多')
    info = z.getinfo('manifest.json')
    if info.file_size > MAX_MANIFEST:
        raise TransferError('清单过大')
    manifest = _json(z.read(info))
    if manifest.get('schema') != SCHEMA or not isinstance(manifest.get('files'), list):
        raise TransferError('不支持的数据包格式')
    _dataset(manifest.get('dataset'))
    expected = {}
    for e in manifest['files']:
        if not isinstance(e, dict) or not isinstance(e.get('path'), str):
            raise TransferError('无效文件清单')
        _name(e['path'])
        if e['path'].casefold() in expected or type(e.get('bytes')) is not int or e['bytes'] < 0:
            raise TransferError('重复路径或无效文件大小')
        if not isinstance(e.get('sha256'), str) or len(e['sha256']) != 64 or any(c not in '0123456789abcdef' for c in e['sha256']):
            raise TransferError('无效 SHA256')
        expected[e['path'].casefold()] = e
    if 'dataset.json' not in expected:
        raise TransferError('数据集身份缺失')
    actual = set()
    for m in members:
        if m.filename == 'manifest.json':
            continue
        _name(m.filename)
        e = expected.get(m.filename.casefold())
        mode = m.external_attr >> 16
        if (m.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                or m.filename.casefold() in actual or not e or e['path'] != m.filename or e['bytes'] != m.file_size):
            raise TransferError(f'文件与清单不符：{m.filename}')
        actual.add(m.filename.casefold())
    if actual != set(expected):
        raise TransferError('文件缺失')
    names = set(e['path'] for e in expected.values())
    if any('/'.join(PurePosixPath(n).parts[:i]) in names for n in names for i in range(1, len(PurePosixPath(n).parts))):
        raise TransferError('文件与目录冲突')
    return manifest


def _dataset(value):
    if not isinstance(value, dict) or value.get('schema') != 'urp.dataset/1' or not isinstance(value.get('dataset_id'), str) or not value['dataset_id']:
        raise TransferError('无效的数据集身份')


def _read_checked(z, entry, target=None):
    h, size = hashlib.sha256(), 0
    with z.open(entry['path']) as f, (target.open('xb') if target else contextlib.nullcontext(None)) as out:
        for chunk in iter(lambda: f.read(CHUNK), b''):
            size += len(chunk)
            if size > entry['bytes']:
                raise TransferError('文件内容超过声明大小')
            h.update(chunk)
            if out:
                out.write(chunk)
    if size != entry['bytes'] or h.hexdigest() != entry['sha256']:
        raise TransferError(f"文件校验失败：{entry['path']}")


def verify_bundle(bundle, expected_sha256=None):
    bundle = Path(bundle).expanduser().resolve()
    sha = digest(bundle)
    if expected_sha256 and sha != expected_sha256:
        raise TransferError('整个数据包的 SHA256 不匹配')
    with zipfile.ZipFile(bundle) as z, tempfile.TemporaryDirectory(prefix='urp-verify-') as tmp:
        manifest = _preflight(z)
        for n, e in enumerate(manifest['files']):
            db = Path(tmp) / f'{n}.sqlite3' if 'sqlite' in e else None
            _read_checked(z, e, db)
            if db:
                if database_info(db) != e['sqlite']:
                    raise TransferError(f"数据库结构或行数不符：{e['path']}")
                db.unlink()
        if _json(z.read('dataset.json')) != manifest.get('dataset'):
            raise TransferError('数据集身份与清单不符')
    return {'path': str(bundle), 'sha256': sha, 'bundle_id': manifest['bundle_id'], 'dataset': manifest['dataset'],
            'files': len(manifest['files']), 'unpacked_bytes': sum(e['bytes'] for e in manifest['files']), 'verified': True}


def restore_bundle(bundle, dest, expected_sha256=None):
    dest = Path(dest).expanduser()
    if not dest.is_absolute() or dest.exists() or dest.is_symlink():
        raise TransferError('恢复位置必须是尚不存在的绝对路径，不能覆盖现有数据')
    dest = dest.resolve()
    receipt = verify_bundle(bundle, expected_sha256)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if receipt['unpacked_bytes'] + (64 << 20) > shutil.disk_usage(dest.parent).free:
        raise TransferError('恢复位置可用空间不足')
    with tempfile.TemporaryDirectory(prefix='.urp-restore-', dir=dest.parent) as tmp:
        stage = Path(tmp) / 'data'
        stage.mkdir(mode=0o700)
        with zipfile.ZipFile(bundle) as z:
            manifest = _preflight(z)
            for e in manifest['files']:
                target = stage.joinpath(*_name(e['path']))
                target.parent.mkdir(parents=True, exist_ok=True)
                _read_checked(z, e, target)
                os.chmod(target, 0o600)
                if 'sqlite' in e and database_info(target) != e['sqlite']:
                    raise TransferError('恢复的数据库校验失败')
        (stage / 'transfer-receipt.json').write_bytes(canonical(receipt | {'restored_utc': utc_now(), 'destination': str(dest)}))
        if digest(bundle) != receipt['sha256']:
            raise TransferError('校验后数据包发生变化，恢复已中止')
        # Reserve a new directory without ever replacing one, including an empty directory created by another restore.
        dest.mkdir(mode=0o700)
        try:
            for child in stage.iterdir():
                child.rename(dest / child.name)
        except BaseException:
            shutil.rmtree(dest)  # only the new directory reserved above; existing data is never selected here
            raise TransferError('无法提交恢复目录；原有数据未修改') from None
    return receipt | {'destination': str(dest)}


def configure_cli(sub):
    data = sub.add_parser('data', help='离线导出、校验或恢复运行数据（不含密钥和环境）')
    modes = data.add_subparsers(dest='data_action', required=True)
    p = modes.add_parser('export')
    p.add_argument('--runtime', required=True)
    p.add_argument('--archive-db')
    p.add_argument('--out', required=True)
    p = modes.add_parser('recover', help='从已保存的 JSON 恢复只读会话证据，拒绝覆盖')
    p.add_argument('--runtime', required=True)
    p.add_argument('--source', action='append', required=True)
    for action in ('verify', 'restore'):
        p = modes.add_parser(action)
        p.add_argument('--bundle', required=True)
        p.add_argument('--sha256')
        if action == 'restore':
            p.add_argument('--dest', required=True)


def run_cli(args):
    try:
        if args.data_action == 'export':
            result = export_bundle(args.runtime, args.out, args.archive_db)
        elif args.data_action == 'recover':
            from .session_recovery import recover_exports
            result = recover_exports(args.runtime, args.source)
        elif args.data_action == 'verify':
            result = verify_bundle(args.bundle, args.sha256)
        else:
            result = restore_bundle(args.bundle, args.dest, args.sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (TransferError, OSError, sqlite3.Error, zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f'数据迁移失败：{exc}', file=sys.stderr)
        return 2
