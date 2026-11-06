#!/usr/bin/env python3
"""Read-only status of the evolving-decision work: git, evolve runs, running jobs and pending items.

Usage: python3 .claude/skills/agri-evolving-decision/scripts/evolve_status.py [--repo PATH]
Standard library only; never writes anything.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

USER_OWNED = ('literature/', 'papers/补充/', 'src/lab/.gitignore', '停止实验台.bat', '启动实验台.bat')
SUMMARIES = ('audit/audit.json', 'policy/policy.json', 'verdict_comparison.json', 'policy_ext.json', 'exec_model.json',
             'summary.json', 'analysis.json', 'admission/admission_rules.json', 'admission/admission.json', 'fillet/summary.json')


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ''


def count_json(d: Path, skip=('summary.json', 'sensitivity.json')) -> int:
    if not d.is_dir():
        return 0
    with os.scandir(d) as it:
        return sum(1 for e in it if e.name.endswith('.json') and e.name not in skip)


def plan_jobs(run: Path) -> int | None:
    p = run / 'plan.json'
    if not p.is_file():
        return None
    try:
        return len(json.loads(p.read_text(encoding='utf-8')).get('jobs', []))
    except (OSError, ValueError):
        return None


def section(title: str) -> None:
    print(f'\n== {title} ==')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=None, help='repository root (default: inferred from this file or the current directory)')
    args = ap.parse_args()
    here = Path(__file__).resolve()
    repo = Path(args.repo) if args.repo else (here.parents[4] if len(here.parents) > 4 and (here.parents[4] / '.git').exists()
                                             else Path(git(Path.cwd(), 'rev-parse', '--show-toplevel') or '.'))

    section('Git')
    branch = git(repo, 'branch', '--show-current')
    print(f'repo   {repo}')
    print(f'branch {branch}   HEAD {git(repo, "log", "-1", "--format=%h %ad %s", "--date=format:%m-%d %H:%M")}')
    ab = git(repo, 'rev-list', '--left-right', '--count', f'main...{branch}') if branch else ''
    if ab:
        behind, ahead = ab.split()
        print(f'vs main: ahead {ahead}, behind {behind}')
    upstream = git(repo, 'rev-parse', '--abbrev-ref', f'{branch}@{{upstream}}') if branch else ''
    print(f'upstream: {upstream or "none (not pushed)"}')
    status = [l for l in git(repo, 'status', '--porcelain').splitlines() if l]
    path_of = lambda l: l[3:].strip('"')
    unquoted = [(l[:2], bytes(path_of(l), 'utf-8').decode('unicode_escape').encode('latin-1').decode('utf-8', 'replace')
                 if '\\' in path_of(l) else path_of(l)) for l in status]
    user = [p for _, p in unquoted if p.startswith(USER_OWNED)]
    other = [(s, p) for s, p in unquoted if not p.startswith(USER_OWNED)]
    print(f'uncommitted: {len(other)} paths (+{len(user)} user-owned, leave untouched)')
    for s, p in other[:25]:
        print(f'  {s} {p}')
    if len(other) > 25:
        print(f'  … {len(other) - 25} more')
    section('Recent commits')
    print(git(repo, 'log', '-8', '--format=%h %ad %s', '--date=format:%m-%d %H:%M'))

    section('Evolve runs (experiments/runs)')
    runs = sorted((repo / 'experiments' / 'runs').glob('evolve-*'))
    print(f'{"run":32s} {"plan":>6s} {"records":>8s} {"fillet":>7s} {"strict":>7s}  flags / summaries / README')
    for run in runs:
        jobs = plan_jobs(run)
        rec = count_json(run / 'records')
        fil = count_json(run / 'fillet')
        rev = count_json(run / 'reverify')
        flags = sorted(p.name for p in run.glob('*.done'))
        sums = [s for s in SUMMARIES if (run / s).is_file()]
        readme = run / 'README.md'
        pending = ''
        if readme.is_file():
            text = readme.read_text(encoding='utf-8')
            if '运行后补写' in text or re.search(r'运行中[；;，,。]', text):
                pending = 'README has pending sections'
        print(f'{run.name:32s} {jobs if jobs is not None else "—":>6} {rec:>8d} {fil:>7d} {rev:>7d}  '
              f'{" ".join(flags) or "-"} | {", ".join(sums) or "-"}{" | " + pending if pending else ""}')

    section('Running evolve processes')
    try:
        ps = subprocess.run(['ps', '-eo', 'pid,etime,args'], capture_output=True, text=True, timeout=10).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        ps = []
    procs = [l for l in ps if 'urp_lab.evolve' in l or 'from urp_lab.evolve' in l]
    print('\n'.join(l[:200] for l in procs[:12]) or 'none')
    try:
        print('load average:', ' '.join(f'{x:.1f}' for x in os.getloadavg()))
    except OSError:
        pass

    section('Desktop progress file')
    desk = Path.home() / 'Desktop'
    logs = sorted(desk.glob('农业机器人路径规划工作台-第*轮进度.md'), key=lambda p: p.stat().st_mtime)
    if logs:
        latest = logs[-1]
        print(latest)
        todo = [l.strip() for l in latest.read_text(encoding='utf-8').splitlines() if l.lstrip().startswith(('- ⏳', '- ⬜'))]
        print('\n'.join(t[:180] for t in todo) or 'no open items')
    prompt = desk / '农业机器人路径规划工作台-接续提示词.md'
    if prompt.is_file():
        print(f'handoff prompt: {prompt}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
