"""Shape of a field-operation instance. What the plan must satisfy is judged later, by check.py."""
from __future__ import annotations

import copy

from shapely.geometry import Polygon

from ..scenarios.values import _int, _num, _xy


def field_operation(inst: dict) -> dict:
    """Work lines, one rig type and refill stations. The mission that produced them stays in the instance."""
    out = copy.deepcopy(inst)
    if inst.get('schema') != 'urp.field-operation/1':
        raise ValueError('field_operation 实例须声明 schema urp.field-operation/1')
    field = [_xy(p, f'field[{i}]') for i, p in enumerate(inst.get('field') or [])]
    if len(field) < 3 or not Polygon(field).is_valid or Polygon(field).area <= 0:
        raise ValueError('田块边界无效')
    obstacles = [[_xy(p, f'obstacles[{k}][{i}]') for i, p in enumerate(ob)] for k, ob in enumerate(inst.get('obstacles') or [])]
    head = _num(inst.get('headland_m'), 'headland_m')
    pitch = _num(inst.get('pitch_m'), 'pitch_m', True)
    if head < 0:
        raise ValueError('headland_m 不能为负')
    rig = inst.get('rig')
    if not isinstance(rig, dict):
        raise ValueError('缺少机组参数 rig')
    for key in ('work_width_m', 'clearance_width_m', 'speed_work_mps', 'speed_turn_mps', 'speed_transfer_mps'):
        _num(rig.get(key), f'rig.{key}', True)
    _num(rig.get('min_turn_radius_m', 0), 'rig.min_turn_radius_m')
    lines = inst.get('lines')
    if not isinstance(lines, list) or not lines:
        raise ValueError('至少需要一条作业行')
    if len(lines) > 400:
        raise ValueError('作业行超过 400 条')
    ids = set()
    for i, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ValueError(f'lines[{i}] 应为对象')
        _xy(line.get('p1'), f'lines[{i}].p1')
        _xy(line.get('p2'), f'lines[{i}].p2')
        if line.get('id') in ids:
            raise ValueError(f"作业行编号重复：{line.get('id')}")
        ids.add(line.get('id'))
        if _num(line.get('demand', 0), f'lines[{i}].demand') < 0:
            raise ValueError(f'lines[{i}].demand 不能为负')
    res = rig.get('resource') or {}
    if res.get('kind', 'none') not in ('none', 'consume', 'collect'):
        raise ValueError('resource.kind 只能是 none / consume / collect')
    if res.get('kind', 'none') != 'none':
        cap = _num(res.get('capacity'), 'resource.capacity', True)
        worst = max(float(x.get('demand', 0)) for x in lines)
        if worst > cap:
            raise ValueError(f"单行需求 {worst:g} 超过容量 {cap:g}；行内补给还未建模")
    stations = inst.get('stations') or []
    if not stations:
        raise ValueError('至少需要一个补给站或出入口')
    for i, st in enumerate(stations):
        _xy((st or {}).get('xy'), f'stations[{i}].xy')
    machines = _int(inst.get('machines', 1), 'machines', 1)
    if machines > 12:
        raise ValueError('机组数超过 12')
    if inst.get('objective', 'nonworking') not in ('nonworking', 'makespan'):
        raise ValueError('objective 须为 nonworking 或 makespan')
    out.update(field=field, obstacles=obstacles, headland_m=head, pitch_m=pitch, depot=_xy(inst.get('depot'), 'depot'), machines=machines,
               objective=inst.get('objective', 'nonworking'), units='m')
    return out
