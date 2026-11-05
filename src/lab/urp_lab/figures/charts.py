"""Charts of one task map: workload, schedule (Gantt), algorithm comparison, convergence, and the simulation
execution (plan vs execution, tracking error, inter-vehicle distance, task progress)."""
from __future__ import annotations

import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

from . import data as D
from .names import alg_index, alg_name, vehicle_name
from .style import Style, color, dash, figure, log_axis, marker, panel_label, qlabel


def _time(t_max: float, st: Style, zh: str = '时间', en: str = 'Time') -> tuple[float, str]:
    return (60.0, qlabel(st, zh, en, 'min')) if t_max > 900 else (1.0, qlabel(st, zh, en, 's'))


def _objective(run: dict | None, st: Style, best_so_far: bool = False) -> str:
    unit = ((run or {}).get('objective') or {}).get('unit') or ''
    mode = ((run or {}).get('metrics') or {}).get('objective_mode') or ((run or {}).get('instance') or {}).get('objective')
    if mode == 'makespan':
        zh, en, u = '最晚完成时间', 'Makespan', 's'
    elif (run or {}).get('problem_class') == 'fleet_routing' or unit == 'm':
        zh, en, u = '总里程', 'Total distance', 'm'
    else:
        zh, en, u = '目标值', 'Objective', unit or None
    if best_so_far:
        zh, en = '当前最好' + zh, 'Best-so-far ' + en.lower()
    return qlabel(st, zh, en, u)


def _hbar_height(n: int, base: float = 0.42, per: float = 0.2) -> float:
    """Absolute height (in) of a chart with one row per vehicle / algorithm."""
    return base + per * max(1, n)


# --------------------------------------------------------------- planning --
def workload(ctx: D.Context, st: Style, opts: dict):
    run = ctx.run
    per = (run.get('metrics') or {}).get('per_vehicle') or []
    n = len(per)
    fig, axs = figure(st, 0.46, ncols=2)
    ax0, ax1 = axs[0]
    x = np.arange(n)
    names = [vehicle_name(i, v['vehicle'], st) for i, v in enumerate(per)]
    lengths = np.array([v.get('length_m') or 0 for v in per], float)
    durs = np.array([v.get('duration_s') or 0 for v in per], float)
    k, tlabel = _time(durs.max() if n else 0, st, '作业用时', 'Working time')
    cols = [color(i) for i in range(n)]
    ax0.bar(x, lengths, width=0.66, color=cols, edgecolor='#222222', linewidth=0.4)
    for i, v in enumerate(per):
        ax0.text(i, lengths[i], f"{v.get('tasks')}" + (f"/{v.get('trips')}" if (v.get('trips') or 1) > 1 else ''), ha='center', va='bottom', fontsize=st.fs - 1.5)
    ax0.set_ylabel(qlabel(st, '行驶里程', 'Distance travelled', 'm'))
    ax1.bar(x, durs / k, width=0.66, color=cols, edgecolor='#222222', linewidth=0.4)
    if n:
        ax1.axhline(durs.max() / k, color='#222222', lw=0.7, ls='--')
        ax1.axhline(durs.mean() / k, color='#777777', lw=0.7, ls=':')
        fig.legend(handles=[Line2D([], [], color='#222222', lw=0.7, ls='--', label=st.t('最晚完成', 'Makespan')),
                            Line2D([], [], color='#777777', lw=0.7, ls=':', label=st.t('平均', 'Mean'))], loc='outside lower right', ncol=2)
    ax1.set_ylabel(tlabel)
    for ax, lab in ((ax0, 'a'), (ax1, 'b')):
        ax.set_xticks(x, names, rotation=0 if n <= 6 else 45, ha='center' if n <= 6 else 'right')
        ax.yaxis.set_major_locator(MaxNLocator(5))
        panel_label(ax, lab, st)
    cv_d = float(lengths.std() / lengths.mean()) if n and lengths.mean() > 0 else 0.0
    cv_t = float(durs.std() / durs.mean()) if n and durs.mean() > 0 else 0.0
    cap = {
        'zh': f"{alg_name(run, st)} 方案的每车工作量：（a）行驶里程，柱顶数字为任务点数（/趟数）；（b）作业用时，虚线为最晚完成、点线为平均。"
              f"里程变异系数 {cv_d:.2f}，用时变异系数 {cv_t:.2f}（越小越均衡）。",
        'en': f"Per-vehicle workload of the {alg_name(run, st)} plan: (a) distance travelled, numbers on bars give tasks (/trips); (b) working time, "
              f"dashed = makespan, dotted = mean. Coefficient of variation: distance {cv_d:.2f}, time {cv_t:.2f} (lower is more balanced).",
    }
    return fig, cap, {'per_vehicle': per}


