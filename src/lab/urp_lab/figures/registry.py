"""Figure catalogue: what each research figure needs, rendering in publication formats, and the paper bundle
(vector + raster figures, source-data CSVs, bilingual captions and a provenance manifest)."""
from __future__ import annotations

import csv
import copy
import io
import json
import threading
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import matplotlib
import numpy as np
import scipy

from ..analysis import cd_unavailable_reason
from . import charts, maps, study
from . import data as D
from .style import PRESETS, Style, apply, fit_height

GROUPS = [('map', '场景与地图', 'Scenario and map'), ('plan', '规划方案', 'Plans'), ('compare', '算法比较（单个任务地图）', 'Algorithm comparison (one task map)'),
          ('study', '统计比较（课题：多实例 × 多种子）', 'Statistical comparison (study)'), ('recommend', '推荐评估', 'Recommendation'),
          ('sim', '仿真执行', 'Simulation')]

FORMATS = {'svg': 'image/svg+xml', 'pdf': 'application/pdf', 'eps': 'application/postscript', 'png': 'image/png', 'tiff': 'image/tiff'}


def _fleet_run(ctx: D.Context) -> str | None:
    r = ctx.run
    if not r or r.get('problem_class') != 'fleet_routing' or r.get('state') != 'done' or not r.get('routes'):
        return '需要一个已完成的多车方案（在「方案对比」里选中）'
    return None


def _need_scenario(ctx):
    return None if ctx.scenario and ctx.scenario.get('problem_class') == 'fleet_routing' else '需要打开一个多车任务地图'


def _need_asset(ctx):
    if not ctx.asset:
        return '需要一个地图资产（由资产生成的任务地图会自动带上，或在资产库里选中地图）'
    return None if ctx.geometry and ctx.geometry.get('boundary') else '这个地图资产提取不出规划几何'


def _same_instance(ctx):
    done = [r for r in ctx.runs if r.get('state') == 'done' and r.get('routes') and r.get('problem_class') == 'fleet_routing']
    if len(done) < 2 or len({D._sha(r.get('instance')) for r in done[:4]}) != 1:
        return '需要同一任务地图（同一实例）上至少 2 个已完成的多车方案'
    return None


def _need_runs(ctx):
    done = [r for r in ctx.runs if r.get('state') == 'done' and (r.get('objective') or {}).get('value') is not None]
    if len(done) < 2:
        return '需要至少 2 个算法的已完成方案（在「推荐与求解」里勾选多个算法对比）'
    if len({D._sha(r.get('instance')) for r in done}) != 1:
        return '单实例算法比较必须使用相同的求解输入；跨实例比较请使用研究课题'
    return None


def _need_trace(ctx):
    from ..analysis import trace_matches
    if len(ctx.runs) >= 2:
        why = _need_runs(ctx)
        if why:
            return why
    return None if any(trace_matches(r) for r in ctx.runs if r.get('state') == 'done') else \
        '所选方案没有可与目标值对照的求解过程轨迹（部分求解器不上报，或上报的是内部目标）'


def _need_study(ctx, extra: Callable[[dict], str | None] | None = None):
    an = ctx.analysis
    if not ctx.study:
        return '需要选中一个研究课题（「算法实验」工作区）'
    if not an or an.get('empty'):
        return '课题还没有完成的运行'
    return extra(an) if extra else None


def _need_sim(ctx, two: bool = False):
    if not ctx.sim:
        return '需要这个方案的一次田间仿真结果（「方案对比」里点 Gazebo 执行）'
    if two and len([t for t in (ctx.sim.get('trails') or {}).values() if len(t) > 1]) < 2:
        return '至少需要 2 台车的轨迹'
    if not ctx.sim.get('trails'):
        return '仿真结果没有记录轨迹'
    return None


def _need_tracking(ctx):
    why = _need_sim(ctx)
    if why:
        return why
    return None if D.cross_track(ctx.sim) else '没有有效跟踪误差记录（排队或取消不产生测量）'


@dataclass(frozen=True)
class Spec:
    key: str
    group: str
    zh: str
    en: str
    fn: Callable
    need: Callable[[D.Context], str | None]
    size: str = 'single'


