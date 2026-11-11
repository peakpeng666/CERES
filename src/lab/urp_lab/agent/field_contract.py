# CERES 田间全覆盖行作任务契约（与合同及求解工具共用 Schema）
from __future__ import annotations

import copy
import json

from ..agri import catalog


def _obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


POINT = {'type': 'array', 'items': {'type': 'number', 'minimum': -1e6, 'maximum': 1e6}, 'minItems': 2, 'maxItems': 2}
RING = {'type': 'array', 'items': POINT, 'minItems': 3, 'maxItems': 1000}
MISSION_SCHEMA = _obj({
    **{key: {'type': 'string', 'enum': [row['id'] for row in catalog.load()[group]]}
       for key, group in [('environment', 'environments'), ('crop', 'crops'), ('operation', 'operations'),
                          ('platform', 'platforms'), ('implement', 'implements')]},
    'title': {'type': 'string', 'maxLength': 80},
    'field': _obj({'field_id': {'type': 'string', 'maxLength': 80},
                   'shape': {'type': 'string', 'enum': ['rect', 'trapezoid', 'l_shape', 'pentagon']},
                   'width_m': {'type': 'number', 'minimum': 5, 'maximum': 5000},
                   'height_m': {'type': 'number', 'minimum': 5, 'maximum': 5000},
                   'ring': RING, 'obstacles': {'type': 'array', 'items': RING, 'maxItems': 100}}),
    'fleet': {'type': 'integer', 'minimum': 1, 'maximum': 12},
    'headland_m': {'type': 'number', 'minimum': 0, 'maximum': 500},
    'angle_deg': {'type': 'number', 'minimum': -360, 'maximum': 360},
    'station': {'type': 'string', 'enum': ['sw', 'se', 'nw', 'ne', 's', 'n']},
    'objective': {'type': 'string', 'enum': ['nonworking', 'makespan']},
    'overrides': _obj({key: {'type': 'number', 'minimum': lo, 'maximum': hi} for key, lo, hi in [
        ('work_width_m', .1, 100), ('min_turn_radius_m', 0, 100), ('speed_kmh', .01, 50),
        ('endurance_h', .01, 168), ('capacity', .001, 1e6), ('rate_per_ha', .001, 1e6)]}),
}, ('environment', 'operation', 'platform', 'implement', 'field'))


def normalize(value) -> dict:
    from .tools import validate
    if not isinstance(value, dict):
        raise ValueError('田间作业任务须为对象')
    mission = copy.deepcopy(value)
    for key in list(mission):
        if mission[key] is None and key not in MISSION_SCHEMA['required']:
            mission.pop(key)
    if isinstance(mission.get('overrides'), dict):
        mission['overrides'] = {k: v for k, v in mission['overrides'].items() if v is not None}
    errors = validate(MISSION_SCHEMA, mission, 'field_mission')
    if errors:
        raise ValueError('；'.join(errors[:6]))
    field = mission['field']
    if field.get('field_id') or field.get('ring'):
        for key in ('width_m', 'height_m', 'shape'):
            field.pop(key, None)
    choices = [bool(field.get('field_id')), bool(field.get('ring')), 'width_m' in field or 'height_m' in field]
    if sum(choices) != 1 or (choices[2] and not {'width_m', 'height_m'} <= field.keys()):
        raise ValueError('田块须明确给出真实田块编号、边界 ring、或完整长宽之一')
    if field.get('field_id'):
        from .contract import sealed_field
        if sealed_field(field['field_id']):
            raise ValueError('封存测试田块不能用于规划')
        if field.get('obstacles'):
            raise ValueError('真实田块使用其存档障碍；自定义障碍请使用显式边界')
    mission.setdefault('fleet', 1)
    mission.setdefault('station', 'sw')
    mission.setdefault('objective', 'makespan' if mission['fleet'] > 1 else 'nonworking')
    return mission


def from_scenario(scenario: dict) -> dict:
    from ..agri.mission import compile_mission
    from ..agri.validate import field_operation
    inst = scenario['instance']
    mission = inst.get('mission') or (scenario.get('provenance') or {}).get('params', {}).get('mission')
    if not isinstance(mission, dict):
        raise ValueError('田间地图缺少作业定义，请从作业设计重新生成地图')
    normalized = normalize({k: v for k, v in mission.items() if k in MISSION_SCHEMA['properties'] and v is not None})
    compiled = compile_mission(normalized)
    if compiled['instance'] is None or compiled['blocked']:
        raise ValueError('田间地图的作业定义未通过农机适配检查，请在作业设计中修正')
    expected, actual = field_operation(compiled['instance']), field_operation(inst)
    changed = [key for key in expected if key != 'mission' and actual.get(key) != expected[key]]
    if changed:
        raise ValueError('田间地图实例与作业定义不一致（' + '、'.join(changed) + '），请从作业设计重新生成地图')
    return normalized


