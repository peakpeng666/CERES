"""Research dashboard of the evolving decision loop, rendered from run outputs.

``write()`` reads the JSON results under ``experiments/runs`` and the library ledger and produces
``docs/reports/持续进化研究看板.html`` (standalone) and ``持续进化研究看板.artifact.html`` (body only, for
publishing). Every number on the page is read from those files. Charts are HTML/CSS drawn to scale with
theme tokens (validated categorical and ordinal palettes), each with a data table; a short script adds
hover and keyboard tooltips. Terminology follows ``terms``.
"""
from __future__ import annotations

import html
import json
import math
from datetime import datetime, timezone

from .. import paths
from . import terms as T

RUNS = paths.REPO / 'experiments' / 'runs'
OUT = paths.REPO / 'docs' / 'reports'
LEDGER = paths.LAB / 'models' / 'library-ledger.json'
PROFILES = ('robot_s', 'tractor_m', 'sprayer_l')
SOURCES = {
    'audit': 'evolve-audit-20261023/audit/audit.json', 'fleet': 'evolve-audit-20261023/fleet_try_order.json',
    'verdicts': 'evolve-audit-20261023/verdict_comparison.json', 'policy': 'evolve-audit-20261023/policy/policy.json',
    'policy_ext': 'evolve-policy-ext-20261024/policy_ext.json', 'exec': 'evolve-exec-model-20261024/exec_model.json',
    'dynamic': 'evolve-dynamic-20261024/summary.json', 'gate': 'evolve-replan-gate-20261024/summary.json',
}


def _load(key: str):
    p = RUNS / SOURCES[key]
    return json.loads(p.read_text(encoding='utf-8')) if p.is_file() else None


def _e(x) -> str:
    return html.escape(str(x))


def _num(x: float, d: int = 1, sign: bool = False) -> str:
    s = f'{x:+.{d}f}' if sign else f'{x:.{d}f}'
    if float(s) == 0:
        s = f'{0:.{d}f}'
    return s.replace('-', '−')


def _pct(x: float, d: int = 1, sign: bool = False) -> str:
    return _num(100 * x, d, sign) + '%'


def _ci(lo: float, hi: float, d: int = 1, scale: float = 100) -> str:
    return f'{_num(scale * lo, d)}–{_num(scale * hi, d)}' if lo >= 0 else f'{_num(scale * lo, d)} 至 {_num(scale * hi, d)}'


def _tick(v: float) -> str:
    v = 0.0 if abs(v) < 1e-12 else v
    return _num(v, 0) if abs(v - round(v)) < 1e-9 else _num(v, 1 if abs(v * 10 - round(v * 10)) < 1e-9 else 2)


def ticks(lo: float, hi: float, target: int = 4) -> list[float]:
    """Round tick values spanning [lo, hi]."""
    span = (hi - lo) or abs(hi) or 1.0
    mag = 10 ** math.floor(math.log10(span / target))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10)), key=lambda s: abs(span / s - target))
    start, end = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
    return [start + i * step for i in range(int(round((end - start) / step)) + 1)]


# ------------------------------------------------------------------ chart components (HTML, to scale)

def _scale(tk):
    lo, hi = tk[0], tk[-1]
    return lambda v: 100 * (min(max(v, lo), hi) - lo) / (hi - lo)


def _grid(tk, x) -> str:
    return ''.join(f'<i class="g{" z" if abs(t) < 1e-12 else ""}" style="left:{x(t):.2f}%"></i>' for t in tk)


def _axis(tk, x, title: str) -> str:
    labels = ''.join(f'<i style="left:{x(t):.2f}%">{_tick(t)}</i>' for t in tk)
    return (f'<div class="row axis" aria-hidden="true"><span class="lab"></span><span class="ticks">{labels}</span><span class="val"></span></div>'
            f'<div class="row axis" aria-hidden="true"><span class="lab"></span><span class="axt">{_e(title)}</span><span class="val"></span></div>')


def _whisker(a: float, b: float, tk, x, cls: str = '', top: str = '50%') -> str:
    kind = 'ci' + (' open' if b > tk[-1] + 1e-12 else '') + (' openl' if a < tk[0] - 1e-12 else '')
    return f'<i class="{kind} {cls}" style="left:{x(a):.2f}%;width:{max(x(b) - x(a), 0.3):.2f}%;top:{top}"></i>'


def bars(rows, tk, axis_title: str, lab='10em', val='4.4em') -> str:
    """rows: {label, value, cls, text, tip, lo?, hi?} or {header}. Values in axis units; bars grow from zero."""
    x = _scale(tk)
    grid = _grid(tk, x)
    out = [f'<div class="plot" style="--lab:{lab};--val:{val}">']
    for r in rows:
        if 'header' in r:
            out.append(f'<div class="ph">{_e(r["header"])}</div>')
            continue
        ci = _whisker(r['lo'], r['hi'], tk, x) if r.get('lo') is not None else ''
        out.append(f'<div class="row{" grp" if r.get("grp") else ""}" tabindex="0" data-tip="{_e(r["tip"])}"><span class="lab">{_e(r["label"])}</span>'
                   f'<span class="track">{grid}<i class="bar {r["cls"]}" style="width:{max(x(r["value"]), 0.35):.2f}%"></i>{ci}</span>'
                   f'<span class="val">{_e(r["text"])}</span></div>')
    out.append(_axis(tk, x, axis_title) + '</div>')
    return ''.join(out)


def forest(rows, series, tk, axis_title: str, lab='7em', val='0px') -> str:
    """rows: {label, tip, text?, cells: {series_key: (estimate, lo, hi)}}; series: [(key, css_key)] stacked within a row."""
    x = _scale(tk)
    grid = _grid(tk, x)
    h = 10 + 12 * len(series)
    out = [f'<div class="plot" style="--lab:{lab};--val:{val}">']
    for r in rows:
        marks = []
        for k, (key, css) in enumerate(series):
            if key not in r['cells']:
                continue
            m, a, b = r['cells'][key]
            top = f'{11 + 12 * k}px'
            marks.append(_whisker(a, b, tk, x, f'c{css}', top) + f'<i class="pt k{css}" style="left:{x(m):.2f}%;top:{top}"></i>')
        out.append(f'<div class="row" tabindex="0" data-tip="{_e(r["tip"])}"><span class="lab">{_e(r["label"])}</span>'
                   f'<span class="track" style="height:{h}px">{grid}{"".join(marks)}</span><span class="val">{_e(r.get("text", ""))}</span></div>')
    out.append(_axis(tk, x, axis_title) + '</div>')
    return ''.join(out)


