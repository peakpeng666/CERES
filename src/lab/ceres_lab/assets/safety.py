"""Import hygiene: sanitised relative paths, bounded zip extraction, no links, no external fetches.

Nothing imported is ever executed: archives are only unpacked, XML is parsed with defusedxml, Xacro
from uploads is refused (its expressions and includes can run arbitrary logic), and remote URIs inside
models are reported as missing dependencies instead of being downloaded.
"""
from __future__ import annotations

import io
import re
import stat
import zipfile
from pathlib import Path

MAX_FILE_BYTES = 256 << 20
MAX_TOTAL_BYTES = 768 << 20
MAX_FILES = 4000
MAX_RATIO = 250          # uncompressed / compressed size for a single large member

SUFFIX_KIND = {
    '.yaml': 'yaml', '.yml': 'yaml', '.pgm': 'image', '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image',
    '.bmp': 'image', '.tif': 'image', '.tiff': 'image', '.geojson': 'geojson', '.json': 'json', '.glb': 'glb', '.gltf': 'gltf',
    '.bin': 'buffer', '.urdf': 'urdf', '.xacro': 'xacro', '.sdf': 'sdf', '.world': 'sdf', '.config': 'config',
    '.dae': 'mesh', '.obj': 'mesh', '.stl': 'mesh', '.mtl': 'material', '.ktx2': 'image', '.zip': 'zip',
    '.xml': 'xml', '.txt': 'text', '.md': 'text', '.license': 'text',
}


class ImportRejected(ValueError):
    """The upload cannot be imported as given (bad archive, unsafe path, size limits)."""


def clean_relpath(name: str) -> str:
    raw = str(name or '').replace('\\', '/')
    if not raw or raw.startswith('/') or re.match(r'^[A-Za-z]:', raw) or any(ord(c) < 32 for c in raw):
        raise ImportRejected(f'不安全的文件路径：{name!r}')
    parts = [p for p in raw.split('/') if p not in ('', '.')]
    if not parts or any(p == '..' for p in parts):
        raise ImportRejected(f'不安全的文件路径：{name!r}')
    return '/'.join(parts)


def write_member(dest: Path, rel: str, data: bytes) -> Path:
    if len(data) > MAX_FILE_BYTES:
        raise ImportRejected(f'{rel} 超过单文件上限 {MAX_FILE_BYTES >> 20} MB')
    target = dest / clean_relpath(rel)
    if dest.resolve() not in target.resolve().parents:
        raise ImportRejected(f'不安全的文件路径：{rel!r}')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def extract_zip(data: bytes, dest: Path) -> list[str]:
    """Unpacks into dest; returns warnings. Rejects links, traversal, bombs and oversize members."""
    warnings: list[str] = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ImportRejected(f'压缩包损坏：{exc}') from None
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise ImportRejected(f'压缩包文件数 {len(infos)} 超过上限 {MAX_FILES}')
        total = 0
        for info in infos:
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                warnings.append(f'跳过符号链接 {info.filename}')
                continue
            if info.flag_bits & 0x1:
                raise ImportRejected(f'不支持加密的压缩包成员 {info.filename}')
            try:
                rel = clean_relpath(info.filename)
            except ImportRejected:
                warnings.append(f'跳过不安全路径 {info.filename!r}')
                continue
            if rel.startswith('__MACOSX/') or rel.split('/')[-1].startswith('._'):
                continue
            total += info.file_size
            if info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ImportRejected('压缩包解压后超过大小上限')
            if info.file_size > (8 << 20) and info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                raise ImportRejected(f'{info.filename} 压缩比异常，疑似压缩炸弹')
            with z.open(info) as src:
                blob = src.read(info.file_size + 1)
            if len(blob) != info.file_size:
                raise ImportRejected(f'{info.filename} 实际大小与目录不符')
            write_member(dest, rel, blob)
    return warnings


def stage(items: list[tuple[str, bytes]], dest: Path) -> list[str]:
    """Writes uploads (keeping their relative folder paths) and unpacks archives into dest."""
    warnings: list[str] = []
    total = 0
    for name, data in items:
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ImportRejected('上传总量超过上限')
        rel = clean_relpath(name)
        if rel.lower().endswith('.zip'):
            warnings += extract_zip(data, dest)
        else:
            write_member(dest, rel, data)
    files = [p for p in dest.rglob('*') if p.is_file()]
    if not files:
        raise ImportRejected('没有可导入的文件')
    return warnings


def kind_of(path: Path) -> str:
    return SUFFIX_KIND.get(path.suffix.lower(), 'other')
