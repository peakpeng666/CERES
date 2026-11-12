"""Versioned agricultural catalog. Every parameter carries a value, a source and an evidence basis.

The catalog is data; it says what a machine or operation is. `rig.py` and `assess.py` derive what follows
from combining them, so a number on screen is always either a sourced value or a named derivation.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .. import lanes

SCHEMA = 'urp.agri-catalog/1'
DATA = Path(__file__).parent / 'data'
FILES = ('sources', 'environments', 'crops', 'operations', 'platforms', 'implements')
TIERS = ('display', 'planning', 'simulation')
TIER_LABEL = {'display': '展示', 'planning': '可规划', 'simulation': '可仿真'}
BASIS_LABEL = {'standard': '标准', 'paper': '论文', 'extension': '推广资料', 'vendor': '厂商资料', 'project': '项目模型', 'assumption': '假设'}
PLATFORM_CLASSES = {'diff_drive': '差速', 'skid_steer': '滑移转向', 'ackermann': '阿克曼', 'tracked': '履带', 'uav': '无人机', 'rail': '轨道', 'boat': '船艇'}


def _param_sources(node):
    if isinstance(node, dict):
        if 's' in node and isinstance(node['s'], str):
            yield node['s']
        for value in node.values():
            yield from _param_sources(value)
    elif isinstance(node, list):
        for value in node:
            yield from _param_sources(value)


@lru_cache(maxsize=1)
def load() -> dict:
    data = {name: json.loads((DATA / f'{name}.json').read_text(encoding='utf-8')) for name in FILES}
    known = {s['id'] for s in data['sources']}
    for group in ('operations', 'platforms', 'implements'):
        for item in data[group]:
            missing = set(_param_sources(item)) - known
            if missing:
                raise ValueError(f'{item["id"]} 引用了未登记的来源 {sorted(missing)}')
    for crop in data['crops']:
        spec = lanes.CROPS.get(crop['lane']) if crop.get('lane') else None
        crop['row_spacing_m'] = {'v': spec[0], 's': crop.get('spacing_source', 'assumption')} if spec else None
        crop['row_width_m'] = {'v': spec[1], 's': 'assumption'} if spec else None
        crop['canopy_height_m'] = {'v': spec[2], 's': 'assumption'} if spec else None
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'schema': SCHEMA, 'version': digest[:12], **data, 'labels': {'tiers': TIER_LABEL, 'basis': BASIS_LABEL, 'platform_classes': PLATFORM_CLASSES}}


def by_id(group: str, item_id: str) -> dict:
    for item in load()[group]:
        if item['id'] == item_id:
            return item
    raise ValueError(f'目录中没有 {group}/{item_id}')


def value(node, default=None):
    return default if node is None else node.get('v', default) if isinstance(node, dict) else node


def basis(source_id: str) -> str:
    return by_id('sources', source_id)['kind']


def compatible_implements(platform: dict, operation_id: str) -> list[dict]:
    return [i for i in load()['implements'] if operation_id in i['operations'] and i['hitch'] in platform['hitch']
            and ('for' not in i or platform['id'] in i['for'])]