def legend(items) -> str:
    """items: [(name, css_class, dot?)]"""
    return '<div class="legend">' + ''.join(f'<span><i class="sw{" dot" if dot else ""} {cls}"></i>{_e(n)}</span>' for n, cls, dot in items) + '</div>'


def table(head, rows, num_from: int = 1) -> str:
    th = ''.join(f'<th{" class=num" if i >= num_from else ""}>{_e(h)}</th>' for i, h in enumerate(head))
    body = ''.join('<tr>' + ''.join(f'<td{" class=num" if i >= num_from else ""}>{_e(c)}</td>' for i, c in enumerate(r)) + '</tr>' for r in rows)
    return f'<details class="data"><summary>数据表</summary><div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div></details>'


def figure(body: str, caption: str, data: str = '') -> str:
    return f'<figure>{body}<figcaption>{caption}</figcaption>{data}</figure>'


def _cell_label(c: str) -> str:
    rate, k = c.split('@')
    return f'{T.t(T.RATE[rate])} · {k[1:]} 车'


# ------------------------------------------------------------------ sections

def sec_headroom(a, fleet):
    fam = a['families']
    keys = [k for k in ('cvrp_archive', 'fleet:total_distance', 'fleet:makespan', 'coverage') if fam.get(k, {}).get('headroom')]
    rows, data = [], []
    for k in keys:
        h = fam[k]['headroom']
        lo, hi = h['rho_ci95']
        name = T.t(T.FAMILY[k])
        rows.append({'label': name, 'value': 100 * h['rho'], 'lo': 100 * lo, 'hi': 100 * hi, 'cls': 's-accent' if k == 'coverage' else 's-neutral',
                     'text': _pct(h['rho'], 2 if h['rho'] < 0.01 else 1),
                     'tip': f'{name}\nVBS–SBS 差距 {_pct(h["rho"], 3)}（95% CI {_ci(lo, hi, 3)}%）\n实例 {fam[k]["instances"]}，可解 {fam[k]["solvable"]}'})
        data.append([name, fam[k]['instances'], fam[k]['solvable'], _pct(h['rho'], 3), f'{_ci(lo, hi, 3)}%'])
    tk = ticks(0, max(r['hi'] for r in rows))
    cov = fam['coverage']
    d, sbs, h = cov['workbench_default'], cov['sbs'], cov['headroom']
    fallback = (fleet or {}).get('total_distance')
    fb = (f'多车任务的差距来自建图与检验器障碍膨胀容差不一致；按 PyVRP → OR-Tools 顺序回退后 {fallback["with_fallback_feasible"]}/{fallback["solvable_instances"]} 可解。'
          if fallback else '')
    body = figure(bars(rows, tk, 'VBS–SBS 差距 / %', lab='11em'),
                  '<b>图 1</b> 各任务族的算法选择余量：单一最优配置（SBS）相对虚拟最优配置（VBS）的平均惩罚化成本比之差，不可行解计 2；误差线为 95% 自助置信区间。',
                  table(['任务族', '实例', '可解', 'VBS–SBS 差距', '95% CI'], data))
    text = (f'<p>覆盖路径配置的差距为 {_pct(h["rho"])}（95% CI {_ci(*h["rho_ci95"])}%），是唯一具有实质选择余量的任务族。'
            f'工作台默认配置（{T.config_label(d["config"])}）可行 {d["feasible"]}/{cov["instances"]}，SBS（{T.config_label(sbs["config"])}）可行 {sbs["feasible"]}，'
            f'VBS 可行 {cov["solvable"]}。{fb}</p>')
    return body + text


def sec_verdicts(v):
    bp = v['by_profile']
    order = [p for p in PROFILES if p in bp]
    rows, data = [], []
    for p in order:
        name = T.t(T.PROFILE[p])
        for i, key in enumerate(('official', 'fillet', 'strict')):
            n = bp[p][key]
            rows.append({'label': name if i == 0 else '', 'value': n, 'cls': f'o{i + 1}', 'text': f'{n}', 'grp': i == 0 and p != order[0],
                         'tip': f'{name} · {T.t(T.VERDICT[key])}\n{n} 条' + ('' if key == 'official' else f'，占原生可行 {_pct(n / bp[p]["official"])}')})
        data.append([name, bp[p]['official'], bp[p]['fillet'], bp[p]['strict'], _pct(bp[p]['fillet'] / bp[p]['official']), _pct(bp[p]['strict'] / bp[p]['official'])])
    tot = v['total']
    tk = ticks(0, max(bp[p]['official'] for p in order))
    lost = {p: 1 - bp[p]['fillet'] / bp[p]['official'] for p in order}
    worst = sorted(order, key=lambda p: -lost[p])
    tol = sorted(v.get('tolerance_sensitivity_m', {}).items(), key=lambda kv: float(kv[0]))
    same = len({n for _, n in tol}) == 1
    tol_txt = (f'偏差容差取 {"、".join(f"{float(k) * 100:g}" for k, _ in tol)} cm 时保留数' + (f'均为 {tol[0][1]}。' if same else '分别为 ' + '、'.join(str(n) for _, n in tol) + '。')
               if tol else '')
    unv = f'另有 {tot["fillet_unverifiable"]} 条路径重算未能复现，计为不可执行。' if tot.get('fillet_unverifiable') else ''
    body = figure(legend([(T.t(T.VERDICT[k]), f'o{i + 1}', False) for i, k in enumerate(('official', 'fillet', 'strict'))]) +
                  bars(rows, tk, '通过检验的路径数 / 条', lab='8em'),
                  '<b>图 2</b> 原生曲率检验判定可行的覆盖路径在圆角可执行性检验与 2 cm 加密曲率检验下的保留数，按机型分组。',
                  table(['机型', '原生', '圆角', '加密', '圆角保留率', '加密保留率'], data))
    text = (f'<p>圆角可执行性检验以最小转弯半径对拐角圆角化（偏差 ≤ 1 cm），再以原生检验复核：保留 {tot["fillet"]}/{tot["official"]}（{_pct(tot["fillet"] / tot["official"])}），'
            f'加密曲率检验保留 {tot["strict"]}（{_pct(tot["strict"] / tot["official"])}）。被剔除比例以{T.t(T.PROFILE[worst[0]])}最高（{_pct(lost[worst[0]])}），'
            f'{T.t(T.PROFILE[worst[1]])}次之（{_pct(lost[worst[1]])}）。{tol_txt}{unv}</p>')
    return body + text


