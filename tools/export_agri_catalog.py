#!/usr/bin/env python3
"""Write docs/agri/catalog.md from the agricultural catalog, so the document cannot drift from the data."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/lab'))

from ceres_lab.agri import catalog as cat  # noqa: E402


def cell(c: dict, node, unit: str = '') -> str:
    if not node:
        return '—'
    b = c['labels']['basis'][cat.by_id('sources', node['s'])['kind']]
    span = f"（{node['r'][0]}–{node['r'][1]}）" if node.get('r') else ''
    return f"{node['v']:g}{unit}{span} · {b}"


def render() -> str:
    c = cat.load()
    out = ['# 参数目录', '', f"由 `tools/export_agri_catalog.py` 从目录生成，版本 `{c['version']}`。模型说明见[田间作业模型](model.md)。", '']
    out += ['## 车辆', '', '| 平台 | 类别 | 层级 | 长 m | 宽 m | 轮距 m | 质量 kg | 转弯半径 m | 作业速度上限 km/h | 续航 h |', '|---|---|---|---|---|---|---|---|---|---|']
    for p in c['platforms']:
        out.append(f"| {p['name']} | {c['labels']['platform_classes'][p['class']]} | {c['labels']['tiers'][p['tier']]} | {cell(c, p['length_m'])} | {cell(c, p['width_m'])} | {cell(c, p['track_m'])} | {cell(c, p['mass_kg'])} | {cell(c, p['min_turn_radius_m'])} | {cell(c, p['speed_work_max_kmh'])} | {cell(c, p.get('endurance_h'))} |")
    out += ['', '## 机具', '', '| 机具 | 类别 | 连接 | 作业 | 幅宽 m | 质量 kg | 容量 |', '|---|---|---|---|---|---|---|']
    ops = {o['id']: o['name'] for o in c['operations']}
    for i in c['implements']:
        out.append(f"| {i['name']} | {i['category']} | {i['hitch']} | {'、'.join(ops[o] for o in i['operations'])} | {cell(c, i['width_m'])} | {cell(c, i['mass_kg'])} | {cell(c, i.get('tank'), ' ' + i['tank']['u'] if i.get('tank') else '')} |")
    out += ['', '## 作业', '', '| 作业 | 模型 | 阶段 | 资源 | 速度 km/h | 田间效率 |', '|---|---|---|---|---|---|']
    family = {'line': '作业行', 'point': '点任务', 'cooperative': '协同', 'support': '支持'}
    for o in c['operations']:
        out.append(f"| {o['name']} | {family[o['family']]} | {o['stage']} | {o.get('resource_name') or '—'} | {cell(c, o.get('speed'))} | {cell(c, o.get('efficiency'))} |")
    out += ['', '## 环境', '', '| 环境 | 结构 | 平台 | 作业行作业 |', '|---|---|---|---|']
    for e in c['environments']:
        out.append(f"| {e['name']} | {e['structure']} | {'、'.join(c['labels']['platform_classes'][x] for x in e['platforms'])} | {'、'.join(ops[o] for o in e['line_operations']) or '—'} |")
    out += ['', '## 来源', '', '| 编号 | 类型 | 核对 | 出处 | 范围 |', '|---|---|---|---|---|']
    for s in c['sources']:
        link = f"[{s['title']}]({s['url']})" if str(s.get('url', '')).startswith('http') else s['title']
        out.append(f"| `{s['id']}` | {c['labels']['basis'].get(s['kind'], s['kind'])} | {'原文' if s['checked'] == 'document' else '检索摘要'} | {link} | {s['scope']} |")
    return '\n'.join(out) + '\n'


if __name__ == '__main__':
    target = ROOT / 'docs/agri/catalog.md'
    if '--check' in sys.argv:
        sys.exit(0 if target.read_text(encoding='utf-8') == render() else 1)
    target.write_text(render(), encoding='utf-8')