def summary(mission: dict) -> str:
    labels = []
    for key, group in [('environment', 'environments'), ('crop', 'crops'), ('operation', 'operations'),
                       ('platform', 'platforms'), ('implement', 'implements')]:
        if mission.get(key):
            labels.append(catalog.by_id(group, mission[key])['name'])
    f = mission['field']
    labels.append(f.get('field_id') or (f"{len(f['ring'])} 顶点边界" if f.get('ring') else f"{f['width_m']:g} × {f['height_m']:g} m"))
    labels.append(f"{mission['fleet']} 台机组")
    return ' · '.join(labels)


def details(mission: dict) -> list[dict]:
    field = mission['field']
    rows = [('优化目标', '最晚完成时间' if mission['objective'] == 'makespan' else '非作业里程'),
            ('补给站', {'sw': '西南', 'se': '东南', 'nw': '西北', 'ne': '东北', 's': '南侧', 'n': '北侧'}[mission['station']]),
            ('地头宽度', f"{mission['headland_m']:g} m" if 'headland_m' in mission else '按机组转弯条件计算'),
            ('作业方向', f"{mission['angle_deg']:g}°" if 'angle_deg' in mission else '沿田块长轴'),
            ('田块形状', {'rect': '矩形', 'trapezoid': '梯形', 'l_shape': 'L 形', 'pentagon': '五边形'}.get(
                field.get('shape'), '真实田块' if field.get('field_id') else '显式边界'))]
    if field.get('ring'):
        rows.append(('边界坐标（m）', json.dumps(field['ring'], ensure_ascii=False)))
    if field.get('obstacles'):
        rows.append(('障碍边界（m）', json.dumps(field['obstacles'], ensure_ascii=False)))
    names = {'work_width_m': '作业幅宽（m）', 'min_turn_radius_m': '最小转弯半径（m）', 'speed_kmh': '作业速度（km/h）',
             'capacity': '容量', 'rate_per_ha': '单位面积用量（/ha）', 'endurance_h': '续航（h）'}
    rows += [(names[k], str(v)) for k, v in mission.get('overrides', {}).items()]
    return [{'label': label, 'text': text} for label, text in rows]


def check_shared_fields(contract: dict, mission: dict):
    from shapely.geometry import Polygon
    from ..agri import rig as rigs
    from ..agri.mission import _field
    ring, holes = _field(mission)
    geom = Polygon(ring, holes)
    x0, y0, x1, y1 = geom.bounds
    field = mission['field']
    shape_map = {'rect': 'rect', 'l_shape': 'L', 'trapezoid': 'irregular', 'pentagon': 'irregular'}
    derived = rigs.derive(catalog.by_id('platforms', mission['platform']), catalog.by_id('implements', mission['implement']),
                          catalog.by_id('operations', mission['operation']), mission.get('overrides') or {})
    facts = {'vehicle_count': mission['fleet'], 'field_size_m': [round(x1 - x0, 3), round(y1 - y0, 3)],
             'field_area_m2': round(geom.area, 3), 'real_field_id': field.get('field_id'),
             'field_shape': shape_map.get(field.get('shape', 'rect')) if not field.get('field_id') and not field.get('ring') else None,
             'depot_location': mission['station'], 'travel_mode': 'rows', 'trip_mode': 'multi',
             'objective': 'makespan' if mission['objective'] == 'makespan' else 'total_distance',
             'vehicle_speed_mps': round(derived['speed_work_mps'], 4)}
    exempt = {'field_mission', 'simulate', 'seed', 'time_limit_s', 'compare_count', 'task_kind'}
    conflicts = []
    for key, item in contract['fields'].items():
        if key in exempt:
            continue
        expected, actual = facts.get(key), item['value']
        if key == 'field_area_m2' and isinstance(expected, float) and abs(actual - expected) <= max(1.0, 0.005 * expected):
            continue
        if key == 'vehicle_speed_mps' and isinstance(expected, float) and abs(actual - expected) <= 0.02:
            continue
        if actual != expected:
            conflicts.append(key)
    if conflicts:
        raise ValueError('田间行作任务与通用字段不一致或不支持这些字段，请修改合同：' + '、'.join(conflicts))
