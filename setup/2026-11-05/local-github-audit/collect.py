#!/usr/bin/env python3
"""Read-only URP storage audit. Outputs metadata and hashes, never file contents.

Run from the repository root; pass a new absolute --output directory and the
SHA256-pinned October preservation map. No upload, deletion, checkout, service
startup, credential read, environment install, or lazy Git object download.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path.cwd().resolve()
GIT = ['git', '-c', 'gc.auto=0']
PRIVATE = {'.aws', '.agents', '.codex', '.v2c', '.video_agent', '.workbuddy'}
DEPENDENCIES = {'.git', '.venv', 'venv', 'node_modules'}
CACHES = {'__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache'}
BUILD = ('src/desktop/dist', 'src/desktop/build/backend', 'src/desktop/build/python-cache', 'src/desktop/build/packs', 'src/frontend/dist', 'baseline-worker/env')


def run(args):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=True).stdout


def git(*args):
    return run(GIT + list(args))


def digest(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(4 << 20), b''):
            h.update(b)
    return h.hexdigest()


def save(out, name, data):
    (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def role(rel):
    if rel.startswith('src/lab/runtime/') or rel.startswith('.local-state/'):
        return 'local-runtime'
    if rel.startswith('baseline-worker/'):
        return 'independent-research-workspace'
    if rel.startswith('setup/'):
        return 'local-acceptance-evidence'
    if rel.startswith('experiments/'):
        return 'research-record'
    return 'other-local'


def disk(path):
    p = Path(path)
    if not p.exists():
        return {'path': str(p), 'exists': False}
    r = subprocess.run(['du', '-s', '-B1', str(p)], text=True, capture_output=True)
    return {'path': str(p), 'exists': True, 'allocated_bytes': int(r.stdout.split()[0]) if r.stdout else None, 'du_exit': r.returncode}


def database(p):
    item = {'path': str(p), 'bytes': p.stat().st_size}
    try:
        with sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True) as source:
            with tempfile.TemporaryDirectory(prefix='urp-audit-db-') as d:
                target = Path(d) / 'consistent.sqlite3'
                with sqlite3.connect(target) as dest:
                    source.backup(dest)
                    tables = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                    item['tables'] = {t: dest.execute('SELECT count(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0] for t in tables}
                    item['quick_check'] = dest.execute('PRAGMA quick_check').fetchone()[0]
                    item['user_version'] = dest.execute('PRAGMA user_version').fetchone()[0]
                item['consistent_backup_sha256'] = digest(target)
    except Exception as e:
        item['error_type'] = type(e).__name__
    return item


def collect(out, map_path, github):
    initial = git('status', '--short', '--branch')
    tracked = {}
    for row in git('ls-files', '-s', '-z').split('\0'):
        if row:
            meta, path = row.split('\t', 1)
            mode, oid, stage = meta.split()
            tracked[path] = {'git_mode': mode, 'git_blob': oid}
    ignored = set(git('ls-files', '--others', '--ignored', '--exclude-standard', '-z').split('\0'))
    index = json.loads((ROOT / 'setup/2026-10-26/local-preservation/recovery-index.json').read_text())
    assert map_path.stat().st_size == index['map']['bytes']
    assert digest(map_path) == index['map']['sha256']
    with gzip.open(map_path, 'rt') as f:
        mapping = {d['path']: d for d in map(json.loads, f)}
    aggregates = defaultdict(lambda: {'files': 0, 'bytes': 0})
    local = []
    skipped = []
    links = []
    largest_tracked = []
    unstable = []
    for base, dirs, names in os.walk(ROOT, followlinks=False):
        parent = Path(base)
        for name in dirs[:]:
            p = parent / name
            rel = p.relative_to(ROOT).as_posix()
            if p.is_symlink():
                links.append({'path': rel, 'target': os.readlink(p), 'exists': p.exists(), 'tracked': rel in tracked})
                dirs.remove(name)
            elif name in PRIVATE or name in DEPENDENCIES or name in CACHES or name.startswith('.venv-') or rel in BUILD or rel == 'setup/2026-11-05/local-github-audit':
                skipped.append(rel)
                dirs.remove(name)
        for name in names:
            p = parent / name
            rel = p.relative_to(ROOT).as_posix()
            if p.is_symlink():
                links.append({'path': rel, 'target': os.readlink(p), 'exists': p.exists(), 'tracked': rel in tracked})
                continue
            try:
                s = p.stat()
            except FileNotFoundError:
                unstable.append(rel)
                continue
            if not stat.S_ISREG(s.st_mode):
                continue
            state = 'tracked' if rel in tracked else 'ignored' if rel in ignored else 'untracked'
            group = rel.split('/')[0]
            agg = aggregates[(group, state)]
            agg['files'] += 1
            agg['bytes'] += s.st_size
            if state == 'tracked':
                if s.st_size >= 20 << 20:
                    largest_tracked.append({'path': rel, 'bytes': s.st_size, **tracked[rel]})
                continue
            row = {'path': rel, 'bytes': s.st_size, 'mtime_ns': s.st_mtime_ns, 'git_state': state, 'role': role(rel)}
            if name in {'credentials.json', 'llm.json'} or name.startswith('.env'):
                row['coverage'] = 'private-not-read'
            else:
                row['sha256'] = digest(p)
                prior = mapping.get('project/' + rel)
                if prior is None:
                    row['coverage'] = 'no-october-preservation-entry'
                elif row['sha256'] != prior.get('source_sha256'):
                    row['coverage'] = 'changed-since-october-preservation'
                else:
                    row['coverage'] = 'same-source-hash-as-october-preservation'
                    row['backup_kind'] = prior['backup']
                s2 = p.stat()
                if (s.st_size, s.st_mtime_ns) != (s2.st_size, s2.st_mtime_ns):
                    row['changed_during_read'] = True
            local.append(row)
    summary = {'schema': 'urp.local-github-audit/1', 'generated_utc': datetime.now(timezone.utc).isoformat(), 'client_date': '2026-11-05',
               'scope': 'metadata and hashes; secrets not read; symlinks not followed; skipped environments/build outputs not included in per-file totals',
               'git_head': git('rev-parse', 'HEAD').strip(), 'git_branch': git('branch', '--show-current').strip(), 'initial_status': initial,
               'main': git('rev-parse', 'origin/main').strip(), 'divergence_main_head': git('rev-list', '--left-right', '--count', 'origin/main...HEAD').strip(),
               'unpublished_local_commits': git('log', '--branches', '--not', '--remotes', '--oneline').splitlines(),
               'aggregates': [{'root': a, 'git_state': b, **v} for (a, b), v in sorted(aggregates.items())],
               'local_untracked_or_ignored': {'files': len(local), 'bytes': sum(r['bytes'] for r in local),
                   'coverage': [{ 'state': k, 'files': sum(r['coverage'] == k for r in local), 'bytes': sum(r['bytes'] for r in local if r['coverage'] == k)} for k in sorted({r['coverage'] for r in local})]},
               'skipped_directories': skipped, 'broken_links': [r for r in links if not r['exists']], 'tracked_links': [r for r in links if r['tracked']],
               'largest_tracked': sorted(largest_tracked, key=lambda x: -x['bytes']), 'vanished_paths': unstable,
               'october_map': {'bytes': map_path.stat().st_size, 'sha256': digest(map_path), 'records': len(mapping)}}
    summary['disk'] = [disk(ROOT / p) for p in ['.', '.git', 'src/desktop', 'src/desktop/dist', 'src/desktop/build/backend', 'src/desktop/build/packs', 'src/lab/runtime', '.local-state', 'papers', 'literature', 'experiments', 'data', 'baseline-worker']]
    summary['host'] = [disk(p) for p in ['/opt/ros', '/home/peakpeng/.ignition', '/home/peakpeng/.ros', '/home/peakpeng/.ollama', '/home/peakpeng/.local/share/uv/python']]
    summary['databases'] = [database(ROOT / p) for p in ['src/lab/runtime/lab.sqlite3', 'src/lab/runtime/agent.sqlite3', '.local-state/research.sqlite3', 'data/backend/research.sqlite3']]
    pack = ROOT / 'src/desktop/build/packs/history-1.zip'
    spec = json.loads((ROOT / 'src/lab/urp_lab/data/packs.json').read_text())['packs'][0]
    with zipfile.ZipFile(pack) as z:
        summary['history_pack'] = {'bytes': pack.stat().st_size, 'sha256': digest(pack), 'sha256_matches_manifest': digest(pack) == spec['sha256'], 'members': len(z.infolist()), 'unpacked_bytes': sum(x.file_size for x in z.infolist()), 'first_crc_error': z.testzip()}
    with gzip.open(out / 'local-data-files.jsonl.gz', 'wt', encoding='utf-8') as f:
        for row in sorted(local, key=lambda x: x['path']):
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    if github:
        releases = json.loads(run(['gh', 'api', 'repos/peakpeng666/URP/releases', '--paginate']))
        clean_releases = [{'tag': r['tag_name'], 'target': r['target_commitish'], 'published_at': r['published_at'], 'url': r['html_url'], 'assets': [{'name': a['name'], 'bytes': a['size'], 'digest': a.get('digest'), 'url': a['browser_download_url']} for a in r['assets']]} for r in releases]
        save(out, 'github-releases.json', clean_releases)
        summary['releases'] = [{'tag': r['tag'], 'assets': len(r['assets']), 'bytes': sum(a['bytes'] for a in r['assets'])} for r in clean_releases]
        actual = {(r['tag'], a['name']): a for r in clean_releases for a in r['assets']}
        catalog = json.loads((ROOT / 'setup/platform-snapshot.json').read_text())
        pins = [dict(a, release_tag=catalog['release_tag']) for a in catalog['assets']] + index['assets'] + [index['map']]
        mismatches = []
        for pin in pins:
            a = actual.get((pin['release_tag'], pin['name']))
            if a is None or a['bytes'] != pin['bytes'] or a['digest'] != 'sha256:' + pin['sha256']:
                mismatches.append({'tag': pin['release_tag'], 'name': pin['name'], 'remote': a})
        summary['release_pin_check'] = {'pins_checked': len(pins), 'mismatches': mismatches, 'scope': 'API size and server SHA256; archive payloads not re-downloaded'}
        save(out, 'remote-refs.txt.json', git('ls-remote', 'origin', 'refs/heads/*', 'refs/tags/*').splitlines())
        runs = json.loads(run(['gh', 'api', 'repos/peakpeng666/URP/actions/runs?head_sha=' + summary['git_head'] + '&per_page=30']))
        summary['current_commit_remote_workflows'] = {'total_count': runs['total_count'], 'runs': [{'name': r['name'], 'status': r['status'], 'conclusion': r['conclusion'], 'url': r['html_url']} for r in runs['workflow_runs']]}
    save(out, 'inventory.json', summary)
    print(json.dumps({'output': str(out), 'head': summary['git_head'], 'local_data': summary['local_untracked_or_ignored'], 'releases': summary.get('releases'), 'release_pins': summary.get('release_pin_check')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--preservation-map', type=Path, required=True)
    parser.add_argument('--github', action='store_true', help='Use gh for read-only GitHub API and git ls-remote')
    args = parser.parse_args()
    if not args.output.is_absolute():
        parser.error('--output must be a new absolute directory')
    args.output.mkdir(parents=True, exist_ok=False)
    collect(args.output, args.preservation_map, args.github)
