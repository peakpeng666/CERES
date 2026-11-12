"""Command line for the evolving decision loop (run from src/lab with the lab virtual environment).

    .venv/bin/python -m ceres_lab.evolve manifest --out FILE
    .venv/bin/python -m ceres_lab.evolve plan --out NEW_DIR [--families coverage,fleet] [--limit N]
    .venv/bin/python -m ceres_lab.evolve run --out DIR --workers 16 --cores 8-23
    .venv/bin/python -m ceres_lab.evolve audit --out DIR
    .venv/bin/python -m ceres_lab.evolve reverify --out DIR --workers 16 --cores 8-23
    .venv/bin/python -m ceres_lab.evolve fillet --out DIR --workers 16 --cores 8-23
    .venv/bin/python -m ceres_lab.evolve policy --out DIR
    .venv/bin/python -m ceres_lab.evolve experience --out FILE
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cores(text: str | None) -> list[int] | None:
    if not text:
        return None
    out = []
    for part in text.split(','):
        a, _, b = part.partition('-')
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ceres_lab.evolve')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('manifest')
    p.add_argument('--out', required=True)
    p = sub.add_parser('plan')
    p.add_argument('--out', required=True)
    p.add_argument('--families', default='coverage,fleet')
    p.add_argument('--limit', type=int, default=None, help='first N development fields (smoke tests)')
    p.add_argument('--run-id', default=None)
    p.add_argument('--note', default='')
    p = sub.add_parser('run')
    p.add_argument('--out', required=True)
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--cores', default=None)
    for name in ('audit', 'policy', 'figures'):
        p = sub.add_parser(name)
        p.add_argument('--out', required=True)
    p = sub.add_parser('reverify')
    p.add_argument('--out', required=True)
    p.add_argument('--workers', type=int, default=16)
    p.add_argument('--cores', default='8-23')
    p = sub.add_parser('fillet')
    p.add_argument('--out', required=True)
    p.add_argument('--workers', type=int, default=16)
    p.add_argument('--cores', default='8-23')
    p = sub.add_parser('experience')
    p.add_argument('--out', required=True)
    args = ap.parse_args(argv)

    if args.cmd == 'manifest':
        from . import instances
        doc = instances.manifest()
        Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(doc['counts'], ensure_ascii=False))
    elif args.cmd == 'plan':
        from . import instances, plans, sweep
        fields = plans.dev_fields()
        if args.limit:
            fields = fields[:args.limit]
        families = set(args.families.split(','))
        jobs, excluded = [], []
        if 'coverage' in families:
            cj, excluded = plans.coverage_plan(fields)
            jobs += cj
        if 'repair' in families:
            cj, excluded = plans.repair_plan(fields)
            jobs += cj
        if 'repair2' in families:
            cj, cex = plans.repair2_plan(fields)
            jobs += cj
            excluded = excluded or cex
        if 'fleet' in families:
            jobs += plans.fleet_plan(fields)
        if 'determinism' in families:
            runs = Path(__file__).resolve().parents[4] / 'experiments' / 'runs'
            jobs += plans.determinism_plan([runs / 'evolve-audit-20261023', runs / 'evolve-repair-20261023'])
        if 'candidates' in families:
            cj, cex = plans.candidate_plan(fields)
            jobs += cj
            excluded = excluded or cex
        out = Path(args.out)
        plan = {'run_id': args.run_id or out.name, 'created_utc': sweep.utc(), 'note': args.note,
                'purpose': ('运行间确定性研究（同一任务重复 5 次；开发侧）；封存测试田块不在计划内' if 'determinism' in families else
                            '准入依据研究候选池（修补窗口、分割角、倒圆输出；开发侧）；封存测试田块不在计划内' if 'candidates' in families else
                            '修补 v2（倒圆可见缺陷的局部修补 + 倒圆输出；开发侧）；封存测试田块不在计划内' if 'repair2' in families else
                            '回路②候选成员（局部 Dubins 修补）准入实验（开发侧）；封存测试田块不在计划内' if 'repair' in families else
                            '互补性审计（开发侧）：真实田块覆盖配置与多车算法组合；封存测试田块不在计划内'),
                'fields': fields, 'families': sorted(families), 'excluded': excluded, 'timeout_s': 300.0,
                'profiles': instances.PROFILES, 'coverage_portfolio': plans.COVERAGE_PORTFOLIO,
                'repair_portfolio': plans.REPAIR_PORTFOLIO if 'repair' in families else {},
                'repair2_portfolio': plans.REPAIR2_PORTFOLIO if 'repair2' in families else {},
                'candidate_pool': plans.CANDIDATE_POOL if 'candidates' in families else {},
                'fleet_portfolio': plans.FLEET_PORTFOLIO, 'workbench_default': plans.WORKBENCH_DEFAULT,
                'field_manifest_rule': instances.manifest()['rule'], 'code': sweep.code_identity(), 'jobs': jobs}
        digest = sweep.write_plan(out, plan)
        print(f'{len(jobs)} jobs, {len(excluded)} geometric exclusions, plan sha256 {digest}')
    elif args.cmd == 'run':
        from . import sweep
        out = Path(args.out)
        log_path = out / 'progress.log'

        def log(msg):
            print(msg, flush=True)
            with log_path.open('a', encoding='utf-8') as fh:
                fh.write(msg + '\n')
        counts = sweep.run(out, args.workers, _cores(args.cores), log)
        print(json.dumps(counts, ensure_ascii=False))
    elif args.cmd == 'audit':
        from . import audit
        print(json.dumps(audit.write(Path(args.out)), ensure_ascii=False, indent=1)[:4000])
    elif args.cmd == 'policy':
        from . import policy
        print(json.dumps(policy.write(Path(args.out)), ensure_ascii=False, indent=1)[:4000])
    elif args.cmd == 'figures':
        from . import figures
        print('\n'.join(figures.write(Path(args.out))))
    elif args.cmd == 'reverify':
        from . import reverify
        print(json.dumps(reverify.run(Path(args.out), args.workers, _cores(args.cores)), ensure_ascii=False, indent=1))
    elif args.cmd == 'fillet':
        from . import fillet
        print(json.dumps(fillet.run(Path(args.out), args.workers, _cores(args.cores)), ensure_ascii=False, indent=1))
    elif args.cmd == 'experience':
        from . import experience
        print(json.dumps(experience.write(Path(args.out)), ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