def schedule(ctx: D.Context, st: Style, opts: dict):
    run = ctx.run
    inst = run['instance']
    speed = float((inst.get('vehicles') or {}).get('speed_mps') or 1.0)
    rts = run.get('routes') or []
    n = len(rts)
    fig, axs = figure(st, height_in=_hbar_height(n, 0.75, 0.24))
    ax = axs[0][0]
    sch = [D.schedule(r.get('polyline') or [], D.route_stops(r, inst), speed, inst.get('depot')) for r in rts]
    end = max([s['end'] for s in sch] or [1.0])
    k, tlabel = _time(end, st)
    rows = []
    for i, (r, s) in enumerate(zip(rts, sch)):
        y = n - 1 - i
        trav = [(a / k, (b - a) / k) for kind, a, b in s['segments'] if kind == 'travel' and b > a]
        serv = [(a / k, (b - a) / k) for kind, a, b in s['segments'] if kind == 'service' and b > a]
        ax.broken_barh(trav, (y - 0.3, 0.6), facecolors=color(i), edgecolors='none')
        if serv:
            ax.broken_barh(serv, (y - 0.3, 0.6), facecolors='#3A3A3A', edgecolors='none')
        arr = np.array([a['t'] for a in s['arrivals']], float) / k
        ax.plot(arr, np.full(len(arr), y), ls='none', marker='o', ms=2.2, mfc='white', mec='#222222', mew=0.4, zorder=5)
        for tr in s['returns']:
            ax.plot([tr / k, tr / k], [y - 0.4, y + 0.4], color='black', lw=1.0, zorder=6)
        rows += [{'vehicle': r.get('vehicle_name'), 'event': 'arrival', 'task': a['task'], 't_s': a['t']} for a in s['arrivals']]
        rows += [{'vehicle': r.get('vehicle_name'), 'event': 'depot_return', 'task': None, 't_s': tr} for tr in s['returns']]
        rows.append({'vehicle': r.get('vehicle_name'), 'event': 'end', 'task': None, 't_s': s['end']})
    ax.axvline(end / k, color='#222222', lw=0.7, ls='--')
    ax.text(end / k, n - 0.5, st.t('最晚完成 ', 'makespan '), fontsize=st.fs - 1.5, va='bottom', ha='right')
    ax.set_yticks(range(n), [vehicle_name(i, r.get('vehicle_name') or '', st) for i, r in enumerate(rts)][::-1])
    ax.set_xlim(0, end / k * 1.04)
    ax.set_ylim(-0.6, n - 0.2)
    ax.set_xlabel(tlabel)
    ax.tick_params(axis='y', length=0)
    ax.spines['left'].set_visible(False)
    handles = [Patch(fc=color(0), label=st.t('行驶（各车颜色）', 'Travel (vehicle colour)')),
               Line2D([], [], ls='none', marker='o', ms=2.6, mfc='white', mec='#222222', mew=0.4, label=st.t('到达任务点', 'Task reached'))]
    if any(kind == 'service' for s in sch for kind, *_ in s['segments']):
        handles.insert(1, Patch(fc='#3A3A3A', label=st.t('作业', 'Service')))
    if any(s['returns'] for s in sch):
        handles.append(Line2D([], [], color='black', lw=1.0, label=st.t('回库卸载', 'Depot return')))
    fig.legend(handles=handles, loc='outside lower center', ncol=len(handles))
    cap = {
        'zh': f"{alg_name(run, st)} 方案的车辆时间线（按规划车速 {speed} m/s 与作业时间推算）：色条为行驶，白点为到达任务点，"
              f"黑竖线为回车库卸载，虚线为最晚完成（{end:.0f} s）。",
        'en': f"Vehicle timelines of the {alg_name(run, st)} plan (planned speed {speed} m/s plus service times): coloured bars are travel, "
              f"white dots task arrivals, black ticks returns to the depot, the dashed line the makespan ({end:.0f} s).",
    }
    return fig, cap, {'events': rows}


