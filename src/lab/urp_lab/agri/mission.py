# CERES 农艺作业任务定义与求解实例编译 (Mission → Instance)
# 挂载作物种类、作业工艺、机具平台、作业行垄向与地头转弯空间
from __future__ import annotations

import copy
import math

from shapely.geometry import Point, Polygon

from . import assess as checks
from . import catalog as cat
from . import rig as rigs
from . import worklines

SCHEMA = 'urp.field-operation/1'
SHAPES = ('rect', 'trapezoid', 'l_shape', 'pentagon')


def shape_ring(shape: str, width: float, height: float) -> list[list[float]]:
    w, h = width, height
    return {
        'rect': [[0, 0], [w, 0], [w, h], [0, h]],
        'trapezoid': [[0, 0], [w, 0], [w * .85, h], [w * .1, h]],
        'l_shape': [[0, 0], [w, 0], [w, h * .55], [w * .55, h * .55], [w * .55, h], [0, h]],
        'pentagon': [[0, 0], [w, h * .08], [w * .92, h * .78], [w * .4, h], [0, h * .7]],
    }[shape]


def long_axis(ring) -> float:
    """Direction of the field's long side: where a farmer would lay the passes."""
    box = list(Polygon(ring).minimum_rotated_rectangle.exterior.coords)
    edges = sorted(((math.dist(a, b), math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))) for a, b in zip(box, box[1:])), reverse=True)
    return round(edges[0][1] % 180, 2)


def _field(m: dict) -> tuple[list, list]:
    f = m.get('field') or {}
    if f.get('field_id'):
        from ..evolve.instances import split_of
        from ..library import fields
        if split_of(f['field_id']) != 'dev':
            raise ValueError('这块田属于封存验证集，不用于作业规划')
        inst = fields.scenario(f['field_id'])['instance']
        return inst['field'], inst.get('obstacles') or []
    if f.get('ring'):
        return [list(map(float, p)) for p in f['ring']], [[list(map(float, p)) for p in o] for o in f.get('obstacles') or []]
    shape = f.get('shape', 'rect')
    if shape not in SHAPES:
        raise ValueError(f'未知田块形状 {shape}')
    width, height = float(f.get('width_m', 200)), float(f.get('height_m', 120))
    if not (5 <= width <= 5000 and 5 <= height <= 5000):
        raise ValueError('田块长宽须为 5–5000 m')
    return shape_ring(shape, width, height), [[list(map(float, p)) for p in o] for o in f.get('obstacles') or []]


def _pitch(env: dict, crop: dict | None, rig: dict, operation: dict) -> tuple[float, str]:
    """Spacing of the work lines: orchard alleys are fixed by the planting; elsewhere the implement sets it."""
    spacing = cat.value(crop.get('row_spacing_m')) if crop and crop.get('row_spacing_m') else None
    width = rig['work_width_m']
    if env['structure'] == 'alley' and spacing:
        return spacing, 'alley'
    if crop and crop.get('stand') == 'row' and spacing and operation.get('crop_protected'):
        return max(1, math.floor(width / spacing + 1e-9)) * spacing, 'rows'
    return width, 'swath'


def _station_xy(field, obstacles, where: str, clear: float, headland: float) -> list[float]:
    """Mid-headland, so that a rig with a turning radius can drive off in either direction."""
    inset = max(clear + .3, headland / 2)
    poly = Polygon(field).buffer(-inset, join_style=2)
    for o in obstacles:
        poly = poly.difference(Polygon(o).buffer(inset, join_style=2))
    x0, y0, x1, y1 = Polygon(field).bounds
    corner = {'sw': (x0, y0), 'se': (x1, y0), 'ne': (x1, y1), 'nw': (x0, y1), 's': ((x0 + x1) / 2, y0), 'n': ((x0 + x1) / 2, y1)}.get(where, (x0, y0))
    if poly.is_empty:
        raise ValueError('田块太小，放不下补给站')
    from shapely.ops import nearest_points
    p = nearest_points(poly, Point(corner))[0]
    return [round(p.x, 3), round(p.y, 3)]