SPECS: list[Spec] = [
    Spec('scenario_map', 'map', '任务地图', 'Task map', maps.scenario_map, _need_scenario),
    Spec('map_extraction', 'map', '地图 → 规划几何', 'Map to planning geometry', maps.map_extraction, _need_asset, 'double'),
    Spec('routes', 'plan', '多车路线', 'Multi-vehicle routes', maps.routes, _fleet_run),
    Spec('workload', 'plan', '每车工作量', 'Per-vehicle workload', charts.workload, _fleet_run),
    Spec('schedule', 'plan', '车辆时间线', 'Vehicle timelines', charts.schedule, _fleet_run, 'double'),
    Spec('routes_compare', 'compare', '多方案路线对照', 'Route comparison', maps.routes_compare, _same_instance, 'double'),
    Spec('algo_compare', 'compare', '算法结果与计算时间', 'Objective and time per algorithm', charts.algo_compare, _need_runs),
    Spec('convergence', 'compare', '求解收敛过程', 'Anytime convergence', charts.convergence, _need_trace),
    Spec('perf_profile', 'study', '性能曲线', 'Performance profiles', study.perf_profile,
         lambda c: _need_study(c, lambda an: None if len(an['algorithms']) >= 2 and an.get('n_complete_instances') else '需要 ≥2 个算法且至少 1 个所有算法齐全的实例')),
    Spec('gap_distribution', 'study', '差距分布', 'Gap distribution', study.gap_distribution,
         lambda c: _need_study(c, lambda an: None if any(an['ecdf'].values()) else '还没有可计算差距的有效运行')),
    Spec('cd_diagram', 'study', '临界差图', 'Critical-difference diagram', study.cd_diagram,
         lambda c: _need_study(c, lambda an: cd_unavailable_reason(an.get('tests') or {}))),
    Spec('pairwise', 'study', '两两检验', 'Pairwise tests', study.pairwise,
         lambda c: _need_study(c, lambda an: None if len(an['algorithms']) >= 2 and any(r['n'] for r in an['tests']['wilcoxon_holm']) else '需要 ≥2 个算法的齐全实例')),
    Spec('anytime_median', 'study', '差距随时间（中位数）', 'Median anytime gap', study.anytime_median,
         lambda c: _need_study(c, lambda an: None if any(v for v in (an.get('anytime') or {}).get('median', {}).values()) else '运行没有可与目标值对照的求解过程轨迹')),
    Spec('time_quality', 'study', '时间–质量权衡', 'Time–quality trade-off', study.time_quality,
         lambda c: _need_study(c, lambda an: None if any(p.get('gap') is not None and p.get('time') is not None for p in an['scatter']) else '还没有可计算差距的有效运行')),
    Spec('instance_space', 'study', '实例空间', 'Instance space', study.instance_space,
         lambda c: _need_study(c, lambda an: None if any(p.get('projection_status') == 'ok' for p in (c.space or {}).get('panels', [])) else '至少需要 3 个带特征的实例')),
    Spec('selection_eval', 'recommend', '推荐 vs 单一最佳 vs 理想选择', 'Recommendation vs SBS vs VBS', study.selection_eval,
         lambda c: _need_study(c, lambda an: None if study.selection_table(c.study_runs)['n'] >= 1 else '需要推荐算法也在课题里运行过的齐全实例')),
    Spec('sim_tracks', 'sim', '仿真轨迹与规划路线', 'Simulated tracks vs plan', maps.sim_tracks, _need_sim, 'double'),
    Spec('plan_vs_exec', 'sim', '规划 vs 执行', 'Planned vs executed', charts.plan_vs_exec, _need_sim, 'double'),
    Spec('tracking_error', 'sim', '路径跟踪误差', 'Path-tracking error', charts.tracking_error, _need_tracking, 'double'),
    Spec('separation', 'sim', '车间最小距离', 'Inter-vehicle distance', charts.separation, lambda c: _need_sim(c, True)),
    Spec('task_progress', 'sim', '任务完成进度', 'Task completion over time', charts.task_progress, _need_sim),
]
BY_KEY = {s.key: s for s in SPECS}


