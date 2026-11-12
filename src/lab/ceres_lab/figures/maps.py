"""Map figures: task map, multi-vehicle routes, side-by-side route comparison, simulation tracks, map → planning geometry."""
from __future__ import annotations

import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.patches import Polygon as MPoly

from . import data as D
from .style import FIELD, OBSTACLE, SIM_BODY, Style, color, figure, marker, north_arrow, panel_label, qlabel, scale_bar
from .names import alg_name, obstacle_name, vehicle_name


def bounds(inst: dict) -> tuple[float, float, float, float]:
    pts = np.asarray(inst['boundary'], float)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def map_aspect(inst: dict, lo: float = 0.42, hi: float = 1.25) -> float:
    x0, y0, x1, y1 = bounds(inst)
    return min(hi, max(lo, (y1 - y0) / max(1e-6, x1 - x0)))


def draw_field(ax, inst: dict, st: Style, *, lanes=None, tasks: bool = True, depot: bool = True, legend: list | None = None):
    ax.add_patch(MPoly(inst['boundary'], closed=True, fc=FIELD['boundary_fill'], ec=FIELD['boundary_edge'], lw=0.8, zorder=1))
    if inst.get('plots'):
        for pl in inst['plots']:
            ax.add_patch(MPoly(pl['polygon'], closed=True, fc=FIELD['plot_fill'], ec=FIELD['plot_edge'], lw=0.5, zorder=2))
        if legend is not None:
            legend.append(Patch(fc=FIELD['plot_fill'], ec=FIELD['plot_edge'], lw=0.5, label=st.t('地块', 'Plot')))
    if lanes:
        ax.add_collection(LineCollection(lanes, colors=FIELD['lane'], linewidths=0.35, linestyles=(0, (2.5, 1.5)), zorder=3))
        if legend is not None:
            legend.append(Line2D([], [], color=FIELD['lane'], lw=0.6, ls=(0, (2.5, 1.5)), label=st.t('行间通道', 'Inter-row lane')))
    kinds = []
    nogo = False
    for o in inst.get('obstacles') or []:
        if o.get('nogo'):
            ax.add_patch(MPoly(o['polygon'], closed=True, fc='none', ec=FIELD['nogo_edge'], hatch='////', lw=0.6, zorder=4))
            nogo = True
            continue
        k = o.get('kind', 'other')
        fc, ec = OBSTACLE.get(k, OBSTACLE['other'])
        ax.add_patch(MPoly(o['polygon'], closed=True, fc=fc, ec=ec, lw=0.5, zorder=4))
        if k not in kinds:
            kinds.append(k)
    if legend is not None:
        for k in kinds:
            fc, ec = OBSTACLE.get(k, OBSTACLE['other'])
            legend.append(Patch(fc=fc, ec=ec, lw=0.5, label=obstacle_name(k, st)))
        if nogo:
            legend.append(Patch(fc='none', ec=FIELD['nogo_edge'], hatch='////', lw=0.6, label=st.t('禁行区', 'No-go zone')))
    if tasks:
        xy = np.asarray([t['xy'] for t in inst.get('tasks') or []], float).reshape(-1, 2)
        dem = np.asarray([t.get('demand', 1) for t in inst.get('tasks') or []], float)
        if len(xy):
            ax.scatter(xy[:, 0], xy[:, 1], s=4 + 3 * np.clip(dem, 1, 6), facecolors='white', edgecolors=FIELD['task'], linewidths=0.5, zorder=10)
            if legend is not None:
                legend.append(Line2D([], [], ls='none', marker='o', mfc='white', mec='black', mew=0.5, ms=3.2, label=st.t('任务点', 'Task')))
    if depot and inst.get('depot') is not None:
        draw_depot(ax, inst['depot'])
        if legend is not None:
            legend.append(Line2D([], [], ls='none', marker='s', mfc='black', mec='white', mew=0.5, ms=4.5, label=st.t('车库', 'Depot')))