def algo_compare(ctx: D.Context, st: Style, opts: dict):
    runs = [r for r in ctx.runs if r.get('state') == 'done' and (r.get('objective') or {}).get('value') is not None]
    runs.sort(key=lambda r: (not (r.get('check') or {}).get('valid'), r['objective']['value']))
    n = len(runs)
    fig, axs = figure(st, height_in=_hbar_height(n, 0.75, 0.22), ncols=2, sharey=True, gridspec_kw={'width_ratios': [1.6, 1]})
    ax0, ax1 = axs[0]
    y = np.arange(n)[::-1]
    vals = np.array([r['objective']['value'] for r in runs], float)
    times = np.array([max((r.get('metrics') or {}).get('wall_s') or 0, 1e-3) for r in runs], float)
    for i, r in enumerate(runs):
        ok = (r.get('check') or {}).get('valid')
        j = alg_index(r['algorithm']['id'])
        c = color(j)
        ax0.hlines(y[i], vals.min() if n else 0, vals[i], color='#BBBBBB', lw=0.6, zorder=1)
        ax0.plot(vals[i], y[i], ls='none', marker=marker(j), ms=4.2, mfc=c if ok else 'white', mec=c, mew=0.9, zorder=3)
        ax1.plot(times[i], y[i], ls='none', marker=marker(j), ms=4.2, mfc=c if ok else 'white', mec=c, mew=0.9, zorder=3)
    ax0.set_yticks(y, [alg_name(r, st) for r in runs])
    ax0.set_ylim(-0.6, n - 0.4)
    ax0.set_xlabel(_objective(runs[0] if runs else None, st))
    log_axis(ax1, 'x')
    ax1.set_xlabel(qlabel(st, '计算时间', 'Wall-clock time', 's'))
    for ax, lab in ((ax0, 'a'), (ax1, 'b')):
        ax.grid(axis='x', which='major')
        panel_label(ax, lab, st)
    if any(not (r.get('check') or {}).get('valid') for r in runs):
        fig.legend(handles=[Line2D([], [], ls='none', marker='o', ms=4, mfc='#555555', mec='#555555', label=st.t('通过独立验算', 'Passed independent check')),
                            Line2D([], [], ls='none', marker='o', ms=4, mfc='white', mec='#555555', label=st.t('未通过验算', 'Failed check'))],
                   loc='outside lower center', ncol=2)
    best = runs[0] if runs else None
    cap = {
        'zh': f"同一任务地图上 {n} 个算法的比较：（a）{_objective(best, st).split('/')[0]}，实心为通过独立验算、空心为未通过；（b）计算时间（对数轴）。"
              f"各算法的时间预算与种子见源数据；最好的有效方案为 {alg_name(best, st) if best else '—'}。",
        'en': f"Comparison of {n} algorithms on the same task map: (a) {_objective(best, st).split(' (')[0].lower()}; filled markers passed the independent "
              f"feasibility check, hollow ones did not; (b) wall-clock time (log scale). Budgets and seeds are in the source data; best valid plan: {alg_name(best, st) if best else '—'}.",
    }
    rows = [{'run_id': r['run_id'], 'algorithm': r['algorithm']['id'], 'objective': r['objective']['value'], 'unit': r['objective'].get('unit'),
             'check_valid': (r.get('check') or {}).get('valid'), 'wall_s': (r.get('metrics') or {}).get('wall_s'),
             'time_limit_s': (r.get('params') or {}).get('time_limit_s'), 'seed': (r.get('params') or {}).get('seed')} for r in runs]
    return fig, cap, {'runs': rows}