def compile_mission(mission: dict) -> dict:
    """Returns the instance, the derived rig, the compatibility checks and a planning estimate."""
    m = copy.deepcopy(mission)
    env = cat.by_id('environments', m['environment'])
    crop = cat.by_id('crops', m['crop']) if m.get('crop') else None
    operation = cat.by_id('operations', m['operation'])
    platform = cat.by_id('platforms', m['platform'])
    implement = cat.by_id('implements', m['implement'])
    if operation.get('family') == 'line' and 'speed' not in operation:
        raise ValueError(f"{operation['name']} 缺少速度参数")
    if operation.get('family') != 'line':
        rig = {'min_turn_radius_m': 0, 'work_width_m': 1, 'clearance_width_m': 1, 'resource': {'kind': 'none'}, 'speed_work_mps': 1, 'wheel_load_kg': None,
               'endurance_s': None, 'track_m': 0, 'length_m': 1}
        return {'instance': None, 'rig': None, 'checks': checks.assess(env, crop, operation, platform, implement, rig, pitch=1, headland_m=9), 'estimate': None, 'lines': []}
    overrides = m.get('overrides') or {}
    if overrides.get('rate_per_ha') is None and crop and operation.get('resource') == 'collect':
        overrides = {**overrides, 'rate_per_ha': cat.value(crop.get('yield_t_ha'), 0) * 1000 or None}
    rig = rigs.derive(platform, implement, operation, overrides)
    pitch, pattern = _pitch(env, crop, rig, operation)
    field, obstacles = _field(m)
    recommended = rigs.headland_for(rig, pitch)
    headland = float(m['headland_m']) if m.get('headland_m') is not None else recommended
    angle = long_axis(field) if m.get('angle_deg') is None else float(m['angle_deg'])
    clear = rig['clearance_width_m'] / 2 + .1
    lines = worklines.generate(field, obstacles, headland, angle, pitch)
    lines = worklines.trim_for_turning(lines, field, obstacles, clear, rig['min_turn_radius_m'])
    if not lines:
        raise ValueError('这块田放不下这台机组的转弯；换更小的机组或减小地头')
    per_m = rigs.demand_per_m(rig, pitch)
    for line in lines:
        line['demand'] = round(line['length_m'] * per_m, 4)
    longest = max(x['length_m'] for x in lines)
    result = checks.assess(env, crop, operation, platform, implement, rig, pitch=pitch, headland_m=headland, line_max_m=longest)
    station = {'id': 'S1', 'xy': _station_xy(field, obstacles, m.get('station', 'sw'), clear, headland), 'service_s': round(rigs.service_s(rig), 1)}
    fleet = int(m.get('fleet') or 1)
    if not 1 <= fleet <= 12:
        raise ValueError('机组数须为 1–12')
    objective = m.get('objective') or ('makespan' if fleet > 1 else 'nonworking')
    if objective not in ('nonworking', 'makespan'):
        raise ValueError('优化目标须为 nonworking 或 makespan')
    instance = {
        'schema': SCHEMA, 'field': field, 'obstacles': obstacles, 'headland_m': headland, 'angle_deg': angle,
        'pitch_m': pitch, 'pattern': pattern, 'lines': lines, 'stations': [station], 'depot': station['xy'], 'machines': fleet,
        'rig': {k: v for k, v in rig.items() if k != 'derivation'}, 'crop_protected': bool(operation.get('crop_protected')) and platform['class'] != 'uav',
        'overfly': platform['class'] == 'uav', 'objective': objective, 'coverage_required': .9,
        'mission': {**m, 'catalog_version': cat.load()['version'], 'recommended_headland_m': recommended},
    }
    return {'instance': instance, 'rig': rig, 'checks': result, 'estimate': estimate(instance), 'lines': lines, 'pitch_m': pitch, 'pattern': pattern,
            'recommended_headland_m': recommended, 'blocked': checks.blocked(result)}


def estimate(inst: dict) -> dict:
    """Before solving: what the field and the rig imply. Field capacity follows ASAE EP496 (S·W·E/10)."""
    rig = inst['rig']
    total_m = sum(x['length_m'] for x in inst['lines'])
    field = Polygon(inst['field'], inst.get('obstacles') or [])
    work_ha = total_m * inst['pitch_m'] / 10000
    v = rig['speed_work_mps']
    res = rig['resource']
    demand = sum(x.get('demand', 0) for x in inst['lines'])
    fills = math.ceil(demand / res['capacity']) if res['kind'] != 'none' and res['capacity'] else None
    rows_per_fill = None
    if fills and demand:
        rows_per_fill = round(res['capacity'] / (demand / len(inst['lines'])), 1)
    theoretical_ha_h = rig['work_width_m'] * v * 3.6 / 10
    eff = rig.get('efficiency_typical') or 1
    return {'field_ha': round(field.area / 10000, 3), 'work_ha': round(work_ha, 3), 'lines': len(inst['lines']), 'line_total_m': round(total_m, 1),
            'pitch_m': inst['pitch_m'], 'theoretical_ha_h': round(theoretical_ha_h, 3), 'effective_ha_h': round(theoretical_ha_h * eff, 3),
            'hours_at_typical_efficiency': round(work_ha / (theoretical_ha_h * eff) / inst['machines'], 2) if theoretical_ha_h else None,
            'demand_total': round(demand, 1), 'fills_min': fills, 'rows_per_fill': rows_per_fill, 'unit': res.get('unit'),
            'turn_ratio': round(2 * rig['min_turn_radius_m'] / inst['pitch_m'], 2),
            'skip_rows_needed': max(0, math.ceil(2 * rig['min_turn_radius_m'] / inst['pitch_m']) - 1)}
