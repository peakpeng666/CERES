"""Optional data downloaded inside the app instead of shipped in the installer.

A pack is a zip published next to the releases. It is checked against the SHA256 in ``data/packs.json``, unpacked into a
fresh directory and only then moved into place, so a failed or cancelled download leaves nothing behind and an installed
pack is never overwritten. Packs live under the runtime directory and are read-only to the rest of the app.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from . import paths

log = logging.getLogger('urp_lab.packs')

MANIFEST = Path(__file__).resolve().parent / 'data' / 'packs.json'
CHUNK = 1 << 20
MAX_MEMBERS = 200_000


class PackError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Cancelled(Exception):
    pass


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def _safe_extract(archive: Path, dest: Path, limit: int):
    """Refuse anything that would land outside the pack or grow past what the manifest declares."""
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        if len(members) > MAX_MEMBERS or sum(m.file_size for m in members) > limit:
            raise PackError(422, 'pack_too_large', '数据包内容超过声明的大小')
        for m in members:
            parts = PurePosixPath(m.filename).parts
            if m.filename.startswith(('/', '\\')) or '..' in parts or '\\' in m.filename or (m.external_attr >> 16) & 0o170000 == 0o120000:
                raise PackError(422, 'pack_unsafe', f'数据包含有不安全的路径：{m.filename}')
        z.extractall(dest)


class PackManager:
    def __init__(self, runtime: Path, bus=None):
        self.root = Path(runtime) / 'packs'
        self.bus = bus
        self.on_installed = None
        self.jobs: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def _dir(self, spec: dict) -> Path:
        return self.root / f"{spec['id']}-{spec['version']}"

    def _spec(self, pack_id: str) -> dict:
        spec = next((p for p in manifest()['packs'] if p['id'] == pack_id), None)
        if spec is None:
            raise PackError(404, 'unknown_pack', f'没有数据包 {pack_id}')
        return spec

    def installed(self, pack_id: str) -> Path | None:
        spec = next((p for p in manifest()['packs'] if p['id'] == pack_id), None)
        path = self._dir(spec) if spec else None
        return path if path and (path / '.complete').is_file() else None

    def _publish(self, pack_id: str):
        if self.bus:
            self.bus.publish('pack', {'id': pack_id, **self.jobs.get(pack_id, {})})

    def list(self) -> list[dict]:
        out = []
        for spec in manifest()['packs']:
            job = self.jobs.get(spec['id']) or {}
            state = job.get('state') or ('installed' if self.installed(spec['id']) else 'absent')
            out.append({k: spec.get(k) for k in ('id', 'name', 'summary', 'bytes', 'unpacked_bytes', 'version')} | {'state': state, 'progress': job.get('progress'), 'error': job.get('error')})
        return out

    def install(self, pack_id: str) -> dict:
        spec = self._spec(pack_id)
        with self._lock:
            if self.jobs.get(pack_id, {}).get('state') in ('downloading', 'installing'):
                raise PackError(409, 'pack_busy', '这个数据包正在下载')
            if self.installed(pack_id):
                raise PackError(409, 'pack_installed', '这个数据包已经安装')
            self.jobs[pack_id] = {'state': 'downloading', 'progress': 0.0, 'error': None}
            self._cancel[pack_id] = threading.Event()
        threading.Thread(target=self._work, args=(spec,), name=f'pack-{pack_id}', daemon=True).start()
        return next(p for p in self.list() if p['id'] == pack_id)

    def _download(self, spec: dict, part: Path, cancel: threading.Event):
        base = os.environ.get('URP_PACKS_BASE_URL') or manifest()['base_url']
        done = part.stat().st_size if part.exists() else 0
        headers = {'Range': f'bytes={done}-'} if done else {}
        with httpx.stream('GET', f"{base.rstrip('/')}/{spec['file']}", headers=headers, follow_redirects=True, timeout=httpx.Timeout(60.0, connect=15.0)) as r:
            if r.status_code == 200:
                done = 0
            elif r.status_code != 206:
                raise PackError(502, 'pack_unavailable', f'数据包暂时下载不了（HTTP {r.status_code}）')
            with part.open('ab' if done else 'wb') as f:
                for chunk in r.iter_bytes(CHUNK):
                    if cancel.is_set():
                        raise Cancelled
                    f.write(chunk)
                    done += len(chunk)
                    self.jobs[spec['id']]['progress'] = min(done / spec['bytes'], 1.0)
                    self._publish(spec['id'])

    def _work(self, spec: dict):
        pid = spec['id']
        cancel = self._cancel[pid]
        part = self.root / f"{spec['file']}.part"
        staging = self.root / f'.staging-{pid}'
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._download(spec, part, cancel)
            self.jobs[pid] = {'state': 'installing', 'progress': 1.0, 'error': None}
            self._publish(pid)
            digest = hashlib.sha256()
            with part.open('rb') as f:
                while chunk := f.read(CHUNK):
                    digest.update(chunk)
            if digest.hexdigest() != spec['sha256']:
                part.unlink(missing_ok=True)
                raise PackError(422, 'pack_corrupt', '下载的数据包校验不通过，已丢弃，可以重新下载')
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            _safe_extract(part, staging, int(spec['unpacked_bytes'] * 1.01) + 1)
            (staging / '.complete').write_text(spec['sha256'])
            os.replace(staging, self._dir(spec))
            part.unlink(missing_ok=True)
            self.jobs.pop(pid, None)
            self._publish(pid)
            log.info('pack %s installed', pid)
            if self.on_installed:
                self.on_installed()
        except Cancelled:
            shutil.rmtree(staging, ignore_errors=True)
            self.jobs.pop(pid, None)
            self._publish(pid)
        except (PackError, httpx.HTTPError, OSError, zipfile.BadZipFile) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            message = exc.message if isinstance(exc, PackError) else f'下载失败：{type(exc).__name__}'
            log.warning('pack %s failed: %s', pid, message)
            self.jobs[pid] = {'state': 'failed', 'progress': None, 'error': message}
            self._publish(pid)

    def cancel(self, pack_id: str):
        self._spec(pack_id)
        event = self._cancel.get(pack_id)
        if event:
            event.set()
        return {'id': pack_id}

    def remove(self, pack_id: str) -> dict:
        spec = self._spec(pack_id)
        if self.jobs.get(pack_id, {}).get('state') in ('downloading', 'installing'):
            raise PackError(409, 'pack_busy', '这个数据包正在下载')
        shutil.rmtree(self._dir(spec), ignore_errors=True)
        (self.root / f"{spec['file']}.part").unlink(missing_ok=True)
        self.jobs.pop(pack_id, None)
        return {'id': pack_id}

    def web_file(self, rel: str) -> Path | None:
        """A file the history pack serves under /data, when the build itself does not carry it."""
        root = self.installed('history')
        if root is None:
            return None
        parts = PurePosixPath(rel).parts
        if not parts or any(p in ('..', '.') or p.startswith('/') for p in parts):
            return None
        path = root.joinpath('web', 'data', *parts)
        return path if path.is_file() else None

    def start_archive(self, services):
        """With the history pack installed, the archive pages get their backend from the same Python as everything else."""
        from .services import ManagedProcess, ARCHIVE_PORT
        pack = self.installed('history')
        if pack is None or (services.archive is not None and services.archive.alive):
            return
        archive_db = Path(os.environ.get('URP_API_DB') or self.root.parent / 'archive' / 'research.sqlite3')
        argv = [sys.executable, '-m', 'urp_lab.archive_runner', '--pack', str(pack), '--db', str(archive_db), '--port', str(ARCHIVE_PORT)]
        services.archive = ManagedProcess('archive', argv, paths.LAB, services.logs_dir / 'archive.log')
        services.archive.start()
