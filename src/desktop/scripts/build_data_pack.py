#!/usr/bin/env python3
"""Build the history pack and record its checksum in the manifest the app ships with.

    python3 src/desktop/scripts/build_data_pack.py [--version 1]

Sources are read, never changed. The zip is deterministic: the same data gives the same SHA256, so rebuilding a release
does not invalidate what users have downloaded. Publish the zip to the release named in the manifest's base_url.
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / 'src/lab/ceres_lab/data/packs.json'
EPOCH = (2026, 1, 1, 0, 0, 0)
SOURCES = [(ROOT / 'src/frontend/dist/data', 'web/data'), (ROOT / 'data/packages', 'archive/packages'), (ROOT / 'data/backend/research.sqlite3', 'archive/research.sqlite3')]


def members():
    for source, target in SOURCES:
        if source.is_file():
            yield source, target
            continue
        if not source.is_dir():
            raise SystemExit(f'缺少 {source}')
        for path in sorted(p for p in source.rglob('*') if p.is_file()):
            yield path, f'{target}/{path.relative_to(source).as_posix()}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', type=int, default=1)
    parser.add_argument('--out', type=Path, default=ROOT / 'src/desktop/build/packs')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    name = f'history-{args.version}.zip'
    archive = args.out / name
    total = 0
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path, target in sorted(members(), key=lambda m: m[1]):
            info = zipfile.ZipInfo(target, EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            data = path.read_bytes()
            total += len(data)
            z.writestr(info, data, compresslevel=9)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    spec = {'id': 'history', 'name': '历史实验档案', 'summary': 'BPP 历史结果、实验档案与研究数据库', 'file': name, 'sha256': digest,
            'bytes': archive.stat().st_size, 'unpacked_bytes': total, 'version': args.version}
    manifest['packs'] = [spec]
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(spec, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
