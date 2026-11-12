"""Library evolution ledger: who is in the coverage library, where each member came from, and on what evidence.

The ledger turns the evolve studies into one machine-readable registry (``src/lab/models/library-ledger.json``) and
a readable table (``experiments/reports/进化决策/算法库进化账本.md``). For every member it records lineage (parent
member and the operation that produced it), the run that introduced it, whether the workbench can run it
(registered catalog algorithm vs research candidate), and its evidence per feasibility verdict on the common
development instances: feasible count with denominator, mean penalised cost ratio, Shapley contribution to the
per-instance-best portfolio (exact up to 20 members, otherwise permutation sampling with a fixed seed) and
whether it is ever the best. Evidence that is incomplete (e.g. a verdict batch still running) is marked as such
and never filled in. Status is derived from evidence and deployment facts only, never asserted.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import paths
from . import audit, plans
from .verdicts import dataset_from

RUNS = paths.REPO / 'experiments' / 'runs'
MODEL = paths.LAB / 'models' / 'library-ledger.json'
REPORT = paths.REPO / 'experiments' / 'reports' / '进化决策' / '算法库进化账本.md'
SOURCES = [('evolve-audit-20261023', None), ('evolve-repair-20261023', 'REPAIR_PORTFOLIO'),
           ('evolve-candidates-20261024', 'CANDIDATE_POOL'), ('evolve-repair2-20261024', 'REPAIR2_PORTFOLIO')]


def lineage(name: str) -> tuple[str | None, str]:
    """(parent member, operation) for a configuration name."""
    if name.startswith('repair2:'):
        return 'repair:' + name[8:], '修补 v2：倒圆判定可见缺陷的局部 Dubins 替换 + 倒圆'
    if name.startswith('repairw:'):
        return 'repair:' + name[8:].split('@')[0], '修补窗口变体'
    if name.startswith('repair:'):
        return 'full:' + name[7:], '修补 v1：官方检查可见曲率缺陷的局部 Dubins 替换'
    if name.startswith('split:'):
        return 'full:' + name[6:].split('@')[0], '分解分割角变体'
    if name.startswith('fillet:'):
        return 'full:' + name[7:], '倒圆输出后处理'
    return None, 'Fields2Cover 注册配置' if name.startswith(('full:', 'single:')) else '注册配置'


def _shapley(r: np.ndarray, penalty: float, samples: int = 4000, seed: int = 20261024) -> tuple[np.ndarray, str]:
    k = r.shape[1]
    if k <= 20:
        return audit.shapley(r, penalty), 'exact'
    rng = np.random.default_rng(seed)
    phi = np.zeros(k)
    for _ in range(samples):
        perm = rng.permutation(k)
        best = np.full(r.shape[0], penalty)
        prev = 0.0
        for j in perm:
            best = np.minimum(best, r[:, j])
            val = float((penalty - best).mean())
            phi[j] += val - prev
            prev = val
    return phi / samples, f'permutation sampling ({samples} permutations, seed {seed})'


def _available_sources():
    dirs, configs, introduced = [], [], {}
    for run, attr in SOURCES:
        d = RUNS / run
        if not (d / 'plan.json').is_file() or not any((d / 'records').glob('*.json')):
            continue
        names = list(plans.COVERAGE_PORTFOLIO) if attr is None else list(getattr(plans, attr, {}) or {})
        if not names:
            continue
        dirs.append(d)
        for n in names:
            if n not in introduced:
                introduced[n] = run
                configs.append(n)
    return dirs, configs, introduced


def _verdict_complete(dirs, configs, verdict):
    """Share of officially feasible jobs per config that carry the requested verdict."""
    from .sweep import load_records
    from .verdicts import verdict_map
    have, total = {}, {}
    for d in dirs:
        vm = verdict_map(d, verdict) if verdict != 'official' else None
        for r in load_records(d):
            if r['family'] != 'coverage' or r['config'] not in configs or r['status'] != 'feasible' or not r.get('valid'):
                continue
            total[r['config']] = total.get(r['config'], 0) + 1
            if vm is None or r['job_id'] in vm:
                have[r['config']] = have.get(r['config'], 0) + 1
    return {c: (have.get(c, 0) / total[c]) if total.get(c) else 1.0 for c in configs}


def build(verdicts=('official', 'fillet', 'strict')) -> dict:
    dirs, configs, introduced = _available_sources()
    model_path = paths.LAB / 'models' / 'coverage-order-v1.json'
    deployed_order = []
    if model_path.is_file():
        art = json.loads(model_path.read_text(encoding='utf-8'))['artifact']
        deployed_order = [art['configs'][j] for j in art['static_order']]
    from ..solvers import catalog
    registered = {a['id'] for a in catalog()}
    evidence = {}
    for v in verdicts:
        complete = _verdict_complete(dirs, configs, v)
        usable = [c for c in configs if complete[c] >= 0.999]
        if not usable:
            continue
        ds = dataset_from(dirs, usable, v)
        r, solvable = ds['rho'], ds['solvable']
        rs = r[solvable]
        phi, method = _shapley(rs, audit.PENALTY)
        best = rs <= 1 + 1e-9
        evidence[v] = {'instances': int(len(r)), 'solvable': int(solvable.sum()), 'shapley_method': method,
                       'unverifiable_cells': ds['unverifiable_cells'],
                       'incomplete': {c: round(complete[c], 4) for c in configs if complete[c] < 0.999},
                       'members': {c: {'feasible': int(np.isfinite(ds['m']['cost'][:, j]).sum()), 'mean_rho_penalised': float(rs[:, j].mean()),
                                       'shapley': float(phi[j]), 'best_on': int(best[:, j].sum())} for j, c in enumerate(usable)}}
    members = []
    for c in configs:
        alg, params = (plans.COVERAGE_PORTFOLIO.get(c) or getattr(plans, 'REPAIR_PORTFOLIO', {}).get(c)
                       or getattr(plans, 'CANDIDATE_POOL', {}).get(c) or getattr(plans, 'REPAIR2_PORTFOLIO', {}).get(c))
        parent, op = lineage(c)
        runnable = alg in registered
        ev = {v: evidence[v]['members'].get(c) for v in evidence}
        if c == plans.WORKBENCH_DEFAULT:
            status = '工作台现行默认'
        elif c in deployed_order and runnable:
            status = f'已上线：经验静态尝试顺序第 {deployed_order.index(c) + 1} 位'
        elif not runnable:
            positive = [v for v, e in ev.items() if e and e['shapley'] > 1e-3]
            status = ('研究候选：证据支持准入（' + '、'.join(positive) + ' 判定下 Shapley > 0），待负责方集成后才能上线') if positive \
                else '研究候选：证据不足'
        else:
            status = '注册配置'
        dominated = [v for v, e in ev.items() if e and e['best_on'] == 0 and e['shapley'] <= 1e-6]
        members.append({'id': c, 'label': plans.config_label(c), 'algorithm_id': alg, 'params': params, 'runnable_in_workbench': runnable,
                        'parent': parent, 'operation': op, 'introduced_in': introduced[c], 'status': status,
                        'dominated_under': dominated, 'evidence': ev})
    doc = {'schema': 'urp.library-ledger/1', 'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'scope': '覆盖配置；开发侧（荷兰 140 块田 × 3 机具）；封存田块未运行',
           'sources': [d.name for d in dirs], 'verdicts': {v: {k: e[k] for k in ('instances', 'solvable', 'shapley_method', 'unverifiable_cells', 'incomplete')}
                                                         for v, e in evidence.items()},
           'members': members}
    payload = json.dumps({k: doc[k] for k in ('sources', 'verdicts', 'members')}, sort_keys=True, ensure_ascii=False).encode()
    doc['content_sha256'] = hashlib.sha256(payload).hexdigest()
    return doc


def render(doc: dict) -> str:
    verdicts = list(doc['verdicts'])
    lines = ['# 算法库进化账本（覆盖配置，开发侧）', '',
             f"生成：{doc['generated_utc']} · 内容指纹 `{doc['content_sha256'][:16]}` · 来源运行：{', '.join(doc['sources'])}。由 `ceres_lab.evolve.ledger` 从运行记录自动生成，"
             '状态只由证据与部署事实推出；证据不完整的判定在表中留空并注明。封存田块未运行。', '',
             '每个判定下的实例数与可解数：' + '；'.join(f"{v} {e['solvable']}/{e['instances']}" + (f"（Shapley：{e['shapley_method']}）") +
                                              (f"，未完成：{len(e['incomplete'])} 个成员" if e['incomplete'] else '') for v, e in doc['verdicts'].items()), '',
             '| 成员 | 来历 | 引入 | 工作台可运行 | 状态 | ' + ' | '.join(f'{v} 可行 / Shapley' for v in verdicts) + ' |',
             '|---|---|---|---|---|' + '---|' * len(verdicts)]
    for m in doc['members']:
        cells = []
        for v in verdicts:
            e = m['evidence'].get(v)
            cells.append('—' if not e else f"{e['feasible']} / {e['shapley']:.3f}")
        parent = f"{plans.config_label(m['parent'])} → {m['operation']}" if m['parent'] else m['operation']
        lines.append(f"| {m['label']} | {parent} | {m['introduced_in']} | {'是' if m['runnable_in_workbench'] else '否'} | {m['status']} | " + ' | '.join(cells) + ' |')
    lines += ['', 'Shapley 值为对逐实例最好组合价值 v(S) = 平均[2 − min ρ] 的贡献（惩罚值 2）；成员多于 20 个时用排列抽样近似。'
              '各判定的 Shapley 只在该判定证据完整的成员集合内计算，跨判定比较时注意成员集合是否相同。「工作台可运行」为否的成员即使证据支持准入，也只能在负责方把对应处理集成进注册适配器后才能上线（接口需求第 16、17 项）。']
    return '\n'.join(lines) + '\n'


def write() -> dict:
    doc = build()
    MODEL.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(render(doc), encoding='utf-8')
    return {'members': len(doc['members']), 'sources': doc['sources'], 'verdicts': list(doc['verdicts']),
            'incomplete': {v: e['incomplete'] for v, e in doc['verdicts'].items() if e['incomplete']}}