def convergence(ctx: D.Context, st: Style, opts: dict):
    from ..analysis import trace_matches, _valid_trace
    done = [r for r in ctx.runs if r.get('trace') and r.get('state') == 'done']
    runs = [r for r in done if trace_matches(r)]
    other = [r for r in done if not trace_matches(r)]
    fig, axs = figure(st, 0.62)
    ax = axs[0][0]
    rows = []
    for r in runs:
        i = alg_index(r['algorithm']['id'])
        tr = np.asarray(r['trace'], float)
        t = np.maximum(tr[:, 0], 1e-3)
        end = max(float((r.get('metrics') or {}).get('wall_s') or t[-1]), t[-1])
        ts, vs = np.r_[t, end], np.r_[tr[:, 1], tr[-1, 1]]
        ax.step(ts, vs, where='post', color=color(i), ls=dash(i), lw=1.0, label=alg_name(r, st))
        ax.plot(ts[0], vs[0], ls='none', marker=marker(i), ms=3, mfc=color(i), mec='white', mew=0.3)
        rows += [{'run_id': r['run_id'], 'algorithm': r['algorithm']['id'], 't_s': float(a), 'best_objective': float(b)} for a, b in tr]
    log_axis(ax, 'x')
    ax.set_xlabel(qlabel(st, '计算时间', 'Wall-clock time', 's'))
    ax.set_ylabel(_objective(runs[0] if runs else None, st, best_so_far=True))
    ax.yaxis.set_major_locator(MaxNLocator(5))
    ax.grid(which='major', axis='both')
    ax.legend(loc='best')
    def reason(r, lang):
        if (r.get('trace_contract') or {}).get('semantic') != 'reported_objective':
            return '代理或未知目标语义' if lang == 'zh' else 'proxy or unknown objective semantics'
        if not _valid_trace(r):
            return '轨迹结构/时间顺序非法或非单调 incumbent' if lang == 'zh' else 'invalid structure/timing or non-monotone incumbent'
        return '轨迹终值与目标值不一致' if lang == 'zh' else 'endpoint mismatch with reported objective'
    skip_zh = '；'.join(f"{alg_name(r, st)}：{reason(r, 'zh')}，未画入" for r in other)
    skip_en = '; '.join(f"{alg_name(r, st)}: {reason(r, 'en')}; excluded" for r in other)
    cap = {
        'zh': '求解过程中当前最好解随计算时间的变化（任一时刻停止求解所能得到的方案质量），横轴为对数时间；各曲线首点为第一个已记录的可行解（此前未知）。' + (f'{skip_zh}。' if other else ''),
        'en': 'Best-so-far objective against wall-clock time (anytime behaviour; log time axis). The first marker is the first recorded feasible solution; earlier telemetry is unknown.'
              + (f' Note: {skip_en}.' if other else ''),
    }
    return fig, cap, {'trace': rows}