# ------------------------------------------------------------------ context --
def build_context(lab, q: dict) -> D.Context:
    from .. import analysis
    from ..assets import importer, store as astore
    from ..scenarios import get_scenario
    from ..services import ServiceError
    ctx = D.Context()
    run = lab.store.get_run(q['run_id']) if q.get('run_id') else None
    if q.get('run_id') and run is None:
        raise ServiceError(404, 'unknown_run', '没有这次求解的记录')
    if run:
        ctx.run = run
    ids = [x for x in (q.get('run_ids') or '').split(',') if x]
    runs = lab.store.runs_by_ids(ids) if ids else (lab.store.list_runs(job_id=run['job_id'], limit=50) if run else [])
    if run:
        runs = [run] + [r for r in runs if r['run_id'] != run['run_id'] and r.get('scenario_id') == run['scenario_id']]
    ctx.runs = runs
    sid = q.get('scenario_id') or (run or {}).get('scenario_id')
    if run:
        if q.get('scenario_id') and q['scenario_id'] != run.get('scenario_id'):
            raise ServiceError(409, 'figure_source_mismatch', '指定的场景与求解记录不一致')
        # An editable scenario is never the source of a historical result figure.
        # The submitted instance can also differ from the scenario's saved instance.
        ctx.scenario = copy.deepcopy(run.get('scenario_snapshot') or {
            'id': run.get('scenario_id'), 'problem_class': run.get('problem_class')})
        ctx.scenario['instance'] = copy.deepcopy(run.get('instance') or ctx.scenario.get('instance'))
    elif sid:
        try:
            ctx.scenario = get_scenario(sid)
        except KeyError:
            raise ServiceError(404, 'unknown_scenario', '没有这个场景') from None
    inst = (run or {}).get('instance') or (ctx.scenario or {}).get('instance') or {}
    if ctx.scenario and ctx.scenario.get('problem_class') == 'fleet_routing' and (inst.get('vehicles') or {}).get('travel') == 'rows':
        try:
            from .. import lanes
            ctx.lanes = lanes.preview(inst)['lanes']
        except Exception:  # noqa: BLE001 - lanes are a visual aid only
            ctx.notes.append('作物行通道无法生成，图中不画')
    sess = q.get('session_id')
    if not sess and run and run.get('problem_class') == 'fleet_routing':
        rows = lab.fieldsim.results(run['run_id'])
        sess = rows[0]['session_id'] if rows else None
    if sess:
        try:
            ctx.sim = lab.fieldsim.result(sess)
        except Exception:  # noqa: BLE001 - missing or unreadable result
            ctx.notes.append(f'仿真结果 {sess} 读取失败')
        if ctx.sim and run and ctx.sim.get('run_id') != run['run_id']:
            raise ServiceError(409, 'figure_source_mismatch', '仿真会话与求解记录不一致，不能叠加展示')
    if q.get('study_id'):
        ctx.study = lab.studies.get(q['study_id'])
        if ctx.study:
            ctx.study_runs = lab.studies.runs(q['study_id'])
            ctx.analysis = analysis.analyze(ctx.study_runs)
            try:
                ctx.space = analysis.instance_space(ctx.study_runs)
            except Exception:  # noqa: BLE001
                ctx.space = None
    aid, av = q.get('asset_id'), q.get('asset_v')
    if not aid:
        bg = ((ctx.scenario or {}).get('layers') or {}).get('background') or {}
        aid, av = (bg.get('asset') or {}).get('id'), (bg.get('asset') or {}).get('version')
    if aid:
        try:
            m = astore.load(aid)
            ver = astore.version(m, int(av) if av else m['current'])
            if m['kind'] == 'map':
                ctx.asset, ctx.asset_version = m, ver
                ctx.geometry = importer.planning_geometry(m, ver, {})
        except Exception:  # noqa: BLE001 - asset deleted or not a map
            ctx.notes.append(f'地图资产 {aid} 读取失败')
    return ctx


def catalog(ctx: D.Context) -> list[dict]:
    out = []
    for s in SPECS:
        try:
            why = s.need(ctx)
        except Exception as exc:  # noqa: BLE001
            why = f'检查失败：{exc}'
        out.append({'key': s.key, 'group': s.group, 'title_zh': s.zh, 'title_en': s.en, 'size': s.size, 'available': why is None, 'reason': why})
    return out


# ---------------------------------------------------------------- rendering --
_LOCK = threading.Lock()
_CACHE: OrderedDict = OrderedDict()
CACHE_MAX_BYTES = 64 * 1024 * 1024


def _draw(spec: Spec, ctx: D.Context, st: Style, opts: dict):
    with apply(st):
        return spec.fn(ctx, st, opts)


def render(key: str, ctx: D.Context, st: Style, fmt: str, opts: dict | None = None) -> tuple[bytes, dict]:
    spec = BY_KEY[key]
    why = spec.need(ctx)
    if why:
        raise LookupError(why)
    ck = (key, ctx.digest(), st, fmt, json.dumps(opts or {}, sort_keys=True))
    with _LOCK:
        if ck in _CACHE:
            _CACHE.move_to_end(ck)
            blob, meta, _ = _CACHE[ck]
            return blob, copy.deepcopy(meta)
        with apply(st):
            fig, cap, table = spec.fn(ctx, st, opts or {})
            fit_height(fig)
            buf = io.BytesIO()
            title = spec.zh if st.lang == 'zh' else spec.en
            kw: dict = {'format': fmt}
            if fmt in ('png', 'tiff'):
                kw['dpi'] = st.dpi
            if fmt == 'tiff':
                kw['pil_kwargs'] = {'compression': 'tiff_lzw'}
            if fmt == 'pdf':
                kw['metadata'] = {'Title': title, 'Creator': '农业机器人路径规划工作台', 'Subject': cap[st.lang][:250]}
            if fmt == 'svg':
                kw['metadata'] = {'Title': title, 'Description': cap[st.lang][:400]}
            fig.savefig(buf, **kw)
            w, h = fig.get_size_inches()
        meta = {'key': key, 'title': title, 'caption': cap, 'size_mm': [round(float(w) * 25.4, 1), round(float(h) * 25.4, 1)], 'style': st.__dict__.copy(),
                'sources': ctx.sources(), 'digest': ctx.digest(), 'table': table}
        blob = buf.getvalue()
        size = len(blob) + len(json.dumps(meta, ensure_ascii=False, default=str).encode())
        if size <= CACHE_MAX_BYTES:
            _CACHE[ck] = (blob, copy.deepcopy(meta), size)
        while len(_CACHE) > 120 or sum(v[2] for v in _CACHE.values()) > CACHE_MAX_BYTES:
            _CACHE.popitem(last=False)
        return blob, meta


