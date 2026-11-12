"""Asset store under runtime/assets: one folder per asset, numbered versions, a JSON manifest.

A version starts as a draft. Confirming its frame / calibration / vehicle profile locks it; scenarios
and experiment records only bind locked versions (id + version + content hash), so later edits never
move what an old result used. Changing a locked version creates the next version: the files are
hard-linked, the metadata is copied and patched.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import paths

KINDS = {'map': '地图', 'vehicle': '车辆', 'facility': '障碍物与设施', 'scenario': '场景'}
ID_RE = re.compile(r'^(map|vehicle|facility|scenario)-[a-z0-9-]{1,48}$')
EDITABLE = ('name', 'category', 'tags', 'notes', 'archived')
_LOCK = threading.RLock()


def root() -> Path:
    return paths.RUNTIME / 'assets'


def staging_root() -> Path:
    return root() / '_staging'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _slug(name: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-')
    return s[:24].strip('-') or 'asset'


def new_id(kind: str, name: str) -> str:
    if kind not in KINDS:
        raise ValueError(f'未知资产类别 {kind}')
    return f'{kind}-{_slug(name)}-{secrets.token_hex(3)}'


def asset_dir(aid: str) -> Path:
    if not isinstance(aid, str) or not ID_RE.match(aid):
        raise KeyError(aid)
    return root() / aid


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=False))
    tmp.replace(path)


def load(aid: str) -> dict:
    path = asset_dir(aid) / 'asset.json'
    if not path.is_file():
        raise KeyError(aid)
    return json.loads(path.read_text())


def save(manifest: dict) -> dict:
    manifest['updated_utc'] = utc_now()
    _write_json(asset_dir(manifest['id']) / 'asset.json', manifest)
    return manifest


def list_all(include_archived: bool = False) -> list[dict]:
    out = []
    if not root().is_dir():
        return out
    for path in sorted(root().glob('*/asset.json')):
        try:
            m = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if include_archived or not m.get('archived'):
            out.append(m)
    return out


def version(manifest: dict, v: int | None = None) -> dict:
    v = int(v or manifest['current'])
    for ver in manifest['versions']:
        if ver['v'] == v:
            return ver
    raise KeyError(f"{manifest['id']}@{v}")


def vdir(aid: str, v: int) -> Path:
    return asset_dir(aid) / f'v{int(v)}'


def _inventory(vd: Path) -> tuple[list[dict], str]:
    """Files of a version and its content hash (over the original files, not derived previews)."""
    files, h = [], hashlib.sha256()
    for p in sorted(vd.rglob('*')):
        if not p.is_file():
            continue
        rel = p.relative_to(vd).as_posix()
        digest = sha256_file(p)
        files.append({'path': rel, 'size': p.stat().st_size, 'sha256': digest})
        if rel.startswith('files/'):
            h.update(f'{rel}\0{digest}\n'.encode())
    return files, h.hexdigest()


def create(kind: str, name: str, staged: Path, *, subtype: str, source: dict, meta: dict, validation: dict,
           preview: str | None = None, category: str = '', tags: list[str] | None = None) -> dict:
    """New asset whose v1 is the staged folder (with files/ and derived/ inside)."""
    with _LOCK:
        aid = new_id(kind, name)
        d = asset_dir(aid)
        d.mkdir(parents=True)
        shutil.move(str(staged), str(d / 'v1'))
        files, digest = _inventory(d / 'v1')
        now = utc_now()
        manifest = {
            'id': aid, 'kind': kind, 'subtype': subtype, 'name': str(name)[:120] or aid, 'category': category,
            'tags': list(tags or []), 'notes': '', 'archived': False, 'created_utc': now, 'updated_utc': now, 'current': 1,
            'versions': [{'v': 1, 'created_utc': now, 'sha256': digest, 'files': files, 'source': source, 'meta': meta,
                          'validation': validation, 'preview': preview, 'locked': False, 'locked_utc': None, 'based_on': None}],
        }
        return save(manifest)


def update(aid: str, patch: dict) -> dict:
    with _LOCK:
        m = load(aid)
        for k in EDITABLE:
            if k in patch:
                v = patch[k]
                if k == 'tags':
                    v = [str(t)[:32] for t in (v or [])][:20]
                elif k == 'archived':
                    v = bool(v)
                else:
                    v = str(v or '')[:400]
                m[k] = v
        return save(m)


def update_version_meta(aid: str, v: int, patch: dict, *, source: dict | None = None) -> dict:
    with _LOCK:
        m = load(aid)
        ver = version(m, v)
        if ver['locked']:
            raise PermissionError(f'{aid} v{v} 已锁定；修改会生成新版本')
        ver['meta'] = _merge(ver['meta'], patch)
        if source:
            ver['source'] = {**ver['source'], **source}
        return save(m)


def lock(aid: str, v: int) -> dict:
    with _LOCK:
        m = load(aid)
        ver = version(m, v)
        if not ver['locked']:
            ver['locked'], ver['locked_utc'] = True, utc_now()
            files, ver['sha256'] = _inventory(vdir(aid, v))
            ver['files'] = files
        return save(m)


def fork(aid: str, v: int, patch: dict | None = None, *, staged: Path | None = None, source: dict | None = None,
         meta: dict | None = None, validation: dict | None = None, preview: str | None = None) -> dict:
    """Next version: either hard-linked copy of v with patched metadata, or new staged files (replacement)."""
    with _LOCK:
        m = load(aid)
        base = version(m, v)
        nv = max(x['v'] for x in m['versions']) + 1
        target = vdir(aid, nv)
        if staged is not None:
            shutil.move(str(staged), str(target))
        else:
            shutil.copytree(vdir(aid, v), target, copy_function=_link_or_copy)
        files, digest = _inventory(target)
        ver = {'v': nv, 'created_utc': utc_now(), 'sha256': digest, 'files': files,
               'source': source or copy.deepcopy(base['source']),
               'meta': meta if meta is not None else _merge(copy.deepcopy(base['meta']), patch or {}),
               'validation': validation if validation is not None else copy.deepcopy(base['validation']),
               'preview': preview if preview is not None else base.get('preview'),
               'locked': False, 'locked_utc': None, 'based_on': v}
        m['versions'].append(ver)
        m['current'] = nv
        return save(m)


def set_current(aid: str, v: int) -> dict:
    with _LOCK:
        m = load(aid)
        version(m, v)
        m['current'] = int(v)
        return save(m)


def file_path(aid: str, v: int, rel: str) -> Path:
    base = vdir(aid, v).resolve()
    target = (base / rel).resolve()
    if base not in target.parents or not target.is_file():
        raise FileNotFoundError(rel)
    return target


def delete(aid: str) -> None:
    with _LOCK:
        shutil.rmtree(asset_dir(aid))


def new_staging() -> tuple[str, Path]:
    token = secrets.token_hex(8)
    d = staging_root() / token
    (d / 'files').mkdir(parents=True)
    (d / 'derived').mkdir()
    _sweep_staging()
    return token, d


def staging(token: str) -> Path:
    if not re.fullmatch(r'[0-9a-f]{16}', str(token)):
        raise KeyError(token)
    d = staging_root() / token
    if not d.is_dir():
        raise KeyError(token)
    return d


def _sweep_staging(max_age_s: float = 6 * 3600) -> None:
    now = time.time()
    for d in staging_root().glob('*'):
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age_s:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _link_or_copy(src: str, dst: str) -> str:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


# replaced as a whole, never merged key by key (a circle footprint must not keep a rectangle's length)
ATOMIC = frozenset({'footprint', 'frame', 'calibration', 'size_m'})


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ATOMIC:
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out