# ------------------------------------------------------------- simulation --
def plan_vs_exec(ctx: D.Context, st: Style, opts: dict):
    sim = ctx.sim
    vs = sim.get('vehicles') or []
    n = len(vs)
    fig, axs = figure(st, height_in=_hbar_height(n, 0.85, 0.24), ncols=2, sharey=True)
    ax0, ax1 = axs[0]
    y = np.arange(n)[::-1]
    dur_p = np.array([v.get('planned_duration_s') or np.nan for v in vs], float)
    dur_e = np.array([v.get('duration_s') or np.nan for v in vs], float)
    len_p = np.array([v.get('planned_length_m') or np.nan for v in vs], float)
    len_e = np.array([v.get('distance_m') or np.nan for v in vs], float)
    k, tlabel = _time(np.nanmax(np.r_[dur_p, dur_e, [0]]), st, '完成用时', 'Completion time')
    for ax, P, E, div in ((ax0, dur_p, dur_e, k), (ax1, len_p, len_e, 1.0)):
        for i in range(n):
            c = color(i)
            ax.plot([P[i] / div, E[i] / div], [y[i], y[i]], color='#BBBBBB', lw=1.0, zorder=1)
            ax.plot(P[i] / div, y[i], ls='none', marker='o', ms=4, mfc='white', mec=c, mew=0.9, zorder=3)
            ax.plot(E[i] / div, y[i], ls='none', marker='o', ms=4, mfc=c, mec=c, zorder=3)
            if np.isfinite(P[i]) and P[i] > 0 and np.isfinite(E[i]):
                ax.annotate(f'{100 * (E[i] - P[i]) / P[i]:+.1f}%', (max(P[i], E[i]) / div, y[i]), xytext=(4, 0), textcoords='offset points',
                            fontsize=st.fs - 1.5, va='center', ha='left')
        ax.grid(axis='x')
        ax.margins(x=0.12)
    ax0.set_yticks(y, [vehicle_name(i, D.vehicle_label(sim, v['name']), st) for i, v in enumerate(vs)])
    ax0.set_ylim(-0.6, n - 0.4)
    ax0.set_xlabel(tlabel)
    ax1.set_xlabel(qlabel(st, '行驶里程', 'Distance', 'm'))
    panel_label(ax0, 'a', st)
    panel_label(ax1, 'b', st)
    fig.legend(handles=[Line2D([], [], ls='none', marker='o', ms=4, mfc='white', mec='#444444', mew=0.9, label=st.t('规划', 'Planned')),
                        Line2D([], [], ls='none', marker='o', ms=4, mfc='#444444', mec='#444444', label=st.t('仿真执行', 'Executed (simulation)'))],
               loc='outside lower center', ncol=2)
    m = sim.get('metrics') or {}
    def metric(key):
        value = m.get(key)
        return f'{value:.0f}' if value is not None and np.isfinite(value) else '—'
    cap = {
        'zh': f"规划与 Gazebo 仿真执行的对比（每车）：（a）完成用时；（b）行驶里程。空心为规划，实心为执行，数字为相对偏差。"
              f"整体最晚完成 {metric('planned_makespan_s')} s → {metric('makespan_s')} s，总里程 {metric('planned_total_distance_m')} m → {metric('total_distance_m')} m。"
              '规划从车库计算；执行还含停车接驳、转向与交通等待，未完成的用时留空。',
        'en': f"Planned versus executed (Gazebo) per vehicle: (a) completion time; (b) distance. Hollow = planned, filled = executed; numbers give the relative deviation. "
              f"Fleet makespan {metric('planned_makespan_s')} s → {metric('makespan_s')} s, total distance {metric('planned_total_distance_m')} m → {metric('total_distance_m')} m. "
              'Plans start at the depot; execution also includes parking transfers, turning and traffic waits. Incomplete durations are unavailable.',
    }
    rows = [{'vehicle': v['name'], 'planned_duration_s': v.get('planned_duration_s'), 'executed_duration_s': v.get('duration_s'),
             'planned_length_m': v.get('planned_length_m'), 'executed_length_m': v.get('distance_m'), 'tasks_done': v.get('tasks_done'),
             'tasks_total': v.get('tasks_total'), 'state': v.get('state')} for v in vs]
    return fig, cap, {'vehicles': rows}


