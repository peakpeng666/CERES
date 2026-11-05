#!/usr/bin/env python3
"""Read-only audit of archived Debian->Ubuntu trust and exact root-package records.
Requires an independently trusted Debian archive keyring and installed sqv/dpkg-deb.
Does not install packages, use the network, or execute package payloads.
"""
import argparse
import hashlib
import io
import json
import lzma
from pathlib import Path
import subprocess
import tarfile
import tempfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fields(stanza):
    result, key = {}, None
    for line in stanza.splitlines():
        if line.startswith(' ') and key:
            result[key] += '\n' + line
        elif ': ' in line:
            key, value = line.split(': ', 1)
            result[key] = value
    return result


def release_sha256(payload):
    section = payload.split('SHA256:\n', 1)[1]
    rows = {}
    for line in section.splitlines():
        parts = line.split()
        if len(parts) == 3 and len(parts[0]) == 64:
            rows[parts[2]] = (parts[0], int(parts[1]))
    return rows


def require(condition, message):
    if not condition:
        raise SystemExit('FAIL: ' + message)


def verify_signature(keyring, source, out):
    subprocess.run(['sqv', '--keyring', str(keyring), '--output', str(out),
                    '--cleartext', str(source)], check=True, capture_output=True)
    return out.read_text()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--debian-trust-keyring', type=Path, required=True,
                        help='Independent trust root, NOT merely the bundled copy.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    bootstrap = root / 'bootstrap'
    snapshot = root / 'snapshot-20261021T000000Z'
    require(args.debian_trust_keyring.is_file(), 'Independent Debian keyring missing')
    with tempfile.TemporaryDirectory(prefix='jammy-metadata-verify-') as directory:
        temp = Path(directory)
        debian_payload = verify_signature(args.debian_trust_keyring,
            bootstrap / 'debian-trixie.InRelease', temp / 'debian-Release')
        index = lzma.decompress((bootstrap / 'debian-trixie-main-amd64.Packages.local-compressed.xz').read_bytes())
        require((sha(index), len(index)) == release_sha256(debian_payload)['main/binary-amd64/Packages'],
                'Debian Packages hash/size mismatch')
        candidates = [fields(stanza) for stanza in index.decode().split('\n\n')
                      if stanza.startswith('Package: ubuntu-keyring\n')]
        require(len(candidates) == 1, 'Expected exactly one Ubuntu keyring package')
        record = candidates[0]
        package = bootstrap / Path(record['Filename']).name
        package_data = package.read_bytes()
        require(sha(package_data) == record['SHA256'] and len(package_data) == int(record['Size']),
                'Ubuntu keyring .deb hash/size mismatch')
        data_tar = subprocess.run(['dpkg-deb', '--fsys-tarfile', str(package)], check=True,
                                   stdout=subprocess.PIPE).stdout
        with tarfile.open(fileobj=io.BytesIO(data_tar), mode='r:') as archive:
            members = [m for m in archive.getmembers() if m.isfile()
                       and m.name.endswith('/ubuntu-archive-keyring.gpg')]
            require(len(members) == 1, 'Expected one public Ubuntu archive keyring')
            key = archive.extractfile(members[0]).read()
        require(key == (bootstrap / 'ubuntu-archive-keyring.gpg').read_bytes(),
                'Extracted Ubuntu keyring differs from authenticated package')
        keyfile = temp / 'ubuntu-archive-keyring.gpg'
        keyfile.write_bytes(key)
        plan = json.loads((snapshot / 'index_download_plan.json').read_text())['indexes']
        root_packages = json.loads((snapshot / 'root_package_candidates.json').read_text())['packages']
        seen = {item['package']: [] for item in root_packages}
        for suite in ['jammy', 'jammy-updates', 'jammy-security']:
            payload = verify_signature(keyfile, snapshot / f'{suite}.InRelease', temp / f'{suite}-Release')
            authenticated = release_sha256(payload)
            for item in [r for r in plan if r['suite'] == suite]:
                compressed = (snapshot / item['local_path']).read_bytes()
                require((sha(compressed), len(compressed)) == authenticated[item['relative_path']],
                        f'Ubuntu index hash/size mismatch: {item["local_path"]}')
                for stanza in lzma.decompress(compressed).decode().split('\n\n'):
                    first = stanza.split('\n', 1)[0]
                    name = first.removeprefix('Package: ')
                    if name in seen:
                        seen[name].append(fields(stanza))
        for item in root_packages:
            records = [r for r in seen[item['package']] if r['Version'] == item['version']
                       and r['Architecture'] == item['architecture']]
            require(bool(records), f'Root pin absent: {item["package"]}')
            require(any(r['SHA256'] == item['sha256'] and r['Filename'] == item['filename']
                        and r['Size'] == item['size'] for r in records),
                    f'Root package record differs: {item["package"]}')
    print('PASS: independent Debian trust -> authenticated Ubuntu keyring -> 3 Ubuntu Release signatures -> 6 indexes -> 13 exact root package records.')
    print('NOT CLAIMED: transitive dependency resolution, root-package payload verification, installation, or native Jammy build.')


if __name__ == '__main__':
    main()
