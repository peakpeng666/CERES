"""Task composer: one sentence → structured spec → editable farm map scenario (problem class `fleet_routing`)."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from datetime import datetime, timezone

from .. import time_windows
from . import generate as gen
from .nl import KINDS, fill_defaults, parse

OBSTACLE_LABEL = {'pond': '池塘', 'building': '建筑', 'greenhouse': '温室', 'tree': '树', 'ditch': '水渠', 'rock': '石块'}
DEPOT_LABEL = {'sw': '西南角', 'se': '东南角', 'nw': '西北角', 'ne': '东北角', 'center': '中部', 'n': '北侧', 's': '南侧', 'e': '东侧', 'w': '西侧'}
DIST_LABEL = {'uniform': '均匀分布', 'clustered': '成片聚集', 'rows': '沿作物行'}
EXAMPLES = [
    '在 200×120 米的果园里，3 台机器人从西南角车库出发，给 40 个点采样，每台最多装 10 个样本，避开两个池塘和一栋房子',
    '5 公顷麦田分成 6 块，4 辆无人车巡检 60 个巡检点，要求尽快完成，有 3 棵大树和一条水渠',
    '30 亩菜地，两台小车从东北角出发运输，每台载重 20，十二个投放点聚集分布',
    '在 L 形的 150×90 米茶园里，2 台植保机器人沿作物行喷洒 36 个点，速度 0.8 m/s',
]


def describe(spec: dict) -> list[dict]:
    """Human-readable cards for every spec field (what the generator will use)."""
    obstacles = '、'.join(f"{v} 处{OBSTACLE_LABEL[k]}" for k, v in (spec.get('obstacles') or {}).items() if v) or '无'
    W, H = spec['size']
    return [
        {'key': 'kind', 'label': '作业', 'value': KINDS[spec['kind']][0]},
        {'key': 'size', 'label': '场地', 'value': f'{W:g} × {H:g} m' + ('（L 形）' if spec['shape'] == 'L' else '（不规则）' if spec['shape'] == 'irregular' else '')},
        {'key': 'plots', 'label': '地块', 'value': f"{spec['plots']} 块"},
        {'key': 'tasks', 'label': '任务点', 'value': f"{spec['tasks']} 个 · {DIST_LABEL[spec['distribution']]}"},
        {'key': 'vehicles', 'label': '车队', 'value': f"{spec['vehicles']} 台" + (f" · 每台容量 {spec['capacity']}" if spec.get('capacity') else '')},
        {'key': 'obstacles', 'label': '障碍', 'value': obstacles},
        {'key': 'depot', 'label': '车库', 'value': DEPOT_LABEL[spec['depot']]},
        {'key': 'objective', 'label': '目标', 'value': '最晚完成时间最短（均衡分工）' if spec['objective'] == 'makespan' else '总里程最短'},
        {'key': 'speed_mps', 'label': '车速', 'value': f"{spec['speed_mps']:g} m/s"},
    ] + _time_cards(spec)


def _time_cards(spec: dict) -> list[dict]:
    rows = [(k, label, spec.get(k)) for k, label in (('start_clock', '出发'), ('earliest', '最早'), ('deadline', '截止')) if spec.get(k)]
    return [{'key': k, 'label': label, 'value': value} for k, label, value in rows]


def apply_time_window(instance: dict, spec: dict) -> str | None:
    """Puts a request's shared window on every task; a time of day with no departure to count from is reported, not guessed."""
    try:
        window = time_windows.resolve(spec.get('deadline'), spec.get('earliest'), spec.get('start_clock'))
    except ValueError as exc:
        return f'时间窗未写入任务：{exc}'
    if not window:
        return None
    if window[0] < 0 or window[1] is not None and window[1] < max(window[0], 1):
        return '时间窗未写入任务：截止或最早时间不在出发时刻之后'
    for t in instance['tasks']:
        t['time_window'] = list(window)
    if spec.get('start_clock'):
        instance['start_clock'] = spec['start_clock']
    return None


def generation_report(spec: dict, instance: dict, notes: list[str] | None = None) -> tuple[dict, list[dict]]:
    """Describe the generated map, retaining unmet requests rather than inventing geometry."""
    requested = {k: spec[k] for k in ('plots', 'tasks', 'obstacles')}
    actual = {'plots': len(instance['plots']), 'tasks': len(instance['tasks']),
              'obstacles': dict(Counter(o['kind'] for o in instance['obstacles']))}
    shortfalls = [n for n in notes or [] if n]
    for key, label in (('plots', '地块'), ('tasks', '任务点')):
        if actual[key] < requested[key]:
            shortfalls.append(f'{label}要求 {requested[key]}，实际 {actual[key]}')
    for kind, count in requested['obstacles'].items():
        placed = actual['obstacles'].get(kind, 0)
        if placed < count:
            shortfalls.append(f'{OBSTACLE_LABEL[kind]}要求 {count}，实际 {placed}')
    report = {'status': 'partial' if shortfalls else 'complete', 'requested': requested,
              'actual': actual, 'shortfalls': shortfalls}
    return report, describe({**spec, **actual})


