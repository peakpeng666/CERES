"""Study-level figures (several instances × algorithms × seeds): performance profiles (Dolan & Moré 2002),
gap distributions, Friedman + Nemenyi critical-difference diagram (Demšar 2006), pairwise Wilcoxon–Holm,
median anytime curves, time–quality trade-off, instance space (Smith-Miles et al.), and the evaluation of the
algorithm recommendation against the single best (SBS) and virtual best (VBS) solvers (as in ASlib)."""
from __future__ import annotations

import math
import statistics as stt
from collections import defaultdict

import numpy as np
from matplotlib.ticker import LogLocator, NullFormatter

from ..analysis import cd_unavailable_reason
from . import data as D
from .names import alg_index, alg_label
from .style import Style, color, dash, figure, log_axis, marker, qlabel


def _names(an: dict, st: Style) -> dict[str, str]:
    zh = {s['algorithm']: s.get('name') for s in an.get('summary') or []}
    return {a: alg_label(a, st, zh.get(a)) for a in an.get('algorithms') or []}


def _n_text(an: dict, st: Style) -> str:
    return st.t(f"{an.get('n_instances')} 个实例（{an.get('n_complete_instances')} 个所有算法齐全）、{an.get('n_runs')} 次运行",
                f"{an.get('n_instances')} instances ({an.get('n_complete_instances')} complete for all algorithms), {an.get('n_runs')} runs")


def perf_profile(ctx: D.Context, st: Style, opts: dict):
    an = ctx.analysis
    pp = an['performance_profile']
    names = _names(an, st)
    taus = np.asarray(pp['taus'], float)
    fig, axs = figure(st, 0.7)
    ax = axs[0][0]
    rows = []
    for a in an['algorithms']:
        rho = np.asarray(pp['curves'].get(a) or [], float)
        if not len(rho):
            continue
        i = alg_index(a)
        ax.step(taus, rho, where='post', color=color(i), ls=dash(i), lw=1.1, label=names[a])
        rows += [{'algorithm': a, 'tau': float(t), 'rho': float(r)} for t, r in zip(taus, rho)]
    if taus.max(initial=1) > 2:
        from matplotlib.ticker import FuncFormatter
        ax.set_xscale('log', base=2)
        ax.xaxis.set_major_locator(LogLocator(base=2))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:g}'))
        ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_xlim(1, taus.max(initial=1.0) * 1.02)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel(st.t('性能比 τ', 'Performance ratio τ'))
    ax.set_ylabel(st.t('实例比例 ρ(τ)', 'Fraction of instances ρ(τ)'))
    ax.grid(which='major')
    ax.legend(loc='lower right')
    cap = {
        'zh': f"性能曲线（Dolan & Moré 2002）：ρ(τ) 为该算法成本不超过每个实例最好成本 τ 倍的实例比例；曲线越靠左上越好，τ = 1 处为取得最好解的比例。"
              f"基于 {_n_text(an, st)}，只纳入所有算法齐全的实例，每个实例取各算法可行运行的平均成本；全失败的算法–实例单元保留在分母，在有限 τ 处不计成功。部分种子失败另见运行成功率。",
        'en': f"Performance profiles (Dolan & Moré 2002): ρ(τ) is the fraction of instances on which an algorithm's cost is within a factor τ of the best; "
              f"higher and further left is better. Based on complete paired instances from {_n_text(an, st)}; per-instance cost is the mean over feasible runs. An algorithm-instance cell with no feasible run remains in the instance denominator but never counts at finite τ. Partial seed failures are reflected separately in run success rates.",
    }
    return fig, cap, {'profile': rows}