def draw_depot(ax, xy):
    ax.plot([xy[0]], [xy[1]], ls='none', marker='s', ms=5, mfc=FIELD['depot'], mec='white', mew=0.6, zorder=40)


def finish(ax, inst: dict, st: Style, *, frame: dict | None = None, axes: bool = True, margin: float = 0.03):
    x0, y0, x1, y1 = bounds(inst)
    dx, dy = (x1 - x0) * margin, (y1 - y0) * margin
    ax.set_xlim(x0 - dx, x1 + dx)
    ax.set_ylim(y0 - dy, y1 + dy)
    ax.set_aspect('equal', adjustable='box')
    if axes:
        ax.set_xlabel(qlabel(st, 'x', 'x', 'm'))
        ax.set_ylabel(qlabel(st, 'y', 'y', 'm'))
    else:
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        scale_bar(ax, st, x0 - dx, y0 - dy, x1 + dx, y1 + dy)
    crs = str((frame or {}).get('crs') or '')
    if 'EPSG:326' in crs or 'EPSG:327' in crs or 'UTM' in crs.upper():
        north_arrow(ax, st)        # projected UTM frame: +y is grid north


def arrows(ax, poly: list, c: str, n: int = 4):
    """A few direction arrows along a route (at 15 %, 38 %, 62 %, 85 % of its length)."""
    P = np.asarray(poly, float)
    if len(P) < 2:
        return
    cum = D.cumlen(poly)
    L = cum[-1]
    if L <= 0:
        return
    for f in np.linspace(0.15, 0.85, n):
        d = f * L
        k = int(np.searchsorted(cum, d, side='right')) - 1
        k = max(0, min(k, len(P) - 2))
        seg = P[k + 1] - P[k]
        sl = float(np.hypot(*seg))
        if sl < 1e-9:
            continue
        a = P[k] + seg * ((d - cum[k]) / sl)
        b = a + seg / sl * min(sl, L * 0.012 + 1e-6)
        ax.annotate('', xy=b, xytext=a, arrowprops={'arrowstyle': '-|>,head_length=0.35,head_width=0.18', 'color': c, 'lw': 0.8,
                                                   'shrinkA': 0, 'shrinkB': 0}, zorder=25)


def legend_below(fig, ax, handles: list, st: Style, ncol: int | None = None):
    if not handles:
        return
    n = ncol or (4 if st.size != 'single' else 3)
    fig.legend(handles=handles, loc='outside lower center', ncol=min(n, len(handles)), handlelength=1.4, columnspacing=0.9)


# ----------------------------------------------------------------- figures --
def scenario_map(ctx: D.Context, st: Style, opts: dict):
    sc = ctx.scenario
    inst = sc['instance']
    fig, axs = figure(st, map_aspect(inst) + 0.16)
    ax = axs[0][0]
    leg: list = []
    draw_field(ax, inst, st, lanes=ctx.lanes, legend=leg)
    finish(ax, inst, st, frame=sc.get('frame'), axes=opts.get('axes', True))
    legend_below(fig, ax, leg, st)
    x0, y0, x1, y1 = bounds(inst)
    n, dem = len(inst['tasks']), sum(t.get('demand', 1) for t in inst['tasks'])
    veh = inst.get('vehicles') or {}
    rows = [{'layer': 'task', 'id': t['id'], 'x': t['xy'][0], 'y': t['xy'][1], 'demand': t.get('demand', 1)} for t in inst['tasks']]
    rows += [{'layer': 'depot', 'id': 'depot', 'x': inst['depot'][0], 'y': inst['depot'][1], 'demand': None}]
    rows += [{'layer': 'obstacle', 'id': o['id'], 'x': None, 'y': None, 'demand': None, 'kind': o.get('kind'), 'vertices': len(o['polygon'])} for o in inst.get('obstacles') or []]
    cap = {
        'zh': f"任务地图“{sc.get('title')}”：场地 {x1 - x0:.0f} m × {y1 - y0:.0f} m，{len(inst.get('plots') or [])} 块地，"
              f"{len(inst.get('obstacles') or [])} 处障碍 / 禁行区，{n} 个任务点（总需求 {dem}，圆点大小表示需求），"
              f"{veh.get('count', '—')} 台车从车库（方块）出发并返回。坐标为局部平面坐标（米）。",
        'en': f"Task map “{sc.get('id')}”: a {x1 - x0:.0f} m × {y1 - y0:.0f} m field with {len(inst.get('plots') or [])} plots, "
              f"{len(inst.get('obstacles') or [])} obstacles / no-go zones and {n} tasks (total demand {dem}; marker size ∝ demand); "
              f"{veh.get('count', '—')} vehicles start from and return to the depot (square). Local planar coordinates in metres.",
    }
    return fig, cap, {'layers': rows}