def sec_policy(p, ext):
    cv = p['cv']['policies']
    learned = [k for k in cv if ':' in k]
    best = max(learned, key=lambda k: cv[k]['first_try_feasible'])
    tiles = ''.join(f'<div class="tile"><div class="tl">{_e(lab)}</div><div class="tv">{_pct(cv[k]["first_try_feasible"])}</div>'
                    f'<div class="ts">{_e(sub)}平均尝试 {_num(cv[k]["tries"], 2)} 次</div></div>'
                    for k, lab, sub in (('default', T.policy_label('default'), ''), ('static', T.policy_label('static'), ''),
                                        (best, '最优实例感知排序', f'{T.policy_label(best)}；')))
    comp = sorted((c for c in p['comparisons'] if c['b'] == 'static' and c['metric'] == 'first'), key=lambda c: -c['mean_diff'])
    rows = [{'label': T.policy_label(c['a']), 'cells': {'a': (100 * c['mean_diff'], 100 * c['bootstrap_ci95'][0], 100 * c['bootstrap_ci95'][1])},
             'text': _num(100 * c['mean_diff'], 1, True),
             'tip': f'{T.policy_label(c["a"])} − 静态尝试顺序\n{_num(100 * c["mean_diff"], 2, True)} 百分点（95% CI {_ci(*c["bootstrap_ci95"], 2)}）\n'
                    f'胜/负/平 {"/".join(map(str, c["wins_losses_ties"]))} 块田，单侧 Wilcoxon p = {c["wilcoxon_p_one_sided"]:.3f}'} for c in comp]
    lo = min(r['cells']['a'][1] for r in rows)
    hi = max(r['cells']['a'][2] for r in rows)
    tk = ticks(min(lo, 0), max(hi, 0))
    data = [[T.policy_label(c['a']), _num(100 * c['mean_diff'], 2, True), _ci(*c['bootstrap_ci95'], 2), '/'.join(map(str, c['wins_losses_ties'])),
             f'{c["wilcoxon_p_one_sided"]:.3f}'] for c in comp]
    body = (f'<div class="tiles">{tiles}</div>' +
            figure(forest(rows, [('a', 'a')], tk, '首次尝试可行率差（实例感知 − 静态）/ 百分点', lab='11em', val='3.4em'),
                   f'<b>图 3</b> 实例感知排序相对静态尝试顺序的首次尝试可行率差，逐田块配对（{p["fields"]} 块田），误差线为田块自助 95% 置信区间。',
                   table(['策略', '差值 / 百分点', '95% CI', '胜/负/平', '单侧 p'], data)))
    n_better = sum(1 for c in comp if c['bootstrap_ci95'][0] > 0)
    text = (f'<p>田块分组 {p["cv"]["folds"]} 折 × {p["cv"]["repeats"]} 次重复交叉验证。静态尝试顺序把首次尝试可行率由 {_pct(cv["default"]["first_try_feasible"])} '
            f'提高到 {_pct(cv["static"]["first_try_feasible"])}，已部署于工作台推荐器；六种实例感知排序中{"无一" if n_better == 0 else f"有 {n_better} 种"}显著优于静态顺序。</p>')
    if ext:
        lifts = [(v, ext[f'old:{v}']['cv']['static']['first_try_feasible'], ext[f'extended:{v}']['cv']['static']['first_try_feasible'])
                 for v in ('official', 'fillet', 'strict') if f'old:{v}' in ext and f'extended:{v}' in ext]
        promoted = sum(1 for r in ext.values() for d in r['decisions'].values() if d['promote'])
        text += (f'<p>加入 6 个局部 Dubins 修复配置后，静态顺序的首次尝试可行率在' +
                 '，'.join(f'{T.t(T.VERDICT[v])}下由 {_pct(a)} 提高到 {_pct(b)}' for v, a, b in lifts) +
                 f'；{len(ext)} 个「算法库 × 检验」组合中{"没有" if promoted == 0 else f"有 {promoted} 个"}实例感知排序满足晋升准则。'
                 '算法库演化带来的提升大于排序学习。</p>')
        text += sec_policy_ext(ext)
    return body + text


def sec_policy_ext(ext):
    body = []
    for ver in ('official', 'fillet', 'strict'):
        keys = [k for k in (f'old:{ver}', f'extended:{ver}') if k in ext]
        if not keys:
            continue
        body.append(f'<tr class="grp"><th colspan="6">{_e(T.t(T.VERDICT[ver]))}</th></tr>')
        for key in keys:
            r = ext[key]
            name, d = max(r['decisions'].items(), key=lambda kv: kv[1]['first_try_diff_mean'])
            chip = '<span class="chip chip-good">晋升</span>' if d['promote'] else '<span class="chip chip-line">不晋升</span>'
            body.append(f'<tr><td>{"原算法库" if key.startswith("old") else "扩展算法库"}</td><td class="num">{_pct(r["cv"]["static"]["first_try_feasible"])}</td>'
                        f'<td>{_e(T.policy_label(name))}</td>'
                        f'<td class="num">{_num(100 * d["first_try_diff_mean"], 1, True)} [{_num(100 * d["first_try_ci95"][0], 1)}, {_num(100 * d["first_try_ci95"][1], 1)}]</td>'
                        f'<td class="num">{_num(100 * d["stop_rho_diff_mean"], 1, True)} [{_num(100 * d["stop_rho_ci95"][0], 1)}, {_num(100 * d["stop_rho_ci95"][1], 1)}]</td>'
                        f'<td>{chip}</td></tr>')
    head = ('<tr><th>算法库</th><th class="num">静态顺序</th><th>最优实例感知排序</th><th class="num">Δ首次可行率 / 百分点</th>'
            '<th class="num">Δ终止成本比 / %</th><th>判定</th></tr>')
    return (f'<figure><div class="tablewrap"><table class="ext"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'
            '<figcaption><b>表 1</b> 原算法库与扩展算法库（增加 6 个局部 Dubins 修复配置）上的尝试顺序评估：静态顺序的首次尝试可行率，'
            '以及首次尝试可行率差最大的实例感知排序相对静态顺序的差值与 95% 置信区间。晋升准则：首次尝试可行率差的 95% 置信下界 &gt; 0 且 Holm 校正单侧 p &lt; 0.05，'
            '终止成本比差的 95% 置信上界 ≤ +1%。</figcaption></figure>')


