"""What the gateway exposes: preview a mission, or turn it into a saved scenario."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import catalog as cat
from . import mission as missions


def compile_preview(mission: dict) -> dict:
    compiled = missions.compile_mission(mission)
    inst = compiled['instance']
    if inst is None:
        return {'checks': compiled['checks'], 'blocked': True, 'estimate': None, 'rig': None, 'lines': [], 'field': None}
    return {'checks': compiled['checks'], 'blocked': compiled['blocked'], 'estimate': compiled['estimate'], 'rig': compiled['rig'],
            'lines': compiled['lines'], 'field': inst['field'], 'obstacles': inst['obstacles'], 'stations': inst['stations'],
            'headland_m': inst['headland_m'], 'recommended_headland_m': compiled['recommended_headland_m'], 'pitch_m': compiled['pitch_m'], 'pattern': compiled['pattern'],
            'objective': inst['objective'], 'catalog_version': cat.load()['version']}


def scenario(mission: dict) -> dict:
    compiled = missions.compile_mission(mission)
    if compiled['instance'] is None or compiled['blocked']:
        first = next((c for c in compiled['checks'] if c['level'] == 'block'), None)
        raise ValueError(f"{first['title']}：{first['detail']}" if first else '这项任务还不能规划')
    inst = compiled['instance']
    rig = compiled['rig']
    op = cat.by_id('operations', mission['operation'])
    env = cat.by_id('environments', mission['environment'])
    crop = cat.by_id('crops', mission['crop']) if mission.get('crop') else None
    est = compiled['estimate']
    digest = hashlib.sha256(json.dumps(mission, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:6]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    title = mission.get('title') or f"{env['name']}·{op['name']}"
    place = f"{crop['name']} · " if crop else ''
    return {
        'id': f'field-{stamp}-{digest}', 'title': title[:40],
        'summary': f"{place}{est['field_ha']:g} ha · {est['lines']} 行 · {inst['machines']} 台 {rig['name']}",
        'problem_class': 'field_operation', 'frame': {'kind': 'field', 'units': 'm', 'crs': 'local'}, 'cost_unit': 'm',
        'provenance': {'generator': 'ceres_lab.agri.mission', 'params': {'mission': mission, 'catalog_version': cat.load()['version']}, 'seed': None,
                       'note': '由作业设计生成；田块为生成几何，机组参数见目录来源。'},
        'sim': {'supported': False, 'world': None, 'spawn': None, 'reason': '田间仿真世界在执行时生成'},
        'instance': inst, 'editable': {}, 'layers': {}, 'origin': 'user',
    }