def _route_layer(ax, run: dict, st: Style, *, hidden: set | None = None, legend: list | None = None, with_stats: bool = True):
    inst = run['instance']
    per = {v['vehicle']: v for v in (run.get('metrics') or {}).get('per_vehicle') or []}
    tasks = {t['id']: t for t in inst['tasks']}
    rows = []
    for i, r in enumerate(run.get('routes') or []):
        name = r.get('vehicle_name') or f'V{i + 1}'
        if hidden and name in hidden:
            continue
        c = color(i)
        P = np.asarray(r.get('polyline') or [], float)
        if len(P) > 1:
            ax.plot(P[:, 0], P[:, 1], color=c, lw=1.0, solid_joinstyle='round', solid_capstyle='round', zorder=20)
            arrows(ax, r['polyline'], c)
        served = [tasks[sv['task']]['xy'] for sv in r.get('services') or [] if sv.get('task') in tasks]
        if served:
            S = np.asarray(served, float)
            ax.plot(S[:, 0], S[:, 1], ls='none', marker=marker(i), ms=3.0, mfc=c, mec='white', mew=0.35, zorder=30)
        pv = per.get(name, {})
        if legend is not None:
            lab = vehicle_name(i, name, st)
            if with_stats and pv:
                lab += st.t(f"：{pv.get('tasks')} 点，{pv.get('length_m', 0):.0f} m" + (f"，{pv.get('trips')} 趟" if (pv.get('trips') or 1) > 1 else ''),
                            f": {pv.get('tasks')} tasks, {pv.get('length_m', 0):.0f} m" + (f", {pv.get('trips')} trips" if (pv.get('trips') or 1) > 1 else ''))
            legend.append(Line2D([], [], color=c, lw=1.0, marker=marker(i), ms=3.0, mfc=c, mec='white', mew=0.35, label=lab))
        rows.append({'vehicle': name, 'tasks': pv.get('tasks'), 'trips': pv.get('trips'), 'load': pv.get('load'), 'length_m': pv.get('length_m'),
                     'duration_s': pv.get('duration_s'), 'sequence': ' | '.join('→'.join(str(x) for x in trip) for trip in (r.get('trips') or [r.get('sequence') or []]))})
    return rows