def _csv(rows: list[dict]) -> bytes:
    keys: list = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    s = io.StringIO()
    w = csv.DictWriter(s, fieldnames=keys, extrasaction='ignore')
    w.writeheader()
    for r in rows:
        w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else ('' if v is None else (float(v) if isinstance(v, np.floating) else v)))
                    for k, v in r.items()})
    return ('﻿' + s.getvalue()).encode('utf-8')   # BOM: opens correctly in Excel


def bundle(ctx: D.Context, preset: str, lang: str, formats: list[str], dpi: int = 600) -> tuple[bytes, dict]:
    """Every available figure as a paper-ready package; nothing is drawn without data."""
    zbuf = io.BytesIO()
    manifest = {'generated_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'preset': preset, 'preset_label': PRESETS[preset]['label'],
                'lang': lang, 'dpi': dpi, 'software': {'matplotlib': matplotlib.__version__, 'numpy': np.__version__, 'scipy': scipy.__version__},
                'sources': ctx.sources(), 'figures': [], 'skipped': [], 'notes': ctx.notes}
    captions = []
    n = 0
    with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as z:
        for s in SPECS:
            why = s.need(ctx)
            if why:
                manifest['skipped'].append({'key': s.key, 'title': s.zh if lang == 'zh' else s.en, 'reason': why})
                continue
            n += 1
            st = Style(preset=preset, size=s.size, lang=lang, dpi=dpi)
            stem = f'fig{n:02d}_{s.key}'
            files = []
            meta = None
            for fmt in formats:
                try:
                    blob, meta = render(s.key, ctx, st, fmt)
                except Exception as exc:  # noqa: BLE001 - one broken figure must not sink the bundle
                    manifest['skipped'].append({'key': s.key, 'title': s.zh, 'reason': f'绘制失败：{exc}'})
                    break
                z.writestr(f'figures/{stem}.{fmt}', blob)
                files.append(f'figures/{stem}.{fmt}')
            if meta is None:
                continue
            for name, rows in (meta['table'] or {}).items():
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    z.writestr(f'source_data/{stem}_{name}.csv', _csv(rows))
                    files.append(f'source_data/{stem}_{name}.csv')
            manifest['figures'].append({'n': n, 'key': s.key, 'title': meta['title'], 'size_mm': meta['size_mm'], 'files': files,
                                        'caption_zh': meta['caption']['zh'], 'caption_en': meta['caption']['en'], 'digest': meta['digest']})
            label = f'图 {n}' if lang == 'zh' else f'Fig. {n}'
            captions.append(f"**{label}　{meta['title']}**　{meta['caption'][lang]}\n")
        z.writestr('captions.md', ('\n'.join(captions) or '（没有可绘制的图）\n').encode('utf-8'))
        z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=1, default=str).encode('utf-8'))
        z.writestr('README.txt', README.encode('utf-8'))
    return zbuf.getvalue(), manifest


README = """论文图包（农业机器人路径规划工作台导出）

figures/       每张图的矢量版（PDF / SVG / EPS，字体内嵌为 TrueType，SVG 文字可编辑）与位图（PNG / TIFF，默认 600 dpi）
source_data/   每张图实际绘制的数据（CSV，UTF-8），供审稿与复现
captions.md    图注（含样本量、统计方法、数据来源）
manifest.json  生成时间、版式预设、软件版本、场景 / 方案 / 仿真 / 课题 / 资产版本等来源与数据指纹

版式：宽度按期刊栏宽（IEEE 88.9/181.9 mm，Elsevier 90/140/190 mm，Springer 84/174 mm，学位论文 120–150 mm），
正文字号 7.5–9 pt，线宽 ≥ 0.5 pt，Okabe–Ito 色盲友好配色并配合标记 / 线型区分，可灰度打印。
所有图只由工作台记录的真实运行与仿真数据生成；缺数据的图不会出现在包里，原因见 manifest.json 的 skipped。
"""