def tracking_error(ctx: D.Context, st: Style, opts: dict):
    """(a) empirical CDF of the cross-track error per vehicle, (b) mean / 95th percentile / maximum per vehicle."""
    sim = ctx.sim
    ct = D.cross_track(sim)
    names = sorted(ct)
    if not names:
        raise LookupError('没有有效的跟踪误差记录（排队或取消不产生误差测量）')
    fig, axs = figure(st, 0.36, ncols=2, gridspec_kw={'width_ratios': [1.25, 1]})
    ax0, ax1 = axs[0]
    rows, stats = [], []
    for i, n in enumerate(names):
        e = np.sort(ct[n]['e'])
        F = np.arange(1, len(e) + 1) / len(e)
        ax0.step(e * 100, F, where='post', color=color(i), ls=dash(i), lw=1.0, label=vehicle_name(i, D.vehicle_label(sim, n), st))
        p95 = float(np.percentile(e, 95))
        ax0.plot(p95 * 100, 0.95, ls='none', marker=marker(i), ms=3.2, mfc=color(i), mec='white', mew=0.3)
        stats.append((n, float(e.mean()), p95, float(e.max()), len(e)))
        rows += [{'vehicle': n, 't_s': float(a), 'cross_track_m': float(b), 'source': ct[n]['source']} for a, b in zip(ct[n]['t'], ct[n]['e'])]
    ax0.axhline(0.95, color='#999999', lw=0.5, ls=':')
    ax0.set_xlabel(qlabel(st, '横向偏差', 'Cross-track error', 'cm'))
    ax0.set_ylabel(st.t('累计比例', 'Cumulative fraction'))
    ax0.set_ylim(0, 1.02)
    ax0.set_xlim(left=0)
    ax0.grid(which='major')
    ax0.legend(loc='lower right')
    x = np.arange(len(stats))
    wbar = 0.26
    for j, (lab_zh, lab_en, idx, hatch) in enumerate((('均值', 'Mean', 1, None), ('95 百分位', '95th percentile', 2, '////'), ('最大', 'Maximum', 3, None))):
        vals = [s[idx] * 100 for s in stats]
        ax1.bar(x + (j - 1) * wbar, vals, width=wbar, color=[color(i) for i in range(len(stats))], alpha=(0.45, 0.75, 1.0)[j],
                edgecolor='#222222', linewidth=0.4, hatch=hatch, label=st.t(lab_zh, lab_en))
    ax1.set_xticks(x, [vehicle_name(i, D.vehicle_label(sim, s[0]), st) for i, s in enumerate(stats)])
    ax1.set_ylabel(qlabel(st, '横向偏差', 'Cross-track error', 'cm'))
    ax1.yaxis.set_major_locator(MaxNLocator(5))
    ax1.legend(handles=[Patch(fc='#888888', alpha=a, ec='#222222', lw=0.4, hatch=h, label=st.t(z, e))
                        for z, e, a, h in (('均值', 'Mean', 0.45, None), ('95 百分位', 'P95', 0.75, '////'), ('最大', 'Max', 1.0, None))],
               loc='upper left', ncol=3, handlelength=1.2, columnspacing=0.8)
    ax1.margins(y=0.18)
    panel_label(ax0, 'a', st)
    panel_label(ax1, 'b', st)
    allv = np.concatenate([ct[n]['e'] for n in names]) if names else np.zeros(1)
    cap = {
        'zh': f"仿真中的路径跟踪误差（真值位置到被跟踪规划路线的横向距离，约 0.5 s 采样）：（a）每车误差的经验累积分布，标记为 95 百分位；"
              f"（b）每车均值、95 百分位与最大值。全体均值 {allv.mean() * 100:.2f} cm，95 百分位 {np.percentile(allv, 95) * 100:.2f} cm，"
              f"最大 {allv.max() * 100:.2f} cm（n = {allv.size} 个记录点）；较大偏差集中在路线转角处。"
              f"仿真在线统计（每个控制周期）给出的最大值为 {(sim.get('metrics') or {}).get('cross_track_max_m', 0) * 100:.1f} cm，两者采样频率不同。",
        'en': f"Path-tracking error in simulation (lateral distance from the ground-truth position to the tracked path, ≈0.5 s sampling): (a) empirical "
              f"CDF per vehicle, markers at the 95th percentile; (b) mean, 95th percentile and maximum per vehicle. Overall mean {allv.mean() * 100:.2f} cm, "
              f"95th percentile {np.percentile(allv, 95) * 100:.2f} cm, maximum {allv.max() * 100:.2f} cm (n = {allv.size} recorded samples); large errors occur at route corners. "
              f"The simulator's online statistic (every control cycle) gives a maximum of {(sim.get('metrics') or {}).get('cross_track_max_m', 0) * 100:.1f} cm; the sampling rates differ.",
    }
    if all(ct[n]['source'] == 'control_cycle' for n in names):
        cap = {
            'zh': (f'控制周期记录的路径跟踪横向偏差，与在线指标使用同一批测量；排队、停留中未测量的周期留空。'
                   f'（a）每车经验分布；（b）均值、95 百分位及最大值。共 {allv.size} 次测量，'
                   f'均值 {allv.mean() * 100:.2f} cm，最大 {allv.max() * 100:.2f} cm。'),
            'en': (f'Cross-track error recorded at control cycles, using the same measurements as the online metric. '
                   f'Unmeasured waiting/service cycles are excluded. (a) Empirical distributions; (b) mean, P95 and maximum. '
                   f'{allv.size} measurements; mean {allv.mean() * 100:.2f} cm, maximum {allv.max() * 100:.2f} cm.')}
    return fig, cap, {'cross_track': rows, 'per_vehicle': [{'vehicle': s[0], 'mean_m': s[1], 'p95_m': s[2], 'max_m': s[3], 'n': s[4]} for s in stats]}


