"""Validated, versioned known-ground input. Derived fields are never trusted."""
import hashlib
import json
import math

from shapely.geometry import Polygon, box

SCHEMA = 'urp.ground-risk/2'
SPEED_POLICY = {'mu_threshold': 0.5, 'v_limit_mps': 0.35,
                'note': '工程假设：μ<0.5 的分区按低附着限速 0.35 m/s 通过（加减速/转向摩擦裕量），未标定'}
DECLARATION = '规划器可读的已知地面图（声明条件）；风险成本为当量米，不是失败概率'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def number(value, label, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f'{label} 须为 {lo}–{hi} 的有限数值')
    return float(value)


def normalize_ground(inst):
    ground = inst.get('ground')
    if ground is None:
        return None
    if not isinstance(ground, dict):
        raise ValueError('ground 须为对象')
    schema = ground.get('schema')
    if schema not in (None, SCHEMA, 'urp.ground-risk/1'):
        raise ValueError('未知 ground.schema')
    derived = {'schema', 'speed_policy', 'declared_as', 'risk_margin_m'} if schema else set()
    if set(ground) - {'zones', 'mu_reference', 'knowledge'} - derived:
        raise ValueError('ground 含未知字段')
    source = ground.get('zones')
    if not isinstance(source, list) or not 1 <= len(source) <= 8:
        raise ValueError('ground.zones 须为 1–8 个分区，与物理世界能力一致')
    reference = number(ground.get('mu_reference', 1.), 'ground.mu_reference', .05, 2.)
    radius = number(inst.get('robot_radius', .5), 'robot_radius', .001, 1000)
    radius = max([radius] + [float(c['robot_radius']) for c in inst.get('vehicles', {}).get('fleet', [])])
    boundary = Polygon(inst['boundary'])
    depots = inst.get('depots') or [inst['depot']]
    zones, shapes = [], []
    for i, zone in enumerate(source):
        if not isinstance(zone, dict) or set(zone) - {'rect', 'mu', 'label'} - ({'risk_rect'} if schema else set()):
            raise ValueError(f'ground.zones[{i}] 含未知字段')
        rect = zone.get('rect')
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            raise ValueError('ground.rect 须为 [x0,y0,x1,y1]')
        rect = [number(c, 'ground.rect', -1e9, 1e9) for c in rect]
        x0, y0, x1, y1 = rect
        if x1-x0 < .5 or y1-y0 < .5:
            raise ValueError('ground 分区每边须至少 0.5 m')
        shape = box(*rect)
        if not boundary.covers(shape):
            raise ValueError('ground 分区超出实际多边形边界')
        if any(shape.intersection(other).area > 1e-12 for other in shapes):
            raise ValueError('ground 分区不可重叠')
        if any(shape.intersection(box(x-2.5, y-2.5, x+2.5, y+2.5)).area > 1e-12 for x, y in depots):
            raise ValueError('ground 分区须避开所有车库周围 2.5 m')
        mu = number(zone.get('mu'), 'ground.mu', .03, 1.)
        label = zone.get('label', f'ground_{i+1}')
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise ValueError('ground.label 须为 1–200 字符的非空文本')
        margin = .5 if schema == 'urp.ground-risk/1' else radius
        expected = [x0-margin, y0-margin, x1+margin, y1+margin]
        if 'risk_rect' in zone and zone['risk_rect'] != expected:
            raise ValueError('ground.risk_rect 与声明足迹重新计算不一致')
        zones.append({'rect': rect, 'risk_rect': [x0-radius, y0-radius, x1+radius, y1+radius],
                      'mu': mu, 'label': label})
        shapes.append(shape)
    knowledge = ground.get('knowledge', 'known_map_v1')
    if not isinstance(knowledge, str) or not knowledge.strip() or len(knowledge) > 200:
        raise ValueError('ground.knowledge 须为 1–200 字符的非空文本')
    for key, expected in [('speed_policy', SPEED_POLICY), ('declared_as', DECLARATION), ('risk_margin_m', radius)]:
        if key in ground and ground[key] != expected:
            raise ValueError(f'ground.{key} 与当前规范不一致')
    return {'schema': SCHEMA, 'zones': zones, 'mu_reference': reference, 'knowledge': knowledge,
            'risk_margin_m': radius, 'speed_policy': dict(SPEED_POLICY), 'declared_as': DECLARATION}