STATUS = (('工作台现行默认', '默认配置', 'chip-line'), ('已上线', '已部署', 'chip-good'), ('研究候选：证据支持', '候选 · 支持纳入', 'chip-warm'),
          ('研究候选', '候选', 'chip-line'), ('注册配置', '注册配置', 'chip-line'))


def _status(s: str) -> tuple[str, str]:
    for prefix, label, cls in STATUS:
        if s.startswith(prefix):
            if prefix == '已上线' and '第' in s:
                label += ' · 第 ' + s.split('第')[1].split('位')[0].strip() + ' 位'
            return label, cls
    return s, 'chip-line'


def sec_ledger(led, table_no: int):
    members = led['members']
    off = led['verdicts'].get('official', {})
    phi_max = max((m['evidence'].get('official') or {}).get('shapley', 0) for m in members) or 1
    rows = []
    for m in members:
        ev, evf = m['evidence'].get('official') or {}, m['evidence'].get('fillet') or {}
        label, cls = _status(m['status'])
        origin = 'Fields2Cover' if not m['parent'] else T.t(T.OPERATION.get(m['id'].split(':')[0], (m['operation'], m['operation'])))
        phi = ev.get('shapley')
        spark = (f'<span class="spark"><i style="width:{100 * max(phi, 0) / phi_max:.1f}%"></i></span>{_num(phi, 3)}' if phi is not None else '—')
        rows.append(f'<tr><td>{_e(T.config_label(m["id"]))}</td><td class="muted">{_e(origin)}</td><td><span class="chip {cls}">{_e(label)}</span></td>'
                    f'<td class="num">{ev.get("feasible", "—")}</td><td class="num">{evf.get("feasible", "—")}</td><td class="num">{spark}</td></tr>')
    reg = [m for m in members if not m['parent'] and m['evidence'].get('official')]
    derived = [m for m in members if m['parent'] and m['evidence'].get('official')]
    head = '<tr><th>成员</th><th>来源</th><th>状态</th><th class="num">原生可行</th><th class="num">圆角可行</th><th class="num">Shapley 贡献</th></tr>'
    caption = (f'<b>表 {table_no}</b> 覆盖算法库的成员、来源与证据。可行数为 {off.get("instances", "—")} 个实例中通过检验的个数；Shapley 贡献为成员对组合价值 '
               f'v(S) = E[2 − min ρ] 的平均边际贡献（原生曲率检验，{len(members)} 个成员，{"精确计算" if off.get("shapley_method") == "exact" else "排列抽样估计"}）；— 表示尚未完成该检验。')
    text = ''
    if reg and derived:
        best_reg = max(reg, key=lambda m: m['evidence']['official']['feasible'])
        best_der = max(derived, key=lambda m: m['evidence']['official']['feasible'])
        top = max(reg + derived, key=lambda m: m['evidence']['official']['shapley'])
        text = (f'<p>局部 Dubins 修复把单一配置的可行数由 {best_reg["evidence"]["official"]["feasible"]}（{T.config_label(best_reg["id"])}）提高到 '
                f'{best_der["evidence"]["official"]["feasible"]}（{T.config_label(best_der["id"])}）；Shapley 贡献最大的成员为{T.config_label(top["id"])}'
                f'（{_num(top["evidence"]["official"]["shapley"], 3)}）。修复配置纳入工作台注册适配器后即可进入部署顺序。</p>')
    return (f'<figure><div class="tablewrap"><table><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<figcaption>{caption}</figcaption></figure>' + text)


def sec_exec(m):
    r = m['results']
    groups = (('all', f'全部 {m["plans"]} 个方案（{r["all"]["constant_speed"]["n"]} 次车辆运行）', ('constant_speed', 'constant_scaled', 'linear')),
              ('with_reference', f'具解析参考的方案（{r["with_reference"]["constant_speed"]["n"]} 次车辆运行）', ('constant_speed', 'constant_scaled', 'reference', 'linear')))
    rows, data, means = [], [], []
    for g, header, keys in groups:
        rows.append({'header': header})
        for k in keys:
            e = r[g].get(k)
            if not e:
                continue
            lo, hi = e['mape_ci95_plan_bootstrap']
            name = T.t(T.TIME_MODEL[k])
            means.append(100 * e['mape'])
            rows.append({'label': name, 'value': 100 * e['mape'], 'lo': 100 * lo, 'hi': 100 * hi, 'cls': 's-accent' if k == 'reference' else 's-neutral',
                         'text': _pct(e['mape']), 'tip': f'{name}\nMAPE {_pct(e["mape"])}（95% CI {_ci(lo, hi)}%），中位绝对百分比误差 {_pct(e["median_ape"])}'})
            data.append([header.split('（')[0], name, e['n'], _pct(e['mape']), f'{_ci(lo, hi)}%', _pct(e['median_ape'])])
    tk = ticks(0, max(means) * 1.08)
    w, a = r['with_reference'], r['all']
    body = figure(bars(rows, tk, '平均绝对百分比误差（MAPE）/ %', lab='10.5em'),
                  '<b>图 4</b> 留一方案交叉验证的车辆驾驶时间预测误差，误差线为方案自助 95% 置信区间，箭头表示区间超出坐标范围。',
                  table(['方案集', '模型', '车辆运行', 'MAPE', '95% CI', '中位误差'], data, num_from=2))
    text = (f'<p>停—转—行解析模型无需拟合，MAPE 为 {_pct(w["reference"]["mape"])}，恒速模型为 {_pct(w["constant_speed"]["mape"])}。'
            f'线性残差校正在全部 {m["plans"]} 个方案上 MAPE 为 {_pct(a["linear"]["mape"])}，方案较少时外推不稳定'
            f'（{_pct(w["linear"]["mape"])}，95% CI {_ci(*w["linear"]["mape_ci95_plan_bootstrap"])}%）。执行反馈以解析模型为先验、以残差学习作校正。</p>')
    return body + text


