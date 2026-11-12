"""Round-2 figures (8–10): verdicts, admission rules by library capacity, and re-planning value.

Reuses the conventions of ``ceres_lab.figures.style`` and ``evolve.figures._save``: Elsevier width, embedded
TrueType, Okabe–Ito colours with marker/dash redundancy, GB/T 3102 axis labels, Chinese and English versions as
PDF, SVG and 600 dpi PNG, source-data CSV and bilingual captions appended to the run's ``figures/index.json``.
Figures 1–7 are never rewritten. Every caption names its denominator, verdict and that results are development-side.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..figures.style import Style, apply, color, dash, figure, fit_height, marker, panel_label, qlabel
from . import terms
from .figures import _save

RULE_LABEL = {'R1_single': ('R1 单一成员', 'R1 single member'), 'R2_vbs': ('R2 虚拟最优配置增量', 'R2 VBS increment'),
              'R3_policy': ('R3 策略收益', 'R3 policy gain'), 'R4_residual': ('R4 策略残差', 'R4 policy residual'),
              'random': ('随机', 'Random')}
DENSITY_LABEL = terms.RATE
POLICY_LABEL = terms.STRATEGY


def three_verdicts(vc: dict, st: Style):
    """Figure 8: official / fillet / strict feasible counts by machine profile."""
    profs = [p for p in ('robot_s', 'tractor_m', 'sprayer_l') if p in vc['by_profile']]
    fig, axs = figure(st, 0.52)
    ax = axs[0][0]
    x = np.arange(len(profs))
    width = 0.26
    keys = (('official', 0, ''), ('fillet', 2, '///'), ('strict', 1, '...'))
    rows = []
    for k, (key, ci, hatch) in enumerate(keys):
        vals = [vc['by_profile'][p][key] for p in profs]
        official = [vc['by_profile'][p]['official'] for p in profs]
        ax.bar(x + (k - 1) * width, vals, width * 0.92, color=color(ci), hatch=hatch, edgecolor='white', lw=0,
               label=st.t(*terms.VERDICT[key]))
        for xi, v, o in zip(x, vals, official):
            ax.text(xi + (k - 1) * width, v + 6, f'{v}\n({100 * v / o:.0f}%)', ha='center', va='bottom', fontsize=st.fs - 2,
                    linespacing=0.9)
        rows += [{'profile': p, 'verdict': key, 'feasible': v, 'official_denominator': o}
                 for p, v, o in zip(profs, vals, official)]
    ax.set_xticks(x, [st.t(*terms.PROFILE[p]) for p in profs])
    ax.set_ylabel(qlabel(st, '通过检验的原生可行路径', 'Natively feasible paths passing the check', '条'))
    ax.set_ylim(0, max(vc['by_profile'][p]['official'] for p in profs) * 1.22)
    ax.grid(axis='y')
    ax.legend(loc='upper right', ncols=3)
    tot = vc['total']
    cap = {'zh': f"三种可执行性检验下的可行路径数（开发集：荷兰 140 块田 × 3 机型；{tot['official']} 条原生可行路径为分母）。"
                 f"原生曲率检验只在原始采样点检查曲率；圆角可执行性检验要求拐角能用最小转弯半径圆弧在相邻直段内倒圆、且倒圆后通过未修改的原生检验"
                 f"（偏离容差 1 cm，0.5/2 cm 结果相同）；加密曲率检验把每条弦加密到 2 cm。柱顶为条数与占该机型原生可行数的比例；"
                 f"圆角检验另有 {tot['fillet_unverifiable']} 条因重跑无法再现记录路径而无法核验，按不可行计入。",
           'en': f"Feasible paths under three executability checks (development set: 140 Dutch fields × 3 machine profiles; "
                 f"{tot['official']} natively feasible paths as denominator). Native: sampled-point curvature only; fillet: every corner must be "
                 f"roundable by a minimum-radius arc inside its adjacent long chords and the rounded path must pass the unchanged native check "
                 f"(1 cm deviation tolerance; 0.5/2 cm identical); densified: every chord densified to 2 cm. Labels give counts and share of the "
                 f"profile's natively feasible paths; {tot['fillet_unverifiable']} fillet-unverifiable paths (hash not reproduced) count as infeasible."}
    return fig, cap, rows


def admission_rules(adm: dict, st: Style):
    """Figure 9: first-try feasibility and stop cost ratio by admission rule and capacity, two verdicts."""
    verdicts = [v for v in ('official', 'fillet') if v in adm]
    fig, axs = figure(st, 0.62, nrows=2, ncols=2)
    rows = []
    rules = list(RULE_LABEL)
    caps = [1, 2, 4]
    for r, v in enumerate(verdicts):
        a, b = axs[r]
        for rule in rules:
            xs, ys, lo, hi = [], [], [], []
            for m in caps:
                cell = adm[v]['rules'].get(f'{rule}@{m}')
                if not cell:
                    continue
                mean, ci = cell['static_first_try']
                xs.append(m), ys.append(100 * mean), lo.append(100 * (mean - ci[0])), hi.append(100 * (ci[1] - mean))
            ax = a
            ax.errorbar(xs, ys, yerr=[lo, hi], color=color(rules.index(rule)), marker=marker(rules.index(rule)),
                        ls=dash(rules.index(rule)), lw=0.9, capsize=1.5, label=st.t(*RULE_LABEL[rule]))
            rows += [{'verdict': v, 'metric': 'first_try', 'rule': rule, 'capacity': m, 'mean': y, 'lo': y - l, 'hi': y + h}
                     for m, y, l, h in zip(xs, ys, lo, hi)]
            xs, ys, lo, hi = [], [], [], []
            for m in caps:
                cell = adm[v]['rules'].get(f'{rule}@{m}')
                if not cell:
                    continue
                mean, ci = cell['static_stop_rho']
                xs.append(m), ys.append(100 * (mean - 1)), lo.append(100 * (mean - ci[0])), hi.append(100 * (ci[1] - mean))
            b.errorbar(xs, ys, yerr=[lo, hi], color=color(rules.index(rule)), marker=marker(rules.index(rule)),
                       ls=dash(rules.index(rule)), lw=0.9, capsize=1.5)
            rows += [{'verdict': v, 'metric': 'stop_rho_excess', 'rule': rule, 'capacity': m, 'mean': y,
                      'lo': y - l, 'hi': y + h} for m, y, l, h in zip(xs, ys, lo, hi)]
        ctrl = adm[v]['control_L0']
        m0, c0 = ctrl['first_try']
        a.axhline(100 * m0, color='#555555', ls=':', lw=0.8)
        a.fill_between([0.8, 4.2], 100 * c0[0], 100 * c0[1], color='#999999', alpha=0.12, lw=0)
        m0, c0 = ctrl['stop_rho']
        b.axhline(100 * (m0 - 1), color='#555555', ls=':', lw=0.8)
        b.fill_between([0.8, 4.2], 100 * (c0[0] - 1), 100 * (c0[1] - 1), color='#999999', alpha=0.12, lw=0)
        a.set_ylabel(qlabel(st, terms.METRIC['first_try'][0], terms.METRIC['first_try'][1], '%'))
        b.set_ylabel(st.t(f"{terms.METRIC['stop_rho'][0]} − 1", f"{terms.METRIC['stop_rho'][1]} − 1"))
        for ax in (a, b):
            ax.set_xticks(caps)
            ax.set_xlim(0.7, 4.3)
            ax.set_xlabel(st.t('库容量 m（新增成员数）', 'Library capacity m (added members)'))
            ax.grid()
        a.set_ylim(min(80, min(r['mean'] for r in rows if r['metric'] == 'first_try') - 4), 102)
        panel_label(a, 'ac'[r], st)
        panel_label(b, 'bd'[r], st)
    axs[0][0].legend(loc='lower right', ncols=2, fontsize=st.fs - 1.5)
    cap = {'zh': '准入规则 × 库容量（开发集，留出田块）。上排为首次尝试可行率，下排为终止成本比减 1；左列为原生曲率检验，右列为圆角可执行性检验。'
                 '每条规则只用外层训练田块选择成员，评价在外层测试田块；误差线为按田块自助法 95% 区间，灰色点线为旧库（L0）单独使用时的同一指标及其区间。'
                 '容量 m 为在 L0 之上新增的成员数；随机规则每个划分抽 20 次。',
           'en': 'Admission rule × library capacity (development set, held-out fields). Top: first-try feasibility; bottom: penalised cost ratio at stop minus 1. '
                 'Left: native curvature check; right: fillet executability check. Each rule selects members using outer-training fields only and is evaluated on outer-test fields; '
                 'error bars are field-bootstrap 95 % intervals; grey dotted lines are the old library (L0) alone with its interval. Capacity m counts members added '
                 'to L0; the random rule draws 20 candidate sets per split.'}
    return fig, cap, rows


def dynamic_value(summary: dict, st: Style):
    """Figure 10: distance and mean-response change versus insertion by density, policy and fleet size."""
    cells = summary['cells']
    densities = [d for d in ('sparse', 'medium', 'dense') if any(k.startswith(d) for k in cells)]
    fleets = sorted({int(k.split('@k')[1]) for k in cells})
    policies = ['periodic3', 'reopt', 'rollout']
    fig, axs = figure(st, 0.55, ncols=2)
    a, b = axs[0]
    rows = []
    for pi, pol in enumerate(policies):
        for fi, k in enumerate(fleets):
            xs, ys, lo, hi = [], [], [], []
            for di, d in enumerate(densities):
                cell = cells.get(f'{d}@k{k}')
                if not cell:
                    continue
                v = cell[f'{pol}_vs_insert_distance_m']
                xs.append(di), ys.append(100 * v['mean_rel']), lo.append(100 * (v['mean_rel'] - v['ci95'][0])), hi.append(100 * (v['ci95'][1] - v['mean_rel']))
            a.errorbar(xs, ys, yerr=[lo, hi], color=color(pi), marker=marker(pi), ls=dash(fi), lw=0.9, capsize=1.5,
                       markerfacecolor=color(pi) if fi == 0 else 'white',
                       label=st.t(f"{POLICY_LABEL[pol][0]} · {k} 台", f"{POLICY_LABEL[pol][1]}, {k} vehicles") if xs else None)
            rows += [{'density': d, 'fleet': k, 'policy': pol, 'metric': 'distance_m', 'mean_rel': y / 100,
                      'lo': (y - l) / 100, 'hi': (y + h) / 100} for d, y, l, h in zip(densities, ys, lo, hi)]
            xs, ys, lo, hi = [], [], [], []
            for di, d in enumerate(densities):
                cell = cells.get(f'{d}@k{k}')
                if not cell:
                    continue
                v = cell[f'{pol}_vs_insert_mean_response_s']
                xs.append(di), ys.append(100 * v['mean_rel']), lo.append(100 * (v['mean_rel'] - v['ci95'][0])), hi.append(100 * (v['ci95'][1] - v['mean_rel']))
            b.errorbar(xs, ys, yerr=[lo, hi], color=color(pi), marker=marker(pi), ls=dash(fi), lw=0.9, capsize=1.5,
                       markerfacecolor=color(pi) if fi == 0 else 'white')
            rows += [{'density': d, 'fleet': k, 'policy': pol, 'metric': 'mean_response_s', 'mean_rel': y / 100,
                      'lo': (y - l) / 100, 'hi': (y + h) / 100} for d, y, l, h in zip(densities, ys, lo, hi)]
    for ax in (a, b):
        ax.axhline(0, color='#555555', lw=0.6, ls=':')
        ax.set_xticks(range(len(densities)), [st.t(*DENSITY_LABEL[d]) for d in densities])
        ax.set_xlabel(st.t('到达密度（班次 3600/1200/400 s）', 'Arrival density (shift 3600/1200/400 s)'))
        ax.grid()
    a.set_ylabel(qlabel(st, f"{terms.METRIC['distance'][0]}相对增量插入", f"{terms.METRIC['distance'][1]} vs insertion", '%'))
    b.set_ylabel(qlabel(st, f"{terms.METRIC['response'][0]}相对增量插入", f"{terms.METRIC['response'][1]} vs insertion", '%'))
    panel_label(a, 'a', st)
    panel_label(b, 'b', st)
    a.legend(loc='lower left', ncols=2, fontsize=st.fs - 1.5)
    n = sum(c['n'] for c in cells.values())
    cap = {'zh': f"动态任务流中重优化策略相对增量插入的总行驶里程与平均响应时间变化（开发集，规划层仿真；{n} 条流，30 块田 × 3 到达密度 × 2 车队规模）。"
                 f"负值更好；误差线为按流自助法 95% 区间。实心标记为 2 台、空心为 4 台；每流 40 个任务、其中 30 个在班次内到达，"
                 f"重优化为 PyVRP 0.3 s 开放路线优化。计时使用恒速模型。",
           'en': f"Change in total travel distance and mean response time of re-optimisation policies versus incremental insertion in dynamic task streams "
                 f"(development set, planning-level simulation; {n} streams, 30 fields × 3 arrival densities × 2 fleet sizes). Lower is better; "
                 f"error bars are stream bootstrap 95 % intervals. Filled markers: 2 vehicles; open: 4. Each stream has 40 tasks, 30 released during the shift; "
                 f"re-optimisation is a 0.3 s open-route PyVRP solve. Timing uses the constant-speed model."}
    return fig, cap, rows


def repair_versions(counts: dict, st: Style):
    """Figure 11: feasible paths of repair v1 and v2 under the three executability checks."""
    versions = list(counts)
    checks = ('official', 'fillet', 'strict')
    fig, axs = figure(st, 0.5)
    ax = axs[0][0]
    x = np.arange(len(versions))
    width = 0.26
    rows = []
    for k, key in enumerate(checks):
        vals = [counts[v][key] for v in versions]
        ax.bar(x + (k - 1) * width, vals, width * 0.92, color=color([0, 2, 1][k]), hatch=['', '///', '...'][k],
               edgecolor='white', lw=0, label=st.t(*terms.VERDICT[key]))
        for xi, val in zip(x, vals):
            ax.text(xi + (k - 1) * width, val + 15, f'{val}', ha='center', va='bottom', fontsize=st.fs - 1)
        rows += [{'version': v, 'check': key, 'feasible': val} for v, val in zip(versions, vals)]
    ax.set_xticks(x, [st.t('修补 v1', 'Repair v1') if 'v1' in v else st.t('修补 v2', 'Repair v2') for v in versions])
    ax.set_ylabel(qlabel(st, '通过检验的可行路径', 'Feasible paths passing the check', '条'))
    ax.set_ylim(0, max(max(counts[v].values()) for v in versions) * 1.2)
    ax.grid(axis='y')
    ax.legend(loc='upper right', ncols=3)
    cap = {'zh': '修补 v1 与 v2 的可行路径数（开发集：388 实例；每根柱的分母为该版本在原生曲率检验下的可行路径数）。'
                 '原生曲率检验为未修改的检查器；圆角可执行性检验要求拐角可用最小转弯半径倒圆并通过原生检验；加密曲率检验把每条弦加密到 2 cm。'
                 'v1 的加密值来自独立重验证（记录路径）；v2 返回的已是圆角后路径，其加密值由圆角判定批次导出。',
           'en': 'Feasible paths of repair v1 and v2 (development set: 388 instances; each bar is relative to that version\'s native-check feasible paths). '
                 'Native: unchanged checker; fillet: corners rounded by the minimum turning radius and the rounded path passing the native check; densified: every chord densified to 2 cm. '
                 'The v1 densified value comes from the independent reverify batch (recorded path); v2 returns the rounded path, so its densified value is derived from the fillet batch.'}
    return fig, cap, rows


def _run_counts(run_dir: Path) -> dict:
    """official / fillet / strict feasible paths of one repair run."""
    summary = json.loads((run_dir / 'fillet' / 'summary.json').read_text(encoding='utf-8'))
    official = int(summary['jobs'])
    fillet = sum(int(v.get('valid', 0)) for v in summary['by_config'].values())
    strict = 0
    for p in (run_dir / 'reverify').glob('*.json'):
        if p.name == 'sensitivity.json':
            continue
        r = json.loads(p.read_text(encoding='utf-8'))
        strict += int(bool(r.get('identical') and r.get('strict_valid_curvature')))
    return {'official': official, 'fillet': fillet, 'strict': strict}


def write(out: Path) -> list[str]:
    out = Path(out)
    target = out / 'figures'
    target.mkdir(exist_ok=True)
    index_path = target / 'index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else []
    keys = ('fig8_three_verdicts', 'fig9_admission_rules', 'fig10_dynamic_value', 'fig11_repair_versions')
    index = [e for e in index if e.get('key') not in keys]
    produced = []
    vc_path = out / 'verdict_comparison.json'
    adm_path = out.parent / 'evolve-candidates-20261024' / 'admission' / 'admission_rules.json'
    dyn_path = out.parent / 'evolve-dynamic-20261024' / 'summary.json'
    vc = json.loads(vc_path.read_text(encoding='utf-8')) if vc_path.exists() else None
    adm = json.loads(adm_path.read_text(encoding='utf-8')) if adm_path.exists() else None
    dyn = json.loads(dyn_path.read_text(encoding='utf-8')) if dyn_path.exists() else None
    rep_dirs = {'v1': out.parent / 'evolve-repair-20261023', 'v2': out.parent / 'evolve-repair2-20261024'}
    counts = {}
    for version, path in rep_dirs.items():
        if (path / 'fillet' / 'summary.json').exists() and any((path / 'reverify').glob('*.json')):
            counts[version] = _run_counts(path)
    for lang in ('zh', 'en'):
        if vc:
            st = Style('elsevier', 'single', lang)
            with apply(st):
                fig, cap, rows = three_verdicts(vc, st)
                _save(fig, target, 'fig8_three_verdicts', st, cap, rows, index)
                produced.append(f'fig8 [{lang}]')
        if adm:
            st = Style('elsevier', 'double', lang)
            with apply(st):
                fig, cap, rows = admission_rules(adm, st)
                _save(fig, target, 'fig9_admission_rules', st, cap, rows, index)
                produced.append(f'fig9 [{lang}]')
        if dyn:
            st = Style('elsevier', 'double', lang)
            with apply(st):
                fig, cap, rows = dynamic_value(dyn, st)
                _save(fig, target, 'fig10_dynamic_value', st, cap, rows, index)
                produced.append(f'fig10 [{lang}]')
        if len(counts) == 2:
            st = Style('elsevier', 'single', lang)
            with apply(st):
                fig, cap, rows = repair_versions(counts, st)
                _save(fig, target, 'fig11_repair_versions', st, cap, rows, index)
                produced.append(f'fig11 [{lang}]')
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding='utf-8')
    return produced