def routes(ctx: D.Context, st: Style, opts: dict):
    run = ctx.run
    inst = run['instance']
    fig, axs = figure(st, map_aspect(inst) + 0.22)
    ax = axs[0][0]
    leg: list = []
    draw_field(ax, inst, st, lanes=ctx.lanes if (inst.get('vehicles') or {}).get('travel') == 'rows' else None, tasks=False)
    rows = _route_layer(ax, run, st, legend=leg)
    draw_depot(ax, inst['depot'])
    finish(ax, inst, st, frame=(run.get('scenario_snapshot') or {}).get('frame'), axes=opts.get('axes', True))
    legend_below(fig, ax, leg, st, ncol=2 if st.size == 'single' else 3)
    m = run.get('metrics') or {}
    ok = (run.get('check') or {}).get('valid')
    cap = {
        'zh': f"{alg_name(run, st)} 在任务地图“{(run.get('scenario_snapshot') or {}).get('title', run['scenario_id'])}”上的多车路线："
              f"{m.get('vehicles_used')} 台车服务 {len(inst['tasks'])} 个任务点，总里程 {m.get('total_distance_m', 0):.1f} m，"
              f"最晚完成 {m.get('makespan_s', 0):.0f} s（车速 {inst['vehicles'].get('speed_mps')} m/s）。颜色与标记区分车辆，箭头为行驶方向，方块为车库。"
              f"独立验算{'通过' if ok else '未通过'}{'；车辆沿作物行间通道行驶' if inst['vehicles'].get('travel') == 'rows' else ''}。",
        'en': f"Multi-vehicle routes computed by {alg_name(run, st)}: {m.get('vehicles_used')} vehicles serve {len(inst['tasks'])} tasks, "
              f"total distance {m.get('total_distance_m', 0):.1f} m, makespan {m.get('makespan_s', 0):.0f} s at {inst['vehicles'].get('speed_mps')} m/s. "
              f"Colours and markers distinguish vehicles; arrows give the driving direction; the square is the depot. "
              f"Independent feasibility check: {'passed' if ok else 'failed'}{'; vehicles drive along the inter-row lanes' if inst['vehicles'].get('travel') == 'rows' else ''}.",
    }
    return fig, cap, {'vehicles': rows}