def sec_dynamic(s, gate):
    cells = [c for c in (f'{r}@k{k}' for r in T.RATE for k in (2, 4)) if c in s['cells']]
    gc = (gate or {}).get('cells', {})
    series = [('reopt', '1'), ('rollout', '2')] + ([('gate', '3')] if gc else [])

    def est(c, key, metric):
        src = gc.get(c, {}).get(f'gate_vs_insert_{metric}') if key == 'gate' else s['cells'][c].get(f'{key}_vs_insert_{metric}')
        return src

    panels, data = [], []
    for metric, title in (('mean_response_s', '(a) 平均响应时间'), ('distance_m', '(b) 总行驶里程')):
        rows = []
        for c in cells:
            vals, tip = {}, [_cell_label(c)]
            for key, _css in series:
                src = est(c, key, metric)
                if src:
                    vals[key] = (100 * src['mean_rel'], 100 * src['ci95'][0], 100 * src['ci95'][1])
                    tip.append(f'{T.t(T.STRATEGY[key])} {_pct(src["mean_rel"], 1, True)}（95% CI {_ci(*src["ci95"])}%）')
            rows.append({'label': _cell_label(c), 'cells': vals, 'tip': '\n'.join(tip)})
        lo = min(v[1] for r in rows for v in r['cells'].values())
        hi = max(v[2] for r in rows for v in r['cells'].values())
        panels.append(f'<div class="panel-mini"><div class="pt-title">{title}</div>{forest(rows, series, ticks(min(lo, 0), max(hi, 0)), "相对增量插入的变化 / %", lab="6.5em")}</div>')
    for c in cells:
        for key, _css in series:
            r_, d_ = est(c, key, 'mean_response_s'), est(c, key, 'distance_m')
            if r_ and d_:
                data.append([_cell_label(c), T.t(T.STRATEGY[key]), _pct(r_['mean_rel'], 1, True), f'{_ci(*r_["ci95"])}%', _pct(d_['mean_rel'], 1, True), f'{_ci(*d_["ci95"])}%'])
    n = s['cells'][cells[0]]['n']
    body = figure(legend([(T.t(T.STRATEGY[key]), f'k{css}', True) for key, css in series]) + f'<div class="duo">{"".join(panels)}</div>',
                  f'<b>图 5</b> 动态任务流中各重规划策略相对增量插入的逐流相对变化（均值与 95% 自助置信区间），每格 {n} 条任务流，规划层离散事件仿真。',
                  table(['到达密度 · 车队', '策略', 'Δ响应时间', '95% CI', 'Δ里程', '95% CI'], data, num_from=2))
    share_rows = [{'label': _cell_label(c), 'value': 100 * s['cells'][c]['rollout_reopt_share'], 'cls': 'k2', 'text': _pct(s['cells'][c]['rollout_reopt_share']),
                   'tip': f'{_cell_label(c)}\n前瞻采样采用重优化的决策比例 {_pct(s["cells"][c]["rollout_reopt_share"])}'} for c in cells]
    minis = [f'<div class="panel-mini"><div class="pt-title">(a) 前瞻采样采用重优化的决策比例</div>'
             f'{bars(share_rows, ticks(0, max(r["value"] for r in share_rows)), "决策比例 / %", lab="6.5em", val="3.6em")}</div>']
    if gc:
        saved_rows = [{'label': _cell_label(c), 'value': 100 * gc[c]['solver_calls']['saved_share'], 'cls': 'k3', 'text': _pct(gc[c]['solver_calls']['saved_share'], 0),
                       'tip': f'{_cell_label(c)}\n每条任务流求解器调用 {_num(gc[c]["solver_calls"]["gate"], 1)} 次（逐事件重优化 {_num(gc[c]["solver_calls"]["always_reopt"], 0)} 次）'}
                      for c in cells if c in gc]
        minis.append(f'<div class="panel-mini"><div class="pt-title">(b) 学习型触发减少的求解器调用</div>'
                     f'{bars(saved_rows, ticks(0, 100), "相对逐事件重优化 / %", lab="6.5em", val="3.6em")}</div>')
    body += figure(f'<div class="duo">{"".join(minis)}</div>', '<b>图 6</b> 重优化的调用频率' + ('：(a) 前瞻采样；(b) 学习型重规划触发器（留出田块）。' if gc else '：前瞻采样。'))
    resp = {c: s['cells'][c]['reopt_vs_insert_mean_response_s']['mean_rel'] for c in cells}
    sparse = [resp[c] for c in cells if c.startswith('sparse')]
    dense = [resp[c] for c in cells if c.startswith('dense')]
    shares = [s['cells'][c]['rollout_reopt_share'] for c in cells]
    text = (f'<p>逐事件重优化的收益随到达密度增加：平均响应时间的改善由稀疏时的 {_pct(-max(sparse))}–{_pct(-min(sparse))} 增至密集时的 '
            f'{_pct(-max(dense))}–{_pct(-min(dense))}，总里程同时下降。前瞻采样仅在 {_pct(min(shares))}–{_pct(max(shares))} 的决策点采用重优化，'
            '响应改善与逐事件重优化相当，重规划的价值集中于少数决策。')
    if gc:
        saved = [gc[c]['solver_calls']['saved_share'] for c in cells if c in gc]
        vs_roll = [gc[c]['gate_vs_rollout_mean_response_s']['ci95'] for c in cells if c in gc]
        ns = sum(1 for lo, hi in vs_roll if lo <= 0 <= hi)
        vr = [gc[c]['gate_vs_reopt_mean_response_s']['mean_rel'] for c in cells if c in gc]
        vd = [gc[c]['gate_vs_reopt_distance_m']['mean_rel'] for c in cells if c in gc]
        text += (f'以前瞻采样的选择为标签、按田块分组 {gate["folds"]} 折训练的学习型重规划触发器（逻辑回归，阈值使训练田块上重优化召回率 ≥ {gate["recall_target"]:.0%}）'
                 f'在留出田块上减少求解器调用 {_pct(min(saved), 0)}–{_pct(max(saved), 0)}；平均响应时间与前瞻采样之差在 {ns}/{len(vs_roll)} 个场景中不显著，'
                 f'相对逐事件重优化的响应时间变化为 {_pct(min(vr), 1, True)} 至 {_pct(max(vr), 1, True)}，总里程变化为 {_pct(min(vd), 1, True)} 至 {_pct(max(vd), 1, True)}。')
    return body + text + '</p>'