def gap_distribution(ctx: D.Context, st: Style, opts: dict):
    an = ctx.analysis
    names = _names(an, st)
    algs = [a for a in an['algorithms'] if an['ecdf'].get(a)]
    algs.sort(key=lambda a: stt.median(an['ecdf'][a]))
    k = len(algs)
    fig, axs = figure(st, max(0.32, min(1.0, 0.16 + 0.085 * k)))
    ax = axs[0][0]
    data = [np.asarray(an['ecdf'][a], float) for a in algs]
    pos = np.arange(k)[::-1] + 1
    bp = ax.boxplot(data, positions=pos, orientation='horizontal', whis=(5, 95), showfliers=False, widths=0.55, patch_artist=True,
                    medianprops={'color': '#111111', 'lw': 0.8}, boxprops={'lw': 0.5}, whiskerprops={'lw': 0.5}, capprops={'lw': 0.5})
    rng = np.random.default_rng(7)                          # jitter only: fixed seed so the figure is reproducible
    rows = []
    for j, (a, d) in enumerate(zip(algs, data)):
        i = alg_index(a)
        bp['boxes'][j].set_facecolor(color(i))
        bp['boxes'][j].set_alpha(0.35)
        ax.plot(d, pos[j] + rng.uniform(-0.16, 0.16, len(d)), ls='none', marker=marker(i), ms=1.8, mfc=color(i), mec='none', alpha=0.8, zorder=3)
        rows += [{'algorithm': a, 'gap_pct': float(g)} for g in d]
    ax.set_yticks(pos, [names[a] for a in algs])
    allg = np.concatenate(data) if data else np.zeros(1)
    if allg.max() > 100 and np.median(allg) < 20:
        ax.set_xscale('symlog', linthresh=1)
    ax.set_xlabel(qlabel(st, '相对参照解的差距', 'Gap to reference', '%'))
    ax.grid(axis='x')
    ax.axvline(0, color='#888888', lw=0.5)
    cap = {
        'zh': f"各算法相对参照解差距的分布（每点一次有有限差距的有效运行；失败和无正参照运行不画点；箱体为四分位，须为 5–95 百分位）。参照：{an.get('reference_note')} {_n_text(an, st)}。",
        'en': f"Distribution of the gap to the reference solution per algorithm (one point per finite valid-run gap; failures and runs without a positive reference are not plotted; boxes are quartiles, whiskers the 5th–95th percentiles). "
              f"Reference: best-known solution for CVRPLIB instances, otherwise the best valid solution found by any algorithm. {_n_text(an, st)}.",
    }
    return fig, cap, {'gaps': rows}


