#!/usr/bin/env python3
"""Index tracked binary assets and third-party license evidence at one commit."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LICENSE_NAMES = ('license', 'licence', 'copying', 'notice', 'copyright')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def tree(ref, *paths):
    entries = []
    for row in git('ls-tree', '-r', '-z', ref, *paths).split(b'\0'):
        if row:
            meta, path = row.decode().split('\t', 1)
            mode, kind, oid = meta.split()
            entries.append({'path': path, 'mode': mode, 'kind': kind, 'oid': oid})
    return entries


def asset_kind(path):
    lower = path.lower()
    if lower.endswith('.pdf'):
        return 'textbook' if path.startswith('literature/教材/') else 'paper' if path.startswith('papers/') else 'document'
    if lower.endswith(('.zip', '.tar.gz', '.tgz', '.tar.xz', '.tar.zst', '.7z', '.rar')):
        return 'experiment-archive' if path.startswith('experiments/') else 'third-party-archive' if path.startswith('src/repos/') else 'archive'
    if lower.endswith(('.sqlite', '.sqlite3', '.db')):
        return 'database'
    if path.startswith('data/cifar-training/') or lower.endswith(('.pt', '.pth', '.npz', '.npy', '.whl')):
        return 'dataset-or-model'
    return None


def license_label(content):
    lower = content.lower()
    if 'gnu affero general public license' in lower:
        return 'AGPL: review version and scope'
    if 'gnu lesser general public license' in lower:
        return 'LGPL: review version and scope'
    if 'gnu general public license' in lower:
        return 'GPL: review version and scope'
    if 'attribution-noncommercial 4.0 international' in lower:
        return 'CC-BY-NC-4.0'
    if 'polyform noncommercial license 1.0.0' in lower:
        return 'PolyForm-Noncommercial-1.0.0'
    if any(term in lower for term in ('non-commercial', 'noncommercial', 'non commercial', 'research purposes only')):
        return 'restricted-terms: review text'
    if 'permission is hereby granted, free of charge' in lower:
        return 'MIT-style'
    if 'apache license' in lower and 'version 2.0' in lower:
        return 'Apache-2.0'
    if 'redistribution and use in source and binary forms' in lower:
        return 'BSD-style: review clauses'
    return 'custom-or-unidentified: review text'


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def verified_copies(path):
    if not path.is_file():
        return {}, []
    records = json.loads(path.read_text())['copies']
    verified = {}
    for record in records:
        if record['state'] != 'restore-verified':
            continue
        if (not record.get('asset_path') or not record.get('url', '').startswith('https://')
                or not re.fullmatch(r'[0-9a-f]{40}', record.get('git_blob_sha1', ''))
                or not re.fullmatch(r'[0-9a-f]{64}', record.get('sha256', ''))
                or not record.get('restore_evidence')):
            raise ValueError('Verified copies require an asset path, URL, content hashes and restore evidence')
        if record['asset_path'] in verified:
            raise ValueError(f"Duplicate verified copy: {record['asset_path']}")
        verified[record['asset_path']] = record
    return verified, records


def restoration_view(entries, copies, copy_records, restoration_path):
    """Join evidence by exact path AND content; emit references, never private locators.

    Restoration proves a copy, not a move. Absence from this tree cannot prove a
    migration; that requires a separate, versioned migration event contract.
    """
    current = {e['path']: e['oid'] for e in entries if asset_kind(e['path'])}
    verified = []
    for i, record in enumerate(copy_records):
        path = record.get('asset_path')
        if path not in copies or record.get('state') != 'restore-verified' or path not in current:
            continue
        if current[path] != record['git_blob_sha1']:
            raise ValueError(f'External copy does not match this Git version: {path}')
        verified.append({**record, 'provider': 'drive', 'evidence_ref': f'copies.json#/copies/{i}'})
    if restoration_path.is_file():
        restoration = json.loads(restoration_path.read_text())
        if (restoration.get('schema') != 'urp.asset-restoration/1'
                or restoration.get('source_kind') != 'independent owner-held Space copies'):
            raise ValueError('Unsupported restoration evidence schema')
        for i, record in enumerate(restoration.get('current_report_assets', [])):
            path = record.get('asset_path')
            if path not in current:
                continue
            if (record.get('exact_path_blob_match') is not True
                    or record.get('git_blob_sha1') != current[path]
                    or record.get('expected_git_blob_sha1') != current[path]
                    or not re.fullmatch(r'[0-9a-f]{64}', record.get('sha256', ''))):
                raise ValueError(f'Restoration evidence does not match this Git version: {path}')
            verified.append({**record, 'provider': 'space',
                             'evidence_ref': f'restoration.json#/current_report_assets/{i}'})
    seen = set()
    for record in verified:
        identity = (record['provider'], record['asset_path'], record['git_blob_sha1'])
        if identity in seen:
            raise ValueError('Duplicate provider/content restoration evidence')
        seen.add(identity)
        if hashlib.sha256(git('cat-file', 'blob', record['git_blob_sha1'])).hexdigest() != record['sha256']:
            raise ValueError(f"External SHA256 does not match Git bytes: {record['asset_path']}")
    return verified


def index(ref, output, copies_path=None, restoration_path=None):
    commit = git('rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}').decode().strip()
    entries = tree(commit)
    copies, copy_records = verified_copies(copies_path or ROOT / 'docs/assets/copies.json')
    restorations = restoration_view(entries, copies, copy_records, restoration_path or ROOT / 'docs/assets/restoration.json')
    by_path = {}
    for evidence in restorations:
        by_path.setdefault(evidence['asset_path'], []).append(evidence)
    output.mkdir(parents=True, exist_ok=False)
    assets = []
    third_party = {}
    for item in entries:
        path = item['path']
        kind = asset_kind(path)
        if kind:
            evidence = by_path.get(path, [])
            assets.append({'path': path, 'kind': kind, 'git_blob_sha1': item['oid'],
                           'rights_status': 'review-required', 'external_copy': ';'.join(r['evidence_ref'] for r in evidence),
                           'external_sha256': evidence[0]['sha256'] if evidence else '',
                           'verified_providers': ';'.join(sorted({r['provider'] for r in evidence}))})
        parts = PurePosixPath(path).parts
        if len(parts) < 4 or parts[:2] != ('src', 'repos'):
            continue
        if parts[2] == 'notes':
            continue
        entry = third_party.setdefault(parts[2], {'path': '/'.join(parts[:3]), 'license_paths': [],
                                                  'license_labels': [], 'nested_license_count': 0})
        if parts[-1].lower().startswith(LICENSE_NAMES):
            if len(parts) == 4:
                entry['license_paths'].append(path)
                content = git('cat-file', 'blob', item['oid']).decode(errors='replace')
                entry['license_labels'].append(license_label(content))
            else:
                entry['nested_license_count'] += 1
    vendors = []
    for name, entry in sorted(third_party.items()):
        vendors.append({'name': name, 'path': entry['path'], 'root_license_paths': ';'.join(entry['license_paths']),
                        'license_text_labels': ';'.join(entry['license_labels']) or 'no-root-license-found',
                        'nested_license_count': entry['nested_license_count'],
                        'provenance_index': 'src/repos/REPOSITORIES.md', 'disposition': 'retain; review before extraction'})
    write_csv(output / 'assets.csv', ['path', 'kind', 'git_blob_sha1', 'rights_status', 'external_copy', 'external_sha256', 'verified_providers'], assets)
    write_csv(output / 'third-party.csv', ['name', 'path', 'root_license_paths', 'license_text_labels',
                                         'nested_license_count', 'provenance_index', 'disposition'], vendors)
    summary = {'source_commit': commit, 'tracked_entries': len(entries),
               'asset_counts': dict(sorted(Counter(row['kind'] for row in assets).items())),
               'third_party_directories': len(vendors),
               'without_root_license': [row['path'] for row in vendors if not row['root_license_paths']],
               'external_copy_candidates': len(copy_records),
               'external_copy_candidates_scope': 'copies.json (Drive registry only)', 'verified_external_copies': len(restorations),
               'verified_current_assets': len(by_path),
               'verified_external_copies_by_provider': {p: sum(r['provider'] == p for r in restorations) for p in ('drive', 'space')},
               'migrated_assets': 0,
               'known_migration_events': 0,
               'migration_event_schema_supported': False,
               'migration_status': 'No registered migration events in these schemas; migrated_assets counts known events only, not an audit of all historical moves. Restoration and absent paths do not prove migration',
               'root_license_present': any(row['path'].lower() in ('license', 'license.md', 'license.txt') for row in entries),
               'identity': 'Git blob SHA1 identifies repository content; external SHA256 is verified against Git bytes on restoration, not migration; external_copy contains evidence references, not private locators.'}
    (output / 'inventory.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--output', type=Path, required=True, help='New inventory directory')
    parser.add_argument('--copies', type=Path, help='Copy registry; defaults to docs/assets/copies.json')
    parser.add_argument('--restoration', type=Path, help='Restoration evidence; defaults to docs/assets/restoration.json')
    args = parser.parse_args()
    index(args.ref, args.output, args.copies, args.restoration)


if __name__ == '__main__':
    main()