OPEN = [('主要终点', '以圆角可执行性检验为覆盖路径可行性的主要终点，原生与加密曲率检验作敏感性分析。'),
        ('外部验证', '预注册修订（H1c–H3c）冻结后，在爱沙尼亚与立陶宛田块上检验选择余量、尝试顺序与修复增益。'),
        ('默认配置', '以无分解配置或集成局部 Dubins 修复的配置替换牛耕式分解默认。'),
        ('多车建图', '统一建图与可行性检验的障碍膨胀参数。'),
        ('时间模型', '以停—转—行模型替换调度中的恒速假设，并在执行数据积累后学习残差。')]

CSS = """
/* One reading column: header, findings, six research panels, open questions. Charts are HTML at column width. */
:root{
  --bg:#f3f5f1; --panel:#fcfdfb; --ink:#142019; --ink-2:#3b4a41; --muted:#5f6d64; --faint:#7d8981; --rule:#e0e6e1; --base:#aab5ad;
  --hover:rgba(20,32,25,.045); --accent:#3a8a5f; --accent-ink:#2b6a48; --accent-soft:#dcece2; --neutral:#a4afa8;
  --ord-1:#7fbf98; --ord-2:#3a8a5f; --ord-3:#1b5a3b; --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a;
  --warm-soft:#f6e7d6; --warm-ink:#8a4b12;
  --display:"Noto Serif SC","Source Han Serif SC","Songti SC",serif;
  --sans:"Noto Sans SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0f1612; --panel:#17201b; --ink:#e3ebe5; --ink-2:#c0cdc4; --muted:#9aab9f; --faint:#7c8a81; --rule:#26332b; --base:#46544b;
  --hover:rgba(227,235,229,.05); --accent:#4f9c72; --accent-ink:#8fd0ab; --accent-soft:#1d3527; --neutral:#4d5a52;
  --ord-1:#2f6f4c; --ord-2:#4f9c72; --ord-3:#8fd0ab; --s1:#3987e5; --s2:#d95926; --s3:#199e70;
  --warm-soft:#3a2a17; --warm-ink:#e8b27a; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0f1612; --panel:#17201b; --ink:#e3ebe5; --ink-2:#c0cdc4; --muted:#9aab9f; --faint:#7c8a81; --rule:#26332b; --base:#46544b;
  --hover:rgba(227,235,229,.05); --accent:#4f9c72; --accent-ink:#8fd0ab; --accent-soft:#1d3527; --neutral:#4d5a52;
  --ord-1:#2f6f4c; --ord-2:#4f9c72; --ord-3:#8fd0ab; --s1:#3987e5; --s2:#d95926; --s3:#199e70;
  --warm-soft:#3a2a17; --warm-ink:#e8b27a; color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 var(--sans)}
[hidden]{display:none!important}
.wrap{max-width:880px;margin:0 auto;padding-block:32px 64px;padding-left:max(16px, env(safe-area-inset-left, 0px));padding-right:max(16px, env(safe-area-inset-right, 0px));display:grid;gap:20px}
header{display:grid;gap:8px}
.eyebrow,.loop,.card .k{font:500 .72rem/1.4 var(--mono);letter-spacing:.07em}
.eyebrow{color:var(--accent-ink)}
h1{font:700 2rem/1.25 var(--display);margin:0;text-wrap:balance}
.dek{color:var(--muted);margin:0;max-width:62ch}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:10px;padding:14px 16px;display:grid;gap:6px;align-content:start;color:inherit;text-decoration:none}
.card:hover,.card:focus-visible{border-color:var(--base)}
.card .k{color:var(--accent-ink)}
.card p{margin:0;font-size:.92rem}
section{background:var(--panel);border:1px solid var(--rule);border-radius:12px;padding:20px 22px;display:grid;gap:14px;min-width:0;scroll-margin-top:16px}
.sh{display:grid;gap:2px}
.loop{color:var(--muted)}
h2{font:600 1.25rem/1.35 var(--display);margin:0;text-wrap:balance}
p{margin:0;max-width:68ch}
figure{margin:0;display:grid;gap:8px;min-width:0}
figcaption{font-size:.84rem;color:var(--muted);max-width:74ch;line-height:1.6}
figcaption b{color:var(--ink-2);font-weight:600;margin-right:.3em}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:.82rem;color:var(--ink-2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:11px;height:11px;border-radius:2px;display:inline-block}
.sw.dot{border-radius:50%}
.plot{display:grid;gap:1px;min-width:0}
.row{display:grid;grid-template-columns:var(--lab) minmax(0,1fr) var(--val);align-items:center;column-gap:10px;border-radius:6px;outline:none}
.row:not(.axis):hover{background:var(--hover)}
.row:not(.axis):focus-visible{background:var(--hover);box-shadow:0 0 0 2px var(--accent)}
.lab{font-size:.84rem;color:var(--ink-2);text-align:right;line-height:1.3}
.val{font:.8rem var(--mono);font-variant-numeric:tabular-nums;color:var(--ink-2);white-space:nowrap}
.track{position:relative;height:24px}
.g{position:absolute;top:0;bottom:0;width:1px;background:var(--rule)}
.g.z{background:var(--base)}
.bar{position:absolute;left:0;top:50%;height:14px;transform:translateY(-50%);border-radius:0 4px 4px 0}
.ci{position:absolute;height:0;border-top:1.5px solid currentColor;color:var(--ink-2);transform:translateY(-.75px)}
.ci::before,.ci::after{content:"";position:absolute;top:-4.5px;height:8px;border-left:1.5px solid currentColor}
.ci::before{left:-.75px}.ci::after{right:-.75px}
.ci.open::after{right:-7px;top:-4.75px;height:0;border-left:7px solid currentColor;border-top:4px solid transparent;border-bottom:4px solid transparent}
.ci.openl::before{left:-7px;top:-4.75px;height:0;border-left:0;border-right:7px solid currentColor;border-top:4px solid transparent;border-bottom:4px solid transparent}
.pt{position:absolute;width:9px;height:9px;border-radius:50%;transform:translate(-50%,-50%);box-shadow:0 0 0 2px var(--panel)}
.ph{font-size:.8rem;font-weight:600;color:var(--ink-2);padding:10px 0 2px}
.ticks{position:relative;height:1.4em}
.ticks i{position:absolute;transform:translateX(-50%);font:normal .72rem var(--mono);font-variant-numeric:tabular-nums;color:var(--faint);white-space:nowrap}
.axt{font-size:.76rem;color:var(--muted);text-align:center}
.s-accent{background:var(--accent)} .s-neutral{background:var(--neutral)}
.o1{background:var(--ord-1)} .o2{background:var(--ord-2)} .o3{background:var(--ord-3)}
.k1{background:var(--s1)} .k2{background:var(--s2)} .k3{background:var(--s3)} .ka{background:var(--accent)}
.ci.c1{color:var(--s1)} .ci.c2{color:var(--s2)} .ci.c3{color:var(--s3)} .ci.ca{color:var(--accent)}
.duo{display:grid;grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));gap:18px}
.panel-mini{display:grid;gap:4px;min-width:0;align-content:start}
.pt-title{font-size:.82rem;font-weight:600;color:var(--ink-2)}
details.data summary{cursor:pointer;font-size:.8rem;color:var(--muted);width:max-content}
details.data[open] summary{margin-bottom:6px}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{padding:6px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
th{font-weight:600;font-size:.78rem;color:var(--muted)}
.num{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
th.num{font-family:inherit}
.row.grp{margin-top:10px}
td.muted{color:var(--muted)}
tr.grp th{padding-top:12px;color:var(--ink-2);font-size:.8rem;border-bottom:1px solid var(--base)}
table.ext td{white-space:nowrap}
.chip{display:inline-block;font-size:.74rem;padding:1px 8px;border-radius:999px;white-space:nowrap}
.chip-good{background:var(--accent-soft);color:var(--accent-ink)}
.chip-warm{background:var(--warm-soft);color:var(--warm-ink)}
.chip-line{border:1px solid var(--rule);color:var(--muted)}
.spark{display:inline-block;width:56px;height:8px;background:var(--rule);border-radius:0 2px 2px 0;vertical-align:middle;margin-right:8px;position:relative}
.spark i{position:absolute;left:0;top:0;bottom:0;background:var(--accent);border-radius:0 2px 2px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(7.5rem,1fr));gap:10px}
.tile{border:1px solid var(--rule);border-radius:10px;padding:10px 14px;display:grid;gap:2px}
.tl{font-size:.8rem;color:var(--muted)} .tv{font:600 1.6rem/1.25 var(--sans)} .ts{font-size:.75rem;color:var(--muted)}
.open{margin:0;padding:0;list-style:none;display:grid;gap:8px}
.open li{display:grid;grid-template-columns:6.5em 1fr;gap:12px}
.open b{font-weight:600}
footer{display:grid;gap:8px;font-size:.78rem;color:var(--muted)}
footer dl{display:grid;grid-template-columns:7em 1fr;gap:2px 12px;margin:0}
footer dt{color:var(--ink-2)} footer dd{margin:0;font-family:var(--mono);overflow-wrap:anywhere}
.tip{position:fixed;z-index:20;max-width:20rem;padding:6px 10px;border-radius:6px;background:var(--ink);color:var(--panel);font-size:.78rem;line-height:1.55;white-space:pre-line;pointer-events:none;box-shadow:0 6px 18px rgba(0,0,0,.18)}
@media (max-width:560px){
  h1{font-size:1.65rem}
  section{padding:16px}
  .row{grid-template-columns:minmax(0,1fr) var(--val);grid-template-areas:"lab lab" "track val";row-gap:0}
  .row .lab{grid-area:lab;text-align:left;padding-top:4px}
  .row .lab:empty{display:none}
  .row .val{grid-area:val}
  .row .track{grid-area:track}
  .row.axis{grid-template-areas:"track val"}
  .row.axis .lab{display:none}
  .row.axis .ticks,.row.axis .axt{grid-area:track}
  .open li,footer dl{grid-template-columns:1fr}
  .tile{padding:8px 10px} .tv{font-size:1.3rem}
}
"""