def cd_diagram(ctx: D.Context, st: Style, opts: dict):
    """Critical-difference diagram (Demšar 2006): mean ranks on an axis, groups not significantly different joined by bars."""
    an = ctx.analysis
    tests = an['tests']
    reason = cd_unavailable_reason(tests)
    if reason:
        raise ValueError(reason)
    nem = tests['nemenyi']
    names = _names(an, st)
    ranks = sorted(nem['mean_ranks'].items(), key=lambda kv: kv[1])
    k = len(ranks)
    cd = nem['critical_difference']
    left = ranks[: (k + 1) // 2]
    right = ranks[(k + 1) // 2:]
    rows_per_side = max(len(left), len(right))
    fig, axs = figure(st, 0.18 + 0.055 * rows_per_side)
    ax = axs[0][0]
    ax.set_xlim(0.5 - 0.95 * k / 3, k + 0.5 + 0.95 * k / 3)
    top = 0.0
    ax.set_ylim(-(rows_per_side * 0.9 + 0.45), 1.6)
    ax.axis('off')
    ax.hlines(top, 1, k, color='#111111', lw=0.8)
    for r in range(1, k + 1):
        ax.vlines(r, top, top + 0.22, color='#111111', lw=0.8)
        ax.text(r, top + 0.32, str(r), ha='center', va='bottom', fontsize=st.fs - 0.5)
    for i, (a, rk) in enumerate(left):
        y = -(i + 1) * 0.9
        ax.plot([rk, rk, 0.85], [top, y, y], color='#111111', lw=0.7)
        ax.text(0.8, y, f'{names[a]}  {rk:.2f}', ha='right', va='center', fontsize=st.fs - 0.5)
    for i, (a, rk) in enumerate(right[::-1]):
        y = -(i + 1) * 0.9
        ax.plot([rk, rk, k + 0.15], [top, y, y], color='#111111', lw=0.7)
        ax.text(k + 0.2, y, f'{rk:.2f}  {names[a]}', ha='left', va='center', fontsize=st.fs - 0.5)
    # CD bar
    ax.plot([1, 1 + cd], [1.15, 1.15], color='#111111', lw=1.0)
    for x in (1, 1 + cd):
        ax.vlines(x, 1.05, 1.25, color='#111111', lw=0.8)
    ax.text(1 + cd / 2, 1.3, f'CD = {cd:.2f}', ha='center', va='bottom', fontsize=st.fs - 0.5)
    # cliques: maximal runs of consecutive algorithms whose rank spread is below CD
    vals = [rk for _, rk in ranks]
    cliques = []
    for i in range(k):
        j = i
        while j + 1 < k and vals[j + 1] - vals[i] <= cd:
            j += 1
        if j > i and not any(a <= i and j <= b for a, b in cliques):
            cliques.append((i, j))
    for c, (i, j) in enumerate(cliques):
        y = -0.25 - 0.18 * c
        ax.plot([vals[i] - 0.03, vals[j] + 0.03], [y, y], color='#111111', lw=2.2, solid_capstyle='butt')
    fr = tests.get('friedman') or {}
    ptxt = f"p = {fr['p_value']:.2g}" if fr.get('p_value') is not None else st.t('无法计算', 'not computable')
    cap = {
        'zh': f"临界差图（Demšar 2006）：轴上为各算法在 {tests['n']} 个实例上的平均秩（1 为最好），粗横线连接的算法之间差异不显著"
              f"（Nemenyi 检验，α = {tests['alpha']}，CD = {cd:.2f}）。Friedman 检验 χ² = {fr.get('statistic'):.2f}，{ptxt}。",
        'en': f"Critical-difference diagram (Demšar 2006): mean ranks over {tests['n']} instances (1 = best); algorithms joined by a thick bar are not "
              f"significantly different (Nemenyi, α = {tests['alpha']}, CD = {cd:.2f}). Friedman test χ² = {fr.get('statistic'):.2f}, {ptxt}.",
    }
    rows = [{'algorithm': a, 'mean_rank': rk} for a, rk in ranks] + [{'algorithm': 'CD', 'mean_rank': cd}]
    return fig, cap, {'ranks': rows}


def pairwise(ctx: D.Context, st: Style, opts: dict):
    an = ctx.analysis
    names = _names(an, st)
    algs = an['algorithms']
    k = len(algs)
    idx = {a: i for i, a in enumerate(algs)}
    M = np.full((k, k), np.nan)
    star = [['' for _ in range(k)] for _ in range(k)]
    for r in an['tests']['wilcoxon_holm']:
        if not r['n']:
            continue
        i, j = idx[r['a']], idx[r['b']]
        M[i, j] = r['a_better'] / r['n']
        M[j, i] = r['b_better'] / r['n']
        p = r.get('p_holm')
        if p is not None:
            s = '***' if p < 0.001 else '**' if p < 0.01 else '*' if p < 0.05 else ''
            if r['a_better'] > r['b_better']:
                star[i][j] = s
            elif r['b_better'] > r['a_better']:
                star[j][i] = s
    fig, axs = figure(st, 0.82)
    ax = axs[0][0]
    im = ax.imshow(M, cmap='RdBu', vmin=0, vmax=1)
    for i in range(k):
        for j in range(k):
            if i != j and np.isfinite(M[i, j]):
                ax.text(j, i, f'{M[i, j]:.2f}{star[i][j]}', ha='center', va='center', fontsize=st.fs - 1.5,
                        color='white' if abs(M[i, j] - 0.5) > 0.32 else '#111111')
    ax.set_xticks(range(k), [names[a] for a in algs], rotation=40, ha='right')
    ax.set_yticks(range(k), [names[a] for a in algs])
    ax.set_xlabel(st.t('列：对手算法', 'Column: opponent'))
    for sp in ax.spines.values():
        sp.set_visible(False)
    cb = ax.figure.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
    cb.set_label(st.t('优于对手的实例比例', 'Share of instances better than opponent'))
    cb.outline.set_linewidth(0.4)
    cap = {
        'zh': f"两两比较：格中数字为行算法优于列算法的实例比例，星号为 Wilcoxon 符号秩检验经 Holm 校正后的显著性（* p<0.05，** p<0.01，*** p<0.001；"
              f"仅纳入双方成本及差值有限的配对；有效配对不足 5 个或全部平局时不检验）。{_n_text(an, st)}。",
        'en': f"Pairwise comparison: each cell is the share of instances on which the row algorithm beats the column algorithm; stars mark Wilcoxon signed-rank "
              f"tests with Holm correction (* p<0.05, ** p<0.01, *** p<0.001; only finite-cost, finite-difference pairs are included; fewer than 5 such pairs or all ties are not tested). {_n_text(an, st)}.",
    }
    return fig, cap, {'pairs': an['tests']['wilcoxon_holm']}


def anytime_median(ctx: D.Context, st: Style, opts: dict):
    an = ctx.analysis
    names = _names(an, st)
    grid = np.asarray(an['anytime']['grid'], float)
    fig, axs = figure(st, 0.62)
    ax = axs[0][0]
    rows = []
    for a in an['algorithms']:
        med = an['anytime']['median'].get(a)
        if not med:
            continue
        i = alg_index(a)
        y = np.array([np.nan if v is None or not math.isfinite(v) else v for v in med], float)
        ax.plot(grid, y, color=color(i), ls=dash(i), lw=1.1, label=names[a], drawstyle='steps-post')
        observed = an['anytime'].get('observed_counts', {}).get(a, [])
        rows += [{'algorithm': a, 't_s': float(t), 'median_gap_pct': None if np.isnan(v) else float(v),
                  'observed_trace_count': observed[k] if k < len(observed) else None}
                 for k, (t, v) in enumerate(zip(grid, y))]
    log_axis(ax, 'x')
    ax.set_xlabel(qlabel(st, '计算时间', 'Wall-clock time', 's'))
    ax.set_ylabel(qlabel(st, '差距中位数', 'Median gap', '%'))
    ax.set_ylim(bottom=0)
    ax.grid(which='major')
    ax.legend(loc='upper right')
    counts = an['anytime'].get('counts') or {}
    details = '; '.join(f"{a}: traces={c['valid_trace_runs']}/{c['terminal_runs']}, terminal successes={c['terminal_successes']}, exclusions={c['reasons']}"
                        for a, c in counts.items())
    cap = {
        'zh': '条件质量曲线：仅最终验算成功且目标遥测合法的运行，各时刻分母为已观测到 incumbent 的轨迹数；首次记录前未知，不补无穷。不是全运行预算成功率。' + details,
        'en': 'Conditional quality curve for successful validated runs with valid objective telemetry. Each time uses only observed incumbents; before the first sample is unknown, not infinity. Not an all-run budget success curve. ' + details,
    }
    count_rows = [{'algorithm': a, 'terminal_runs': c['terminal_runs'], 'terminal_successes': c['terminal_successes'],
                   'valid_trace_runs': c['valid_trace_runs'], **c['reasons']} for a, c in counts.items()]
    return fig, cap, {'anytime': rows, 'telemetry_counts': count_rows}


def time_quality(ctx: D.Context, st: Style, opts: dict):
    an = ctx.analysis
    names = _names(an, st)
    pts = [p for p in an['scatter'] if p.get('time') is not None and p.get('gap') is not None]
    fig, axs = figure(st, 0.68)
    ax = axs[0][0]
    med = []
    for a in an['algorithms']:
        mine = [p for p in pts if p['algorithm'] == a]
        if not mine:
            continue
        i = alg_index(a)
        t = np.maximum([p['time'] for p in mine], 1e-3)
        g = np.asarray([p['gap'] for p in mine], float)
        ax.plot(t, g, ls='none', marker=marker(i), ms=2.4, mfc=color(i), mec='none', alpha=0.55)
        med.append((a, float(np.median(t)), float(np.median(g)), i))
        ax.plot(np.median(t), np.median(g), ls='none', marker=marker(i), ms=5.5, mfc=color(i), mec='#111111', mew=0.6, zorder=5, label=names[a])
    front = sorted(med, key=lambda m: m[1])
    best, line = math.inf, []
    for m in front:
        if m[2] < best:
            best = m[2]
            line.append(m)
    if len(line) > 1:
        ax.step([m[1] for m in line], [m[2] for m in line], where='post', color='#444444', lw=0.6, ls=':', zorder=4)
    log_axis(ax, 'x')
    ax.set_xlabel(qlabel(st, '计算时间', 'Wall-clock time', 's'))
    ax.set_ylabel(qlabel(st, '相对参照解的差距', 'Gap to reference', '%'))
    ax.grid(which='major')
    ax.legend(loc='upper right')
    cap = {
        'zh': f"计算时间与解质量的权衡：小点为单次运行，大点为各算法的中位数，点线为中位数的帕累托前沿。{_n_text(an, st)}。",
        'en': f"Time–quality trade-off: small markers are single runs, large markers the per-algorithm medians, the dotted line the Pareto front of the medians. {_n_text(an, st)}.",
    }
    return fig, cap, {'runs': pts, 'medians': [{'algorithm': a, 'time_median_s': t, 'gap_median_pct': g} for a, t, g, _ in med]}


TIE = 1e-5   # relative: objective values closer than this are the same solution quality (floating-point noise between solvers)


def _best(vals: dict[str, float]) -> tuple[str | None, list[str]]:
    """Best algorithm, or None with the list of tied algorithms."""
    if not vals:
        return None, []
    lo = min(vals.values())
    ties = sorted((a for a, v in vals.items() if v <= lo + TIE * max(1.0, abs(lo))), key=alg_index)
    return (ties[0] if len(ties) == 1 else None), ties


def instance_space(ctx: D.Context, st: Style, opts: dict):
    """Render every legal panel; do not substitute a second winner contract."""
    panels = [p for p in (ctx.space or {}).get('panels', []) if p.get('projection_status') == 'ok']
    if not panels:
        raise ValueError('至少需要一个同特征/比较合同内含 3 个实例的面板')
    fig, axs = figure(st, nrows=len(panels), height_in=2.5 * len(panels))
    rows, descriptions = [], []
    for panel, axes in zip(panels, axs):
        ax = axes[0]
        pts = panel['points']
        for j, a in enumerate(panel['algorithms']):
            mine = [p for p in pts if p['best'] == a]
            if mine:
                ax.plot([p['x'] for p in mine], [p['y'] for p in mine], ls='none', marker=marker(j),
                        ms=3.8, mfc=color(j), mec='white', mew=.3, label=a)
        unspecified = [p for p in pts if not p['best']]
        if unspecified:
            ax.plot([p['x'] for p in unspecified], [p['y'] for p in unspecified], ls='none', marker='x',
                    ms=3.6, mec='#888888', label=st.t('未指定唯一胜者', 'No unique winner'))
        ev = panel.get('explained_variance') or [0, 0]
        ax.set_xlabel(f'PC1 ({100 * ev[0]:.0f}%)')
        ax.set_ylabel(f'PC2 ({100 * ev[1]:.0f}%)')
        ax.set_title(f"{panel['feature_schema']} | {panel['budget_s']} s", fontsize=st.fs)
        ax.axhline(0, color='#DDDDDD', lw=.4, zorder=0)
        ax.axvline(0, color='#DDDDDD', lw=.4, zorder=0)
        ax.legend(loc='best', fontsize=st.fs - 1)
        rows += [{'panel_id': panel['panel_id'], 'feature_schema': panel['feature_schema'],
                  'budget_s': panel['budget_s'], **p} for p in pts]
        descriptions.append(f"{panel['feature_schema']}, {panel['budget_s']} s, n={len(pts)}, candidates={panel['algorithms']}")
    detail = '; '.join(descriptions)
    cap = {
        'zh': '实例空间按特征 schema、目标语义及预算分面；算法版本/参数分候选列。颜色为完整候选集合内的唯一最低平均成本；候选不齐全、单候选、全失败或并列不指定唯一胜者。投影与颜色不表示统计显著性。' + detail,
        'en': 'Instance space faceted by feature schema, objective semantics and budget; versions and parameters remain distinct candidates. Colours indicate a unique minimum mean cost only with a complete candidate set. Missing candidates, single candidates, failures or ties have no unique winner. Projection and colour do not establish statistical significance. ' + detail,
    }
    return fig, cap, {'points': rows, 'panel_contracts': [{k: v for k, v in p.items() if k != 'points'} for p in panels]}


def selection_eval(ctx: D.Context, st: Style, opts: dict):
    """Recommended algorithm (AS) vs single best solver (SBS) vs per-instance oracle (VBS)."""
    ev = selection_table(ctx.study_runs)
    an = ctx.analysis or {}
    names = _names(an, st) if an.get('algorithms') else {}
    fig, axs = figure(st, 0.62)
    ax = axs[0][0]
    rows = ev['rows']
    for label, key, c, ls in ((st.t('推荐（AS）', 'Recommended (AS)'), 'gap_as', color(0), '-'),
                              (st.t(f"单一最佳（SBS：{names.get(ev['sbs'], ev['sbs'])}）", f"Single best (SBS: {names.get(ev['sbs'], ev['sbs'])})"), 'gap_sbs', color(1), '--')):
        g = np.sort([r[key] for r in rows if r[key] is not None])
        if len(g):
            ax.step(np.r_[0, g], np.r_[0, np.arange(1, len(g) + 1) / len(g)], where='post', color=c, ls=ls, lw=1.1, label=label)
    ax.axvline(0, color='#444444', lw=0.6, ls=':', label=st.t('理想（VBS）', 'Oracle (VBS)'))
    ax.set_xlabel(qlabel(st, '相对每个实例最好算法的差距', 'Gap to the per-instance best', '%'))
    ax.set_ylabel(st.t('实例累计比例', 'Cumulative share of instances'))
    ax.set_ylim(0, 1.02)
    ax.set_xlim(left=min(-0.5, -0.02 * max([r['gap_sbs'] or 0 for r in rows] + [1])))
    ax.grid(which='major')
    ax.legend(loc='lower right')
    cg = ev['closed_gap']
    cap = {
        'zh': f"算法推荐的效果（{ev['n']} 个推荐算法已运行的实例）：曲线为相对每个实例最好算法（VBS）的差距的累计分布（相对差 < {TIE:.0e} 视为并列）。"
              f"推荐命中最好算法 {ev['hits']}/{ev['n']}，平均差距 推荐 {ev['mean_as']:.3g}% / 单一最佳 {ev['mean_sbs']:.3g}%"
              + (f"，闭合差距 {cg:.2f}（1 = 达到 VBS，0 = 等同 SBS）" if cg is not None else '') + f"。推荐取自每次运行提交时保存的建议快照（策略 {ev['policy']}）。",
        'en': f"Effect of the algorithm recommendation ({ev['n']} instances on which the recommended algorithm was run): cumulative distribution of the gap to the "
              f"per-instance best algorithm (VBS; relative differences < {TIE:.0e} count as ties). The recommendation hit the best algorithm on {ev['hits']}/{ev['n']} "
              f"instances; mean gap {ev['mean_as']:.3g}% (recommended) vs {ev['mean_sbs']:.3g}% (single best)" + (f", closed gap {cg:.2f} (1 = VBS, 0 = SBS)" if cg is not None else '') +
              f". Recommendations are the snapshots stored with each run at submission (policy {ev['policy']}).",
    }
    return fig, cap, {'instances': rows}


def selection_table(runs: list[dict]) -> dict:
    by = defaultdict(list)
    for r in runs:
        by[f"{r['scenario_id']}#{r['instance_sha256'][:10]}"].append(r)
    per, recs, policies = {}, {}, set()
    for k, rs in by.items():
        vals = defaultdict(list)
        for r in rs:
            v = (r.get('objective') or {}).get('value')
            if r.get('state') == 'done' and (r.get('check') or {}).get('valid') and v is not None and math.isfinite(v):
                vals[r['algorithm']['id']].append(float(v))
            adv = (r.get('recommendation_at_submission') or {}).get('advice') or {}
            if adv.get('recommended'):
                recs[k] = adv['recommended']
                policies.add(adv.get('policy') or '?')
        per[k] = {a: stt.mean(v) for a, v in vals.items()}
    algs = sorted({a for p in per.values() for a in p})
    complete = [k for k, p in per.items() if p and all(a in p for a in algs)]
    gap = lambda v, best: 100.0 * (v - best) / best if best > 0 else 0.0  # noqa: E731
    sbs = min(algs, key=lambda a: stt.mean(gap(per[k][a], min(per[k].values())) for k in complete)) if complete else None
    rows = []
    for k in complete:
        p, rec = per[k], recs.get(k)
        if rec not in p:
            continue
        best = min(p.values())
        g_as, g_sbs = gap(p[rec], best), gap(p[sbs], best)
        g_as, g_sbs = (0.0 if g < 100 * TIE else g for g in (g_as, g_sbs))      # floating-point ties between solvers
        rows.append({'instance': k, 'recommended': rec, 'vbs': _best(p)[1] or [min(p, key=p.get)], 'gap_as': g_as, 'gap_sbs': g_sbs, 'hit': g_as == 0.0})
    n = len(rows)
    mean_as = stt.mean(r['gap_as'] for r in rows) if rows else float('nan')
    mean_sbs = stt.mean(r['gap_sbs'] for r in rows) if rows else float('nan')
    return {'rows': rows, 'n': n, 'hits': sum(r['hit'] for r in rows), 'sbs': sbs, 'mean_as': mean_as, 'mean_sbs': mean_sbs,
            'closed_gap': (mean_sbs - mean_as) / mean_sbs if rows and mean_sbs > 1e-12 else None, 'policy': '、'.join(sorted(policies)) or '—'}



