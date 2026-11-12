#!/usr/bin/env python3
"""Fail before claiming latest if an installer, updater checksum or pinned history pack is missing."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import urllib.request


def verify(artifacts, version):
    required = [f'agri-route-workbench-{version}-windows-x64.exe',
                f'agri-route-workbench-{version}-linux-x86_64.AppImage',
                f'agri-route-workbench-{version}-linux-amd64.deb']
    # Electron builder normally uses x64 for AppImage and amd64 for deb; accept only the actual platform pair.
    if not (artifacts / required[1]).exists():
        required[1] = f'agri-route-workbench-{version}-linux-x64.AppImage'
    if not (artifacts / required[2]).exists():
        required[2] = f'agri-route-workbench-{version}-linux-x64.deb'
    for name in required:
        p = artifacts / name
        if not p.is_file() or p.stat().st_size < 10_000_000:
            raise ValueError(f'Installer missing or a stub: {name}')
    for name in ('latest.yml', 'latest-linux.yml'):
        text = (artifacts / name).read_text()
        if not re.search(rf'^version:\s*[\'"]?{re.escape(version)}[\'"]?\s*$', text, re.M):
            raise ValueError(f'Updater version mismatch: {name}')
        pairs = re.findall(r'- url:\s*([^\r\n]+)\s*\n\s+sha512:\s*([^\s]+)', text)
        if not pairs:
            raise ValueError(f'No updater checksums: {name}')
        for file, expected in pairs:
            p = artifacts / file.strip().strip('\'"')
            if p.parent.resolve() != artifacts.resolve():
                raise ValueError('Invalid installer path')
            actual = hashlib.sha512(p.read_bytes()).digest()
            if base64.b64encode(actual).decode() != expected:
                raise ValueError(f'Updater hash mismatch: {p.name}')
    manifest = Path(__file__).resolve().parents[2] / 'lab/ceres_lab/data/packs.json'
    packs = json.loads(manifest.read_text())
    for pack in packs['packs']:
        url = f"{packs['base_url'].rstrip('/')}/{pack['file']}"
        h, size = hashlib.sha256(), 0
        with urllib.request.urlopen(url, timeout=120) as response:
            for chunk in iter(lambda: response.read(1 << 20), b''):
                size += len(chunk); h.update(chunk)
        if size != pack['bytes'] or h.hexdigest() != pack['sha256']:
            raise ValueError(f"Pinned data pack unavailable or corrupt: {pack['id']}")
    hashes = []
    for p in sorted(artifacts.iterdir()):
        if p.is_file() and p.name != 'SHA256SUMS':
            hashes.append(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n')
    (artifacts / 'SHA256SUMS').write_text(''.join(hashes))
    print(json.dumps({'version': version, 'installers': required, 'data_packs_verified': len(packs['packs'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts', required=True, type=Path)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    verify(args.artifacts, args.version)