SCRIPT = """<script>
(() => {
  const tip = document.createElement('div');
  tip.className = 'tip'; tip.hidden = true; tip.setAttribute('role', 'tooltip');
  document.body.appendChild(tip);
  const show = (el) => {
    tip.textContent = el.dataset.tip; tip.hidden = false;
    const r = (el.querySelector('.track') || el).getBoundingClientRect();
    const w = tip.offsetWidth, h = tip.offsetHeight;
    const x = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8);
    const y = r.top - h - 8 < 8 ? r.bottom + 8 : r.top - h - 8;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  };
  const hide = () => { tip.hidden = true; };
  document.querySelectorAll('[data-tip]').forEach((el) => {
    el.addEventListener('pointerenter', () => show(el));
    el.addEventListener('pointerleave', hide);
    el.addEventListener('focus', () => show(el));
    el.addEventListener('blur', hide);
  });
  addEventListener('scroll', hide, { passive: true });
})();
</script>"""


def page() -> str:
    a, fleet, v, p = _load('audit'), _load('fleet'), _load('verdicts'), _load('policy')
    ext, ex, dyn, gate = _load('policy_ext'), _load('exec'), _load('dynamic'), _load('gate')
    led = json.loads(LEDGER.read_text(encoding='utf-8')) if LEDGER.is_file() else None
    sections = []
    if a:
        sections.append(('s1', '决策学习', '算法选择余量', sec_headroom(a, fleet)))
    if v:
        sections.append(('s2', '评价方法', '可执行性检验', sec_verdicts(v)))
    if p:
        sections.append(('s3', '决策学习', '尝试顺序策略', sec_policy(p, ext)))
    if led:
        sections.append(('s4', '算法库演化', '算法库演化谱系', sec_ledger(led, 2 if ext else 1)))
    if ex:
        sections.append(('s5', '执行反馈', '执行时间模型', sec_exec(ex)))
    if dyn:
        sections.append(('s6', '动态调度', '重规划时机', sec_dynamic(dyn, gate)))

    cards = []
    if a and p:
        cov, cv = a['families']['coverage'], p['cv']['policies']
        others = max(a['families'][k]['headroom']['rho'] for k in a['families'] if k != 'coverage' and a['families'][k].get('headroom'))
        cards.append(('s1', '决策学习', f'选择余量集中于覆盖路径配置（VBS–SBS 差距 {_pct(cov["headroom"]["rho"])}，其余任务族 ≤ {_pct(others)}）；'
                                         f'静态尝试顺序把首次尝试可行率由 {_pct(cv["default"]["first_try_feasible"])} 提高到 {_pct(cv["static"]["first_try_feasible"])}。'))
    if v:
        t = v['total']
        cards.append(('s2', '评价方法', f'原生曲率检验判定可行的路径中，{_pct(1 - t["fillet"] / t["official"])} 无法按最小转弯半径圆角化，可执行性被系统性高估。'))
    if led:
        der = [m for m in led['members'] if m['parent'] and m['evidence'].get('official')]
        reg = [m for m in led['members'] if not m['parent'] and m['evidence'].get('official')]
        if der and reg:
            bd = max(m['evidence']['official']['feasible'] for m in der)
            br = max(m['evidence']['official']['feasible'] for m in reg)
            top = max(der + reg, key=lambda m: m['evidence']['official']['shapley'])
            txt = f'局部 Dubins 修复把单一配置的可行实例由 {br} 个提高到 {bd} 个'
            if ext and 'old:fillet' in ext and 'extended:fillet' in ext:
                a_, b_ = (ext[k]['cv']['static']['first_try_feasible'] for k in ('old:fillet', 'extended:fillet'))
                txt += f'；纳入修复配置后，静态顺序的首次尝试可行率（圆角检验）由 {_pct(a_)} 提高到 {_pct(b_)}，大于任何排序学习的收益。'
            else:
                txt += '，修复配置的 Shapley 贡献居各成员之首。' if top['parent'] else '。'
            cards.append(('s4', '算法库演化', txt))
    if ex and dyn:
        w = ex['results']['with_reference']
        shares = [c['rollout_reopt_share'] for c in dyn['cells'].values()]
        txt = (f'停—转—行解析模型的驾驶时间 MAPE 为 {_pct(w["reference"]["mape"])}（恒速模型 {_pct(w["constant_speed"]["mape"])}）；'
               f'前瞻采样仅在 {_pct(min(shares))}–{_pct(max(shares))} 的决策点重优化即达到逐事件重优化的响应改善')
        if gate:
            saved = [c['solver_calls']['saved_share'] for c in gate['cells'].values()]
            txt += f'，学习型重规划触发器在留出田块上减少求解器调用 {_pct(min(saved), 0)}–{_pct(max(saved), 0)}'
        cards.append(('s5', '执行反馈与动态调度', txt + '。'))
    card_html = ''.join(f'<a class="card" href="#{sid}"><span class="k">{_e(k)}</span><p>{_e(txt)}</p></a>' for sid, k, txt in cards)
    sec_html = ''.join(f'<section id="{sid}"><div class="sh"><span class="loop">{_e(loop)}</span><h2>{_e(title)}</h2></div>{body}</section>'
                       for sid, loop, title, body in sections)
    open_html = ''.join(f'<li><b>{_e(k)}</b><span>{_e(txt)}</span></li>' for k, txt in OPEN)
    src_rows = [('选择余量', [SOURCES['audit'], SOURCES['fleet']]), ('可执行性检验', [SOURCES['verdicts']]),
                ('尝试顺序', [SOURCES['policy']] + ([SOURCES['policy_ext']] if ext else [])), ('演化谱系', ['src/lab/models/library-ledger.json']),
                ('执行时间', [SOURCES['exec']]), ('动态调度', [SOURCES['dynamic']] + ([SOURCES['gate']] if gate else []))]
    src_html = ''.join(f'<dt>{_e(k)}</dt><dd>{"<br>".join(_e(s if s.startswith("src/") else "experiments/runs/" + s) for s in srcs)}</dd>' for k, srcs in src_rows)
    fields = p['fields'] if p else '—'
    inst = a['families']['coverage']['instances'] if a else '—'
    n_prof = len(v['by_profile']) if v else len(PROFILES)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return f"""<title>持续进化研究看板</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+SC:wght@400;600&family=Noto+Serif+SC:wght@600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header><div class="eyebrow">农业机器人路径规划 · 持续进化决策与调度</div><h1>持续进化研究看板</h1>
<p class="dek">开发集：荷兰 {fields} 块田块 × {n_prof} 类机型，共 {inst} 个覆盖实例；爱沙尼亚与立陶宛田块留作外部验证。</p></header>
<div class="cards">{card_html}</div>
{sec_html}
<section id="open"><div class="sh"><span class="loop">后续工作</span><h2>开放问题</h2></div><ul class="open">{open_html}</ul></section>
<footer><dl>{src_html}<dt>生成器</dt><dd>src/lab/ceres_lab/evolve/dashboard.py</dd><dt>更新</dt><dd>{now}</dd></dl></footer>
</div>
{SCRIPT}
"""


def write() -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    body = page()
    frag = OUT / '持续进化研究看板.artifact.html'
    full = OUT / '持续进化研究看板.html'
    frag.write_text(body, encoding='utf-8')
    head, _, rest = body.partition('<div class="wrap">')
    full.write_text('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                    + head + '</head><body><div class="wrap">' + rest + '</body></html>\n', encoding='utf-8')
    return [str(frag), str(full)]
