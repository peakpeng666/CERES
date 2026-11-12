"""Journal-style figures for the complementarity audit, selection policies and execution feedback.

Uses the workbench figure conventions (ceres_lab/figures/style.py: Elsevier column widths, embedded TrueType,
Okabe–Ito colours with marker/dash redundancy, GB/T 3102 axis labels). Each figure is written in Chinese and
English as PDF, SVG and 600 dpi PNG, with its source-data CSV and both captions in ``figures/index.json``.
Only records produced by the run directory (and the read-only experience summary) are drawn.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from ..figures.style import Style, apply, color, dash, figure, fit_height, marker, panel_label, qlabel
from . import plans
from . import terms as T

FAMILY_LABEL = {**T.FAMILY, 'cvrp_archive': ('CVRP 基准（4505 次运行）', 'CVRP benchmark (4505 runs)')}
PROFILE_LABEL = T.PROFILE
POLICY_LABEL = T.POLICY
OWN_KEYS = ('fig1_headroom', 'fig2_coverage_feasibility', 'fig3_shapley', 'fig4_try_order', 'fig5_learning_curve',
            'fig6_instance_space', 'fig7_execution_feedback')


def _policy_label(name: str, st: Style) -> str:
    return T.policy_label(name, st.lang)


def _save(fig, out: Path, key: str, st: Style, caption: dict, rows: list[dict], index: list):
    fit_height(fig)
    base = out / f'{key}_{st.lang}'
    for fmt in ('pdf', 'svg', 'png'):
        kw = {'format': fmt}
        if fmt == 'png':
            kw['dpi'] = st.dpi
        fig.savefig(f'{base}.{fmt}', **kw)
    csv_path = out / f'{key}.csv'
    if rows and not csv_path.exists():
        keys = []
        for r in rows:
            keys += [k for k in r if k not in keys]
        with csv_path.open('w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    w_in, h_in = fig.get_size_inches()
    index.append({'key': key, 'lang': st.lang, 'files': [f'{base.name}.{f}' for f in ('pdf', 'svg', 'png')],
                  'source_csv': csv_path.name if rows else None, 'caption': caption[st.lang],
                  'size_mm': [round(w_in * 25.4, 1), round(h_in * 25.4, 1)]})


def headroom(audit: dict, st: Style):
    fams = [f for f in ('cvrp_archive', 'fleet:total_distance', 'fleet:makespan', 'coverage') if f in audit['families']]
    fig, axs = figure(st, 0.42, ncols=2)
    a, b = axs[0]
    rows = []
    y = np.arange(len(fams))[::-1]
    for yi, f in zip(y, fams):
        s = audit['families'][f]
        h = s.get('headroom') or {}
        val = 100 * h.get('rho', 0.0)
        lo, hi = (100 * v for v in h.get('rho_ci95', (0.0, 0.0)))
        a.barh(yi, max(val, 1e-4), color=color(0), height=0.55)
        a.errorbar(max(val, 1e-4), yi, xerr=[[max(val - lo, 0)], [max(hi - val, 0)]], fmt='none', ecolor='#111111', lw=0.6, capsize=1.5)
        a.text(max(hi, val, 1e-4) * 1.25, yi, f'{val:.3g}%', va='center', fontsize=st.fs - 1)
        feas_gap = 100 * h.get('feasible_share', 0.0)
        b.barh(yi, feas_gap, color=color(3), height=0.55, hatch='///', edgecolor='white', lw=0)
        b.text(feas_gap + 0.4, yi, f"{h.get('feasible', 0)}/{s['instances']}", va='center', fontsize=st.fs - 1)
        rows.append({'family': f, 'instances': s['instances'], 'solvable': s['solvable'], 'sbs': (s.get('sbs') or {}).get('config'),
                     'headroom_rho_pct': val, 'ci_low_pct': lo, 'ci_high_pct': hi, 'feasible_gap': h.get('feasible'),
                     'feasible_gap_pct': feas_gap})
    labels = [st.t(*FAMILY_LABEL[f]) for f in fams]
    a.set_yticks(y, labels)
    a.set_xscale('log')
    a.set_xlim(1e-3, 300)
    a.set_xlabel(qlabel(st, 'VBS–SBS 差距', 'VBS–SBS gap', '%'))
    b.set_yticks(y, [''] * len(y))
    b.set_xlabel(qlabel(st, '仅非 SBS 配置可行的实例', 'Instances feasible only for non-SBS configurations', '%'))
    b.set_xlim(0, max(5.0, max(r['feasible_gap_pct'] for r in rows) * 1.35))
    for ax in (a, b):
        ax.grid(axis='x', which='major')
    panel_label(a, 'a', st)
    panel_label(b, 'b', st)
    cap = {'zh': '各任务族的算法选择余量（开发集）。(a) 单一最优配置（SBS，平均惩罚化成本比最低）相对虚拟最优配置（VBS）的平均惩罚化成本比之差，误差线为按田块/基准实例自助 95% 置信区间，'
                 '横轴为对数刻度；(b) 仅非 SBS 配置通过独立检验的实例比例。成本比以各实例最优可行成本为 1，不可行解计 2；无可行配置的实例不计入 (a)。',
           'en': 'Algorithm-selection headroom by task family (development set). (a) Mean penalised cost-ratio gap between the single best solver (SBS, lowest mean penalised '
                 'ratio) and the virtual best solver (VBS), with field/base-instance bootstrap 95% CIs, log scale; (b) share of instances feasible only for non-SBS '
                 'configurations under the independent checker. Ratios are relative to the best feasible cost per instance; infeasible results count as 2; instances '
                 'without a feasible configuration are excluded from (a).'}
    return fig, cap, rows


def coverage_feasibility(audit: dict, matrix_csv: Path, st: Style):
    cov = audit['families']['coverage']
    data = {}
    with matrix_csv.open(encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            prof = json.loads(r['instance'])['profile']
            data.setdefault((prof, r['config']), []).append(r['cost'] != '')
            data.setdefault((prof, '__any__'), [])
    instances = {}
    with matrix_csv.open(encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            instances.setdefault(r['instance'], []).append(r['cost'] != '')
    any_by_prof = {}
    for key, flags in instances.items():
        any_by_prof.setdefault(json.loads(key)['profile'], []).append(any(flags))
    configs = list(plans.COVERAGE_PORTFOLIO)
    profs = [p for p in PROFILE_LABEL if any((p, c) in data for c in configs)]
    fig, axs = figure(st, 0.55)
    ax = axs[0][0]
    width = 0.8 / max(1, len(profs))
    x = np.arange(len(configs))
    rows = []
    for pi, p in enumerate(profs):
        vals = [100 * np.mean(data.get((p, c), [False])) for c in configs]
        ax.bar(x + (pi - (len(profs) - 1) / 2) * width, vals, width * 0.92, color=color(pi), label=st.t(*PROFILE_LABEL[p]),
               hatch=['', '///', '...'][pi % 3], edgecolor='white', lw=0)
        vbs = 100 * np.mean(any_by_prof.get(p, [False]))
        ax.axhline(vbs, color=color(pi), ls=dash(pi + 1), lw=0.7)
        rows += [{'profile': p, 'config': c, 'feasible_pct': v, 'vbs_pct': vbs} for c, v in zip(configs, vals)]
    ax.set_xticks(x, [T.config_label(c, st.lang) for c in configs], rotation=40, ha='right')
    ax.set_ylabel(qlabel(st, '通过独立检验的实例', 'Instances passing the independent check', '%'))
    ax.set_ylim(0, 105)
    ax.grid(axis='y')
    ax.legend(loc='upper right', ncols=3)
    d = cov.get('workbench_default') or {}
    cap = {'zh': f"Fields2Cover 配置在荷兰开发集田块上的可行率（按机型分组）。柱为通过足迹、采样曲率与内缩区覆盖率 ≥ 90% 检验的实例比例，虚线为任一配置可行（VBS）的比例；"
                 f"共 {cov['instances']} 个田块 × 机型实例。工作台默认配置（{T.config_label(d.get('config', plans.WORKBENCH_DEFAULT))}）可行 {d.get('feasible')} 个。",
           'en': f"Feasibility of Fields2Cover configurations on the Dutch development fields by machine type. Bars: share passing the footprint, sampled-curvature and "
                 f"≥ 90% inner-coverage checks; dashed lines: share solved by any configuration (VBS); {cov['instances']} field × machine instances. "
                 f"The workbench default ({T.config_label(d.get('config', plans.WORKBENCH_DEFAULT), 'en')}) is feasible on {d.get('feasible')}."}
    return fig, cap, rows


def shapley_bars(audit: dict, st: Style):
    cov = audit['families']['coverage']
    sh = sorted(cov['shapley'], key=lambda r: -r['shapley'])
    fig, axs = figure(st, 0.55)
    ax = axs[0][0]
    y = np.arange(len(sh))[::-1]
    ax.barh(y + 0.18, [r['shapley'] for r in sh], 0.34, color=color(0), label=st.t('Shapley 贡献', 'Shapley contribution'))
    ax.barh(y - 0.18, [r['marginal'] for r in sh], 0.34, color=color(1), hatch='///', edgecolor='white', lw=0,
            label=st.t('边际贡献', 'Marginal contribution'))
    ax.set_yticks(y, [T.config_label(r['config'], st.lang) for r in sh])
    ax.set_xlabel(st.t('对组合价值 v(S) 的贡献（惩罚化成本比单位）', 'Contribution to portfolio value v(S) (penalised-ratio units)'))
    ax.grid(axis='x')
    ax.legend(loc='lower right')
    cap = {'zh': '覆盖配置对虚拟最优组合的贡献。v(S) 为实例平均的 [2 − min_{a∈S} ρ]；Shapley 贡献按全部子集精确计算（Fréchette 等，2016），边际贡献为自完整组合移除该配置后的价值损失。'
                 '边际贡献近零而 Shapley 贡献非零，表示该配置与其他配置可相互替代。',
           'en': 'Contributions of coverage configurations to the virtual-best portfolio. v(S) is the instance mean of [2 − min_{a∈S} ρ]; Shapley contributions are exact over all '
                 'subsets (Fréchette et al. 2016); the marginal contribution is the loss when the configuration is removed from the full portfolio. A near-zero marginal '
                 'contribution with a non-zero Shapley contribution indicates substitutability.'}
    return fig, cap, [{'config': r['config'], 'shapley': r['shapley'], 'marginal': r['marginal']} for r in sh]


def try_order(audit: dict, policy: dict | None, st: Style):
    cov = audit['families']['coverage']
    curve = cov['greedy_static_order']
    fig, axs = figure(st, 0.55)
    ax = axs[0][0]
    t = [c['tries'] for c in curve]
    feas = [100 * c['feasible'] / cov['solvable'] for c in curve]
    ax.plot(t, feas, color=color(0), marker=marker(0), ls=dash(0), label=st.t('贪心静态顺序（全样本拟合）', 'Greedy static order (fitted on all data)'))
    ax.axhline(100, color='#555555', ls=':', lw=0.7)
    rows = [{'tries': a, 'feasible_pct_of_solvable': b, 'config': c['config']} for a, b, c in zip(t, feas, curve)]
    if policy:
        for i, n in enumerate(('default', 'static', 'logit:agri', 'knn5:agri')):
            p = policy['cv']['policies'].get(n)
            if p:
                ax.scatter([p['tries']], [100.0], marker=marker(i + 1), color=color(i + 1), zorder=5, s=14,
                           label=st.t(f"{_policy_label(n, st)}：平均尝试 {p['tries']:.2f} 次", f"{_policy_label(n, st)}: {p['tries']:.2f} tries"))
                rows.append({'policy': n, 'mean_tries_to_feasible_cv': p['tries'], 'first_try_feasible_cv': p['first_try_feasible']})
    ax.set_xlabel(st.t('已尝试的配置数', 'Configurations tried'))
    ax.set_ylabel(qlabel(st, '已获可行路径的可解实例', 'Solvable instances with a feasible path', '%'))
    ax.set_xticks(t)
    ax.set_ylim(0, 104)
    ax.grid()
    ax.legend(loc='lower right')
    cap = {'zh': '尝试顺序与可行率。曲线为在全部开发集数据上按边际收益贪心构造的固定顺序（样本内）；散点为田块分组交叉验证中各策略获得首个可行路径所需的平均尝试次数（留出田块）。',
           'en': 'Try-order and feasibility. The curve is a fixed order built greedily by marginal gain on all development data (in-sample); markers give the mean number of '
                 'attempts to the first feasible path for each policy under field-grouped cross-validation (held-out fields).'}
    return fig, cap, rows


def learning_curve(policy: dict, st: Style):
    preq = policy['prequential']
    names = [n for n in ('default', 'static-online', 'knn5:size', 'logit:size', 'knn5:agri', 'logit:agri', 'knn1:agri')
             if n in preq['curves']]
    fig, axs = figure(st, 0.62, ncols=2)
    a, b = axs[0]
    rows = []
    for i, n in enumerate(names):
        c = preq['curves'][n]
        mean, lo, hi = (np.array(c[k]) for k in ('mean', 'p10', 'p90'))
        x = np.arange(1, len(mean) + 1)
        a.plot(x, mean[:, 0], color=color(i), ls=dash(i), lw=1.0, label=_policy_label(n, st))
        a.fill_between(x, lo[:, 0], hi[:, 0], color=color(i), alpha=0.12, lw=0)
        b.plot(x, mean[:, 1], color=color(i), ls=dash(i), lw=1.0)
        rows += [{'policy': n, 'fields_seen': int(xi), 'cum_regret_mean': float(m0), 'cum_first_try_failures_mean': float(m1)}
                 for xi, m0, m1 in zip(x, mean[:, 0], mean[:, 1])]
    for ax in (a, b):
        ax.axvline(preq['cold_start_fields'] + 0.5, color='#777777', ls=':', lw=0.6)
        ax.set_xlabel(st.t('已处理田块数', 'Fields processed'))
        ax.grid()
    a.set_ylabel(st.t('累积遗憾 Σ(成本比 − 1)', 'Cumulative regret Σ(ratio − 1)'))
    b.set_ylabel(st.t('累积首次尝试失败次数', 'Cumulative first-try failures'))
    a.legend(loc='upper left', fontsize=st.fs - 1.5)
    panel_label(a, 'a', st)
    panel_label(b, 'b', st)
    cap = {'zh': f"先测后学（prequential）评价：按 {preq['orders']} 个随机田块顺序逐块处理，每块先由当前策略决定尝试顺序并计分，再将该田块的全部结果并入经验库。"
                 f"前 {preq['cold_start_fields']} 块采用冷启动顺序（默认配置优先），竖虚线后启用学习策略。实线为均值，阴影为第 10–90 百分位。",
           'en': f"Prequential (test-then-train) evaluation over {preq['orders']} random field orders: each field is first decided by the current policy and scored, then all of its "
                 f"results enter the experience base. The first {preq['cold_start_fields']} fields use the cold-start order (default first); learned policies start "
                 f"after the dotted line. Lines: mean; bands: 10th–90th percentiles."}
    return fig, cap, rows


def instance_space(policy_ds, st: Style):
    from . import features as F
    from .policy import design
    X = design(policy_ds['feats'], F.COVERAGE)
    keep = policy_ds['solvable']
    Z = (X - X.mean(axis=0)) / np.where(X.std(axis=0) > 1e-12, X.std(axis=0), 1)
    U, S, Vt = np.linalg.svd(Z[keep] - Z[keep].mean(axis=0), full_matrices=False)
    P = (Z - Z[keep].mean(axis=0)) @ Vt[:2].T
    best = np.argmin(policy_ds['rho'], axis=1)
    configs = policy_ds['configs']
    fig, axs = figure(st, 1.15)
    ax = axs[0][0]
    rows = []
    shown = sorted(set(best[keep]), key=lambda j: -int((best[keep] == j).sum()))
    for rank, j in enumerate(shown):
        sel = keep & (best == j)
        ax.scatter(P[sel, 0], P[sel, 1], s=8, color=color(rank), marker=marker(rank), lw=0.3, edgecolor='white',
                   label=T.config_label(configs[j], st.lang))
    if (~keep).any():
        ax.scatter(P[~keep, 0], P[~keep, 1], s=10, color='#222222', marker='x', lw=0.6, label=st.t('无可行配置', 'No feasible configuration'))
    for i in range(len(P)):
        rows.append({'instance': policy_ds['m']['keys'][i], 'pc1': float(P[i, 0]), 'pc2': float(P[i, 1]),
                     'best_config': configs[best[i]] if keep[i] else None})
    var = S ** 2 / (S ** 2).sum()
    ax.set_xlabel(st.t(f'主成分 1（{100 * var[0]:.1f}% 方差）', f'PC1 ({100 * var[0]:.1f}% variance)'))
    ax.set_ylabel(st.t(f'主成分 2（{100 * var[1]:.1f}% 方差）', f'PC2 ({100 * var[1]:.1f}% variance)'))
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc='outside lower center', ncols=2, fontsize=st.fs - 1.5, markerscale=1.4, frameon=False)
    ax.grid()
    cap = {'zh': '实例空间：农艺结构特征的主成分投影（Smith-Miles 实例空间分析的线性简化）。颜色与标记表示该实例上成本比最低的配置，× 为无配置通过独立检验的实例。',
           'en': 'Instance space: principal components of the agronomic features (a linear simplification of Smith-Miles instance space analysis). Colour and marker: '
                 'configuration with the lowest ratio on the instance; ×: no configuration passes the independent checker.'}
    return fig, cap, rows


def execution_feedback(summary: dict, episodes_path: Path, st: Style):
    rows = []
    for line in episodes_path.read_text(encoding='utf-8').splitlines():
        e = json.loads(line)
        if e['source'] != 'field_execution' or e['state'] != 'succeeded':
            continue
        for v in e['vehicles']:
            if v['state'] == 'done' and v['planned_duration_s'] and v['driving_duration_s']:
                rows.append({'session': e['session'], 'plan_paths_sha256': e['plan_paths_sha256'], 'vehicle': v['name'],
                             'serial_traffic': e['serial_traffic'], 'planned_duration_s': v['planned_duration_s'],
                             'driving_duration_s': v['driving_duration_s'], 'queue_wait_s': v['queue_wait_s'],
                             'reference_duration_s': v['reference_duration_s']})
    fig, axs = figure(st, 0.62)
    ax = axs[0][0]
    p = np.array([r['planned_duration_s'] for r in rows])
    a = np.array([r['driving_duration_s'] for r in rows])
    serial = np.array([r['serial_traffic'] for r in rows])
    for i, (flag, zh, en) in enumerate(((False, '早期并行执行会话', 'Early parallel sessions'), (True, '串行交通预约会话', 'Serial-reservation sessions'))):
        sel = serial == flag
        if sel.any():
            ax.scatter(p[sel], a[sel], s=12, color=color(i), marker=marker(i), label=st.t(zh, en), lw=0.3, edgecolor='white')
    lim = [0, max(a.max(), p.max()) * 1.05] if len(rows) else [0, 1]
    ax.plot(lim, lim, color='#555555', ls=':', lw=0.7, label=st.t('实际 = 规划', 'actual = planned'))
    ax.set_xlim(lim)
    ax.set_ylim(lim)
    ax.set_xlabel(qlabel(st, '规划时间（恒速模型）', 'Planned duration (constant-speed model)', 's'))
    ax.set_ylabel(qlabel(st, '仿真驾驶时间（扣除排队等待）', 'Simulated driving time (queue waits removed)', 's'))
    ax.legend(loc='upper left')
    ax.grid()
    fe = summary['field_execution']
    s = fe.get('vehicle_driving_over_planned') or {}
    cap = {'zh': f"执行反馈信号：{fe['succeeded']} 场成功田间仿真中 {len(rows)} 次车辆运行的规划时间与仿真驾驶时间（已扣除串行预约的排队等待）。驾驶/规划时间比中位数 "
                 f"{s.get('median', float('nan')):.3f}，第 10–90 百分位 {s.get('p10', float('nan')):.3f}–{s.get('p90', float('nan')):.3f}；会话包含同一方案的重复执行，"
                 f"共 {fe.get('distinct_plans')} 个不同方案。二者之差源于加减速、转向与跟踪误差，即执行反馈学习的校正对象。",
           'en': f"Execution-feedback signal: planned duration versus simulated driving time (serial-reservation queue waits removed) for {len(rows)} vehicle runs in "
                 f"{fe['succeeded']} successful field simulations. Median driving/planned ratio {s.get('median', float('nan')):.3f} (10th–90th percentile "
                 f"{s.get('p10', float('nan')):.3f}–{s.get('p90', float('nan')):.3f}); sessions repeat plans ({fe.get('distinct_plans')} distinct). The gap arises from "
                 f"acceleration, turning and tracking error and is the quantity execution-feedback learning corrects."}
    return fig, cap, rows


def write(out: Path) -> list[str]:
    out = Path(out)
    audit = json.loads((out / 'audit' / 'audit.json').read_text(encoding='utf-8'))
    pol_path = out / 'policy' / 'policy.json'
    policy = json.loads(pol_path.read_text(encoding='utf-8')) if pol_path.exists() else None
    target = out / 'figures'
    target.mkdir(exist_ok=True)
    index: list = []
    ds = None
    exp_path = out / 'experience' / 'episodes.jsonl'
    for lang in ('zh', 'en'):
        for key, size, fn in (('fig1_headroom', 'double', lambda st: headroom(audit, st)),
                              ('fig2_coverage_feasibility', 'double', lambda st: coverage_feasibility(audit, out / 'audit' / 'coverage_matrix.csv', st)),
                              ('fig3_shapley', 'single', lambda st: shapley_bars(audit, st)),
                              ('fig4_try_order', 'single', lambda st: try_order(audit, policy, st))):
            if key != 'fig1_headroom' and 'coverage' not in audit['families']:
                continue
            st = Style('elsevier', size, lang)
            with apply(st):
                fig, cap, rows = fn(st)
                _save(fig, target, key, st, cap, rows, index)
        if policy:
            st = Style('elsevier', 'double', lang)
            with apply(st):
                fig, cap, rows = learning_curve(policy, st)
                _save(fig, target, 'fig5_learning_curve', st, cap, rows, index)
            if ds is None:
                from .policy import dataset
                ds = dataset(out)
            st = Style('elsevier', 'single', lang)
            with apply(st):
                fig, cap, rows = instance_space(ds, st)
                _save(fig, target, 'fig6_instance_space', st, cap, rows, index)
        if exp_path.exists():
            summary = json.loads(exp_path.with_suffix('.summary.json').read_text(encoding='utf-8'))
            st = Style('elsevier', 'single', lang)
            with apply(st):
                fig, cap, rows = execution_feedback(summary, exp_path, st)
                _save(fig, target, 'fig7_execution_feedback', st, cap, rows, index)
    index_path = target / 'index.json'
    others = [e for e in json.loads(index_path.read_text(encoding='utf-8')) if e.get('key') not in OWN_KEYS] if index_path.exists() else []
    index_path.write_text(json.dumps(index + others, ensure_ascii=False, indent=1), encoding='utf-8')
    return [f"{i['key']} [{i['lang']}] {i['size_mm']}" for i in index]