def separation(ctx: D.Context, st: Style, opts: dict):
    sim = ctx.sim
    S = D.separation_series(sim)
    fig, axs = figure(st, 0.6)
    ax = axs[0][0]
    t, d = S['t'], S['d']
    k, tlabel = _time(float(t[-1]) if len(t) else 1, st)
    ax.plot(t / k, d, color=color(0), lw=0.8)
    log_axis(ax, 'y')
    thr = D.SIM_BODY_DIAGONAL
    ax.axhline(thr, color='#C0392B', lw=0.7, ls='--')
    lo = max(min(float(d.min()) * 0.7, thr * 0.5), 1e-3)
    ax.set_ylim(lo, float(d.max()) * 1.6)
    ax.fill_between(t / k, lo, thr, color='#C0392B', alpha=0.08, lw=0)
    # label where the curve is far above the line, so it never crosses the data
    clear = (d > thr * 6) if len(d) else np.zeros(0, bool)
    xl = float(t[np.argmax(clear)] / k) if clear.any() else float(t[len(t) // 2] / k)
    ax.text(xl, thr * 1.12, st.t('低于此线车体可能接触', 'below: bodies may touch'), color='#C0392B', fontsize=st.fs - 1.5, ha='left', va='bottom')
    i = int(np.argmin(d))
    ax.plot(t[i] / k, d[i], ls='none', marker='v', ms=4, mfc='#C0392B', mec='white', mew=0.4, zorder=5)
    ax.annotate(f'{d[i]:.2f} m', (t[i] / k, d[i]), xytext=(-7, 0), textcoords='offset points', fontsize=st.fs - 1.5, ha='right', va='center',
                bbox={'facecolor': 'white', 'edgecolor': 'none', 'pad': 0.5, 'alpha': 0.8})
    ax.set_xlabel(tlabel)
    ax.set_ylabel(qlabel(st, '最小车间距', 'Min. vehicle spacing', 'm'))
    ax.grid(axis='y', which='major')
    # where does the closest approach happen: at the shared depot (start / return) or in the field?
    plan = sim.get('execution_plan') or {}
    depot = ((plan.get('instance') or {}).get('depot')) or ((ctx.run or {}).get('instance') or {}).get('depot')
    near = None
    if depot is not None:
        a, b = str(S['pair'][i]).split('–')
        pa = D.trail_xy_at(sim['trails'][a], np.array([t[i]]))[0]
        pb = D.trail_xy_at(sim['trails'][b], np.array([t[i]]))[0]
        near = max(np.hypot(*(pa - depot)), np.hypot(*(pb - depot))) < 5.0
    below = float(np.mean(d < thr)) * 100
    where_zh = '，发生在共用车库附近（出发 / 返回排队）' if near else ('，发生在田间' if near is not None else '')
    where_en = ', at the shared depot (departure / return)' if near else (', in the field' if near is not None else '')
    cap = {
        'zh': f"仿真中任意两车之间的最小距离（车体中心，0.5 s 网格，对数纵轴）。红色虚线为车体对角线 {thr:.2f} m，低于该值车体可能接触；"
              f"{below:.1f}% 的时间低于该值。最近距离 {d[i]:.2f} m（t = {t[i]:.0f} s{where_zh}）。仿真中各车之间的碰撞被屏蔽（不会表现为碰撞），"
              f"规划不建模车间冲突、执行不做车间避让，因此多车避碰未作为验收项。",
        'en': f"Minimum distance between any two vehicles in simulation (body centres, 0.5 s grid, log scale). The red dashed line is the body diagonal ({thr:.2f} m): "
              f"below it the bodies may touch; {below:.1f}% of the time is below it. Closest approach {d[i]:.2f} m at t = {t[i]:.0f} s{where_en}. Robot–robot contacts are "
              f"masked in the simulation (they never register as collisions); the planner does not model inter-vehicle conflicts and execution does not enforce avoidance, "
              f"so collision avoidance is not claimed.",
    }
    if sim.get('traffic', {}).get('robot_collisions'):
        cap['zh'] = (f'任意两车中心最小距离（0.5 s 网格，不能代替整段独立验算）；车体对角线 {thr:.2f} m。'
                     f'最近距离 {d[i]:.2f} m，低于对角线的采样占比 {below:.1f}%。'
                     '本次启用物理车间碰撞；整段任务互斥预约、回独立车位。等待计入实际用时，非并行避碰算法。'
                     '完整时间插值净空与物理接触记录见实验结果。')
        cap['en'] = (f'Minimum vehicle-centre spacing (0.5 s grid, separate continuous-segment checking required). '
                     f'Body diagonal {thr:.2f} m; minimum {d[i]:.2f} m; {below:.1f}% of samples below the diagonal. '
                     'Physical robot contacts are enabled. Whole missions are mutually exclusive with separate parking. '
                     'Measured time includes queueing; this is not a concurrent multi-agent avoidance algorithm. '
                     'Interpolated clearance checks and physical contacts are retained in the experiment record.')
    rows = [{'t_s': float(a), 'min_distance_m': float(b), 'pair': p} for a, b, p in zip(t, d, S['pair'])]
    return fig, cap, {'separation': rows}


def task_progress(ctx: D.Context, st: Style, opts: dict):
    sim = ctx.sim
    plan = sim.get('execution_plan') or {}
    original = sim.get('planning_solution') or ctx.run or {}
    inst = original.get('instance') or plan.get('instance') or {}
    speed = float(inst.get('vehicles', {}).get('speed_mps') or 0) or None
    planned = []
    for route in original.get('routes') or []:
        if speed:
            planned += [a['completed_s'] for a in D.schedule(route['polyline'], D.route_stops(route, inst), speed, None)['arrivals']]
    complete_records = any('serviced_at' in t for t in sim.get('tasks') or [])
    time_key = 'serviced_at' if complete_records else 't_sim'
    origin = sim.get('execution_started_sim_s') or 0.0
    executed = sorted(t[time_key] - origin for t in sim.get('tasks') or []
                      if t.get('state') == 'reached' and t.get(time_key) is not None)
    planned.sort()
    total = len(sim.get('tasks') or [])
    fig, axs = figure(st, 0.6)
    ax = axs[0][0]
    end = max(planned[-1] if planned else 0, executed[-1] if executed else 0, 1)
    k, tlabel = _time(end, st)
    if planned:
        ax.step(np.r_[0, planned] / k, np.arange(len(planned) + 1), where='post', color='#444444', ls='--', lw=0.9, label=st.t('规划', 'Planned'))
    if executed:
        ax.step(np.r_[0, executed] / k, np.arange(len(executed) + 1), where='post', color=color(0), lw=1.1, label=st.t('仿真执行', 'Executed (simulation)'))
    ax.axhline(total, color='#999999', lw=0.5, ls=':')
    ax.set_xlabel(tlabel)
    ax.set_ylabel(st.t('累计完成任务点数', 'Tasks completed') if complete_records else st.t('累计到达任务点数', 'Tasks reached'))
    ax.set_ylim(0, total * 1.06 + 0.5)
    ax.set_xlim(left=0)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
    ax.grid(axis='y')
    ax.legend(loc='lower right')
    lag = ''
    if planned and executed and len(executed) == len(planned):
        delay = np.asarray(executed) - np.asarray(planned)
        lag = st.t(f'；第 k 次完成的时间中位滞后 {np.median(delay):.0f} s', f'; median lag of the k-th completion {np.median(delay):.0f} s')
    cap = {
        'zh': f"任务进度：规划按车库出发、车速与停留时间推算完成时刻；执行含接驳、转向和排队（{len(executed)}/{total}）{lag}。"
              + ('执行从共同任务开始计时，以作业停留完成为准。' if complete_records and 'execution_started_sim_s' in sim else
                 '历史记录沿用原仿真时钟；缺少作业完成时刻时仅表示到达，不能认定作业已完成。'),
        'en': f"Task progress: planned completion times include travel from the depot and service; execution includes transfers, turning and queueing ({len(executed)}/{total}){lag}. "
              + ('Execution is relative to the common mission start and counts completed service.' if complete_records and 'execution_started_sim_s' in sim else
                 'Legacy records retain the simulation clock; without service completion timestamps, the count means arrivals only.'),
    }
    rows = [{'series': 'planned', 't_s': float(x)} for x in planned] + [{'series': 'executed', 't_s': float(x)} for x in executed]
    return fig, cap, {'arrivals': rows}