def routes_compare(ctx: D.Context, st: Style, opts: dict):
    runs = [r for r in ctx.runs if r.get('state') == 'done' and r.get('routes')][:4]
    inst = runs[0]['instance']
    n = len(runs)
    ncols = 2 if n == 4 else n
    nrows = 2 if n == 4 else 1
    asp = map_aspect(inst) * nrows / ncols + 0.12
    fig, axs = figure(st, asp, nrows=nrows, ncols=ncols, sharex=True, sharey=True)
    rows = []
    for k, run in enumerate(runs):
        ax = axs[k // ncols][k % ncols]
        draw_field(ax, run['instance'], st, lanes=ctx.lanes if (run['instance'].get('vehicles') or {}).get('travel') == 'rows' else None, tasks=False)
        _route_layer(ax, run, st)
        draw_depot(ax, run['instance']['depot'])
        finish(ax, run['instance'], st, axes=opts.get('axes', True))
        m = run.get('metrics') or {}
        ax.set_title(f"{alg_name(run, st)}\n{m.get('total_distance_m', 0):.0f} m · {m.get('makespan_s', 0):.0f} s" + ('' if (run.get('check') or {}).get('valid') else st.t(' · 未通过验算', ' · check failed')),
                     fontsize=st.fs - 0.5, loc='left', pad=2)
        panel_label(ax, 'abcd'[k], st)
        if k % ncols:
            ax.set_ylabel('')
        if nrows == 2 and k < 2:
            ax.set_xlabel('')
        rows.append({'panel': 'abcd'[k], 'run_id': run['run_id'], 'algorithm': run['algorithm']['id'], 'total_distance_m': m.get('total_distance_m'),
                     'makespan_s': m.get('makespan_s'), 'vehicles_used': m.get('vehicles_used'), 'check_valid': (run.get('check') or {}).get('valid')})
    cap = {
        'zh': '同一任务地图上不同算法的多车路线（同一比例尺）：' + '；'.join(f"（{r['panel']}）{alg_name(x, st)}" for r, x in zip(rows, runs)) +
              '。每幅标题给出总里程与最晚完成时间；颜色区分车辆。',
        'en': 'Routes of different algorithms on the same task map (common scale): ' + '; '.join(f"({r['panel']}) {alg_name(x, st)}" for r, x in zip(rows, runs)) +
              '. Panel titles give total distance and makespan; colours distinguish vehicles.',
    }
    return fig, cap, {'panels': rows}


def _tracks(ax, sim: dict, plan: dict, names: list[str], st: Style, lw: float = 1.1):
    for i, name in enumerate(names):
        c = color(i)
        pv = next((v for v in plan.get('vehicles') or [] if v['name'] == name), None)
        if pv and pv.get('path'):
            P = np.asarray(pv['path'], float)
            ax.plot(P[:, 0], P[:, 1], color=c, lw=0.6, ls=(0, (3, 2)), zorder=22)
        T = np.asarray((sim.get('trails') or {}).get(name) or [], float)
        if len(T) > 1:
            ax.plot(T[:, 1], T[:, 2], color=c, lw=lw, alpha=0.85, zorder=20)
    reached = [t for t in sim.get('tasks') or [] if t.get('state') == 'reached']
    missed = [t for t in sim.get('tasks') or [] if t.get('state') != 'reached']
    if reached:
        R = np.asarray([t.get('stop') or t['xy'] for t in reached], float)
        ax.plot(R[:, 0], R[:, 1], ls='none', marker='o', ms=2.6, mfc='white', mec='#222222', mew=0.5, zorder=30)
    if missed:
        M = np.asarray([t.get('stop') or t['xy'] for t in missed], float)
        ax.plot(M[:, 0], M[:, 1], ls='none', marker='x', ms=4.5, mec='#C0392B', mew=1.0, zorder=31)
    return bool(missed)


def sim_tracks(ctx: D.Context, st: Style, opts: dict):
    """(a) ground-truth tracks over the whole field, (b) close-up where the tracking error is largest."""
    sim = ctx.sim
    plan = (sim.get('execution_plan') or {})
    inst = plan.get('instance') or (ctx.run or {}).get('instance') or (ctx.scenario or {}).get('instance')
    names = [v['name'] for v in plan.get('vehicles') or []] or sorted(sim.get('trails') or {})
    # This map annotates an actual pose; controller measurements have their own
    # timestamps and array length, and cannot be indexed into the pose samples.
    ct = D.cross_track(sim, prefer_control=False)
    worst = max(((n, int(c['e'].argmax()), float(c['e'].max())) for n, c in ct.items() if len(c['e'])), key=lambda w: w[2], default=None)
    fig, axs = figure(st, 0.52, ncols=2, gridspec_kw={'width_ratios': [1.6, 1]})
    ax0, ax1 = axs[0]
    for ax in (ax0, ax1):
        draw_field(ax, inst, st, lanes=ctx.lanes if sim.get('travel') == 'rows' else None, tasks=False)
    missed = _tracks(ax0, sim, plan, names, st)
    _tracks(ax1, sim, plan, names, st, lw=1.6)
    draw_depot(ax0, inst['depot'])
    finish(ax0, inst, st, axes=opts.get('axes', True))
    rows_worst = {}
    if worst:
        n, idx, emax = worst
        T = np.asarray(sim['trails'][n], float)
        cx, cy, yaw = T[idx, 1], T[idx, 2], T[idx, 3]
        h = max(1.5, emax * 14)
        from matplotlib.patches import Rectangle
        from matplotlib.transforms import Affine2D
        ax0.add_patch(Rectangle((cx - h, cy - h), 2 * h, 2 * h, fill=False, ec='#111111', lw=0.7, zorder=40))
        ax0.annotate('(b)', (cx + h, cy + h), xytext=(2, 2), textcoords='offset points', fontsize=st.fs - 1, fontweight='bold')
        L, W = SIM_BODY
        body = Rectangle((-L / 2, -W / 2), L, W, fc='none', ec='#111111', lw=0.6, ls='-', zorder=35)
        body.set_transform(Affine2D().rotate(yaw).translate(cx, cy) + ax1.transData)
        ax1.add_patch(body)
        path = next(v['path'] for v in plan['vehicles'] if v['name'] == n)
        q, d = D.project((cx, cy), path)
        ax1.annotate('', xy=(q[0], q[1]), xytext=(cx, cy), arrowprops={'arrowstyle': '<->', 'lw': 0.7, 'color': '#C0392B', 'shrinkA': 0, 'shrinkB': 0}, zorder=45)
        ax1.annotate(st.t(f'最大横向偏差 {d * 100:.1f} cm', f'max. cross-track error {d * 100:.1f} cm'), ((cx + q[0]) / 2, (cy + q[1]) / 2),
                     xytext=(6, -10), textcoords='offset points', fontsize=st.fs - 1.5, color='#C0392B', zorder=46)
        ax1.set_xlim(cx - h, cx + h)
        ax1.set_ylim(cy - h, cy + h)
        ax1.set_aspect('equal', adjustable='box')
        ax1.set_xlabel(qlabel(st, 'x', 'x', 'm'))
        ax1.set_ylabel(qlabel(st, 'y', 'y', 'm'))
        rows_worst = {'vehicle': n, 't_s': float(T[idx, 0]), 'x': float(cx), 'y': float(cy), 'cross_track_m': d}
    panel_label(ax0, 'a', st)
    panel_label(ax1, 'b', st)
    leg = [Line2D([], [], color=color(i), lw=1.1, label=vehicle_name(i, D.vehicle_label(sim, nm), st)) for i, nm in enumerate(names)]
    leg += [Line2D([], [], color='#444444', lw=0.6, ls=(0, (3, 2)), label=st.t('规划路线', 'Planned path')),
            Line2D([], [], color='#444444', lw=1.1, label=st.t('真值轨迹', 'Ground-truth track')),
            Line2D([], [], ls='none', marker='o', ms=2.6, mfc='white', mec='#222222', mew=0.5, label=st.t('已到达任务', 'Task reached')),
            Patch(fc='none', ec='#111111', lw=0.6, label=st.t('车体 0.8 m × 0.56 m', 'Robot body 0.8 m × 0.56 m'))]
    if missed:
        leg.append(Line2D([], [], ls='none', marker='x', ms=4.5, mec='#C0392B', mew=1.0, label=st.t('未到达任务', 'Task missed')))
    legend_below(fig, ax0, leg, st, ncol=5)
    m = sim.get('metrics') or {}
    cap = {
        'zh': f"Gazebo 田间仿真中各车的真值轨迹（实线）与规划路线（虚线）：（a）全场；（b）跟踪误差最大处的局部放大（方框为仿真车体）。"
              f"{m.get('tasks_reached')}/{m.get('tasks_total')} 个任务点到达；仿真在线统计的横向偏差均值 {m.get('cross_track_mean_m', 0) * 100:.2f} cm、最大 {m.get('cross_track_max_m', 0) * 100:.1f} cm"
              f"（（b）为按真值轨迹记录点复算的最大偏差，与控制周期测量分别计算）。"
              f"执行方式为纯追踪路径跟踪（不调用 Nav2 重规划），位置取自仿真真值（非估计定位）。会话 {sim.get('session_id')}。",
        'en': f"Ground-truth tracks (solid) and planned paths (dashed) in the Gazebo field simulation: (a) whole field; (b) close-up where the tracking error is largest "
              f"(rectangle = simulated robot body). {m.get('tasks_reached')}/{m.get('tasks_total')} tasks reached; online cross-track error mean "
              f"{m.get('cross_track_mean_m', 0) * 100:.2f} cm, max {m.get('cross_track_max_m', 0) * 100:.1f} cm ((b) recomputes deviation at recorded ground-truth poses, separately from control-cycle measurements). Vehicles track the plan with pure pursuit (no Nav2 "
              f"replanning); positions are simulator ground truth, not estimates. Session {sim.get('session_id')}.",
    }
    rows = [{'vehicle': n, 't_s': p[0], 'x': p[1], 'y': p[2], 'yaw': p[3]} for n, tr in (sim.get('trails') or {}).items() for p in tr]
    return fig, cap, {'tracks': rows, 'tasks': sim.get('tasks') or [], 'worst_point': [rows_worst] if rows_worst else []}


def map_extraction(ctx: D.Context, st: Style, opts: dict):
    """(a) the imported map as stored, (b) the planning geometry extracted from it."""
    a, ver, geo = ctx.asset, ctx.asset_version, ctx.geometry
    meta = ver['meta']
    fig, axs = figure(st, 0.5, ncols=2, sharex=True, sharey=True)
    ax0, ax1 = axs[0]
    from .images import asset_image
    img = asset_image(a['id'], ver['v'], meta)
    if img is not None:
        arr, affine = img
        from matplotlib.transforms import Affine2D
        A = np.asarray(affine, float)
        tr = Affine2D(np.array([[A[0, 0], A[0, 1], A[0, 2]], [A[1, 0], A[1, 1], A[1, 2]], [0, 0, 1]])) + ax0.transData
        h, w = arr.shape[:2]
        im = ax0.imshow(arr, extent=(0, w, h, 0), interpolation='nearest' if a['subtype'] == 'ros_grid' else 'antialiased', zorder=1)
        im.set_transform(tr)
    elif a['subtype'] == 'vector':
        from .images import vector_features
        for f in vector_features(a['id'], ver['v'], meta):
            g = f['geometry']
            polys = [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates'] if g['type'] == 'MultiPolygon' else []
            for poly in polys:
                ax0.add_patch(MPoly(poly[0], closed=True, fc='#EEEEEE', ec='#555555', lw=0.4))
            if g['type'] in ('LineString', 'MultiLineString'):
                lines = [g['coordinates']] if g['type'] == 'LineString' else g['coordinates']
                ax0.add_collection(LineCollection(lines, colors='#555555', linewidths=0.5))
    inst = {'boundary': geo['boundary'], 'plots': geo.get('plots') or [], 'obstacles': geo.get('obstacles') or [], 'tasks': geo.get('tasks') or [],
            'depot': geo.get('depot')}
    leg: list = []
    draw_field(ax1, inst, st, tasks=bool(inst['tasks']), depot=inst['depot'] is not None, legend=leg)
    # both panels show the whole imported map (a raster is usually larger than its free region)
    x0, y0, x1, y1 = bounds(inst)
    if meta.get('frame'):
        fx0, fy0, fx1, fy1 = meta['frame']['bounds']
        x0, y0, x1, y1 = min(x0, fx0), min(y0, fy0), max(x1, fx1), max(y1, fy1)
    extent = {'boundary': [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}
    for ax, lab in ((ax0, 'a'), (ax1, 'b')):
        finish(ax, extent, st)
        panel_label(ax, lab, st)
    ax1.set_ylabel('')
    ax0.set_title(st.t('导入的地图', 'Imported map'), fontsize=st.fs - 0.5, loc='left', pad=2)
    ax1.set_title(st.t('提取的规划几何', 'Extracted planning geometry'), fontsize=st.fs - 0.5, loc='left', pad=2)
    legend_below(fig, ax1, leg, st)
    stats = geo.get('stats') or {}
    kind = {'ros_grid': st.t('ROS 占据栅格', 'ROS occupancy grid'), 'vector': 'GeoJSON', 'image': st.t('标定图片', 'calibrated image')}.get(a['subtype'], a['subtype'])
    free = stats.get('free_area_m2')
    free_zh = f'，可通行面积 {free} m²' if free else ''
    free_en = f', free area {free} m²' if free else ''
    cap = {
        'zh': f"地图资产“{a['name']}”（{kind}，v{ver['v']}）到规划几何的转换：（a）原始地图；（b）提取的场地边界、地块与障碍"
              f"（边界 {len(geo['boundary'])} 个顶点，{len(inst['obstacles'])} 处障碍{free_zh}）。" + '；'.join(geo.get('notes') or []),
        'en': f"From the map asset “{a['id']}” ({kind}, v{ver['v']}) to planning geometry: (a) the imported map; (b) the extracted field boundary, plots "
              f"and obstacles ({len(geo['boundary'])} boundary vertices, {len(inst['obstacles'])} obstacles{free_en}).",
    }
    rows = [{'layer': 'boundary', 'vertices': len(geo['boundary'])}] + [{'layer': 'obstacle', 'id': o['id'], 'kind': o.get('kind'), 'vertices': len(o['polygon'])} for o in inst['obstacles']]
    return fig, cap, {'geometry': rows}