def generation_note(report: dict) -> str:
    return ' · 未完全生成：' + '；'.join(report['shortfalls']) if report['shortfalls'] else ''


def compose(text: str, seed: int | None = None, overrides: dict | None = None) -> dict:
    parsed = parse(text)
    spec, defaults = fill_defaults({**parsed['spec'], **(overrides or {})})
    if seed is None:
        seed = spec.get('seed')
        if seed is None:
            seed = int(hashlib.sha256(parsed['text'].encode()).hexdigest()[:8], 16) % 100000
    instance = gen.generate(spec, seed)
    generation, cards = generation_report(spec, instance, [apply_time_window(instance, spec)] + [f'未进入任务：{u}' for u in parsed['unsupported']])
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    slug = re.sub(r'[^a-z0-9]+', '-', spec['kind'])[:12]
    sid = f'task-{slug}-{stamp}-{seed % 1000:03d}'
    W, H = spec['size']
    title = (parsed['text'][:22] + '…') if len(parsed['text']) > 22 else (parsed['text'] or '新任务')
    n_obs = len(instance['obstacles'])
    word = instance['meta']['task_word']
    scenario = {
        'id': sid, 'title': title,
        'summary': f"{instance['vehicles']['count']} 台车 · {len(instance['tasks'])} 个{word} · {W:g}×{H:g} m · {len(instance['plots'])} 块地 · {n_obs} 处障碍" + generation_note(generation),
        'problem_class': 'fleet_routing', 'frame': {'kind': 'field', 'units': 'm', 'crs': 'local'}, 'cost_unit': 'm',
        'provenance': {'generator': 'urp_lab.compose', 'params': {'text': parsed['text'], 'spec': spec, 'seed': seed}, 'seed': seed,
                       'note': '由任务描述生成；地块、障碍、任务点与车队都可在地图上继续修改。'},
        'sim': {'supported': False, 'world': None, 'spawn': None, 'reason': '生成的农田地图不是 Gazebo 世界'},
        'instance': instance, 'editable': {'tasks': True, 'depot': True, 'capacity': True, 'demands': True},
        'layers': {}, 'origin': 'user', 'compose': {'text': parsed['text'], 'found': parsed['found'], 'defaults': defaults,
                                                     'cards': cards, 'generation': generation, 'seed': seed},
    }
    return scenario


def from_field(field_id: str, text: str = '', seed: int | None = None, overrides: dict | None = None) -> dict:
    """Use a real Fields2Benchmark boundary as the farm map; plots, obstacles and tasks are laid out inside it."""
    from ..library import fields
    base = fields.scenario(field_id)
    pts = base['instance']['field']
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    outline = [[round(x - min(xs), 2), round(y - min(ys), 2)] for x, y in pts]
    parsed = parse(text)
    extra = {k: v for k, v in (overrides or {}).items() if k not in ('size', 'area_m2', 'shape')}
    spec, defaults = fill_defaults({**parsed['spec'], **extra, 'size': [max(xs) - min(xs), max(ys) - min(ys)], 'shape': 'rect'})
    seed = seed if seed is not None else int(hashlib.sha256((field_id + parsed['text']).encode()).hexdigest()[:8], 16) % 100000
    sc = compose(parsed['text'] or '真实田块任务', seed, spec)
    inst = gen.generate(spec, seed, outline)
    notes = [apply_time_window(inst, spec)] + [f'未进入任务：{u}' for u in parsed['unsupported']]
    sc['instance'] = inst
    word = inst['meta']['task_word']
    W, H = spec['size']
    sc['title'] = (base['title'] + ' · ' + (parsed['text'][:16] + '…' if len(parsed['text']) > 16 else parsed['text'] or '任务地图'))[:60]
    sc['summary'] = (f"{inst['vehicles']['count']} 台车 · {len(inst['tasks'])} 个{word} · 真实田块 {base['summary'].split('·')[1].strip()} · "
                     f"{len(inst['plots'])} 块地 · {len(inst['obstacles'])} 处障碍")
    sc['provenance']['note'] = f"边界来自 Fields2Benchmark 真实田块 {field_id}，地块、障碍与任务点在田内生成。"
    sc['provenance']['ref'] = 'mier2024fields2benchmark'
    generation, cards = generation_report(spec, inst, notes)
    sc['summary'] += generation_note(generation)
    sc['compose']['cards'] = cards
    sc['compose']['generation'] = generation
    sc['compose']['defaults'] = defaults
    return sc


def gen_plot_union(plots):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    return unary_union([Polygon(p['polygon']) for p in plots])


