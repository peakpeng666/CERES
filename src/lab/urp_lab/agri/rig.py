"""CERES 农机机组装配与运动学参数导出 (Rig)

将底盘平台与作业机具挂接，导出规划器所需的最小转弯半径、作业幅宽、地速及补给参数。
保留每项参数的推导规则与依据来源。
"""
from __future__ import annotations

import math

from . import catalog as cat

KMH = 1 / 3.6
TURN_SPEED_RATIO = .75       # 地头转向降速比 (转弯 15 km/h vs 作业 20 km/h)
TRANSFER_CAP_KMH = 15.0      # 地头转场空驶限速 (km/h)
PIVOT_RATE = .8              # 原地自转角速度极限 (rad/s)
FILL_FIXED_S = 240.0         # 进站停靠对接基础固定耗时 (s)
FILL_RATE_PER_MIN = 200.0    # 药液/物料加注流率 (L/min)
TRAILED_MARGIN = 1.1         # 牵引机具转弯内切安全余量系数
OVERHEAD_CATEGORIES = {'植保', '施肥'}   # 顶置式作业幅宽 (喷杆/撒肥机高于作物冠层)


def _derived(value, unit, rule, sources):
    return {'v': value, 'u': unit, 'rule': rule, 'sources': sorted({s for s in sources if s})}


def _src(node):
    return node.get('s') if isinstance(node, dict) else None


def turn_depth(radius: float, pitch: float) -> float:
    """Headland depth a forward turn needs between parallel tracks `pitch` apart (Bochtis & Vougioukas 2008 geometry)."""
    if radius <= 0:
        return 0.0
    if pitch >= 2 * radius:
        return radius
    return radius + math.sqrt(max(0.0, 4 * radius ** 2 - (pitch / 2 + radius) ** 2))


def derive(platform: dict, implement: dict, operation: dict, overrides: dict | None = None) -> dict:
    o = {k: v for k, v in (overrides or {}).items() if v is not None}
    v = cat.value
    width = float(o.get('work_width_m', v(implement['width_m'])))
    p_len, i_len = v(platform['length_m']), v(implement['length_m'], 0)
    body_w = v(platform['width_m'])
    overhead = implement.get('category') in OVERHEAD_CATEGORIES or implement.get('hitch') == 'integrated'
    clear_w = body_w if overhead or implement['id'] == 'inspect_camera' else max(body_w, width)

    r_platform = v(platform['min_turn_radius_m'], 0)
    r_impl = v(implement.get('turn_radius_m'), 0)
    trailed = bool(implement.get('trailed'))
    radius = max(r_platform, r_impl * TRAILED_MARGIN if trailed else 0)
    radius = float(o.get('min_turn_radius_m', radius))
    pivot = bool(platform.get('pivot')) and not trailed and radius == 0
    reverse = bool(platform.get('reverse')) and not trailed

    op_speed = implement.get('speed_kmh') or operation.get('speed')
    limit = v(platform['speed_work_max_kmh'])
    speed_kmh = min(v(op_speed), limit)
    speed_kmh = float(o.get('speed_kmh', speed_kmh))
    efficiency = implement.get('efficiency') or operation.get('efficiency')
    transfer_kmh = min(v(platform['speed_transport_kmh']), TRANSFER_CAP_KMH)
    turn_kmh = min(speed_kmh * TURN_SPEED_RATIO, transfer_kmh)

    endurance_h = o.get('endurance_h', v(platform.get('endurance_h')))
    tank = implement.get('tank')
    kind = operation.get('resource', 'none') if tank else 'none'
    capacity = float(o.get('capacity', v(tank))) if tank else None
    rate = None
    if kind != 'none':
        rate = float(o['rate_per_ha']) if 'rate_per_ha' in o else operation.get('default_rate')
    mass = v(platform['mass_kg']) + v(implement.get('mass_kg'), 0)
    wheels = platform.get('wheels') or 0
    ground = platform.get('ground')
    wheel_load_kg = mass / wheels if wheels and ground == 'tyre' else None
    track = v(platform.get('track_m'), 0)
    return {
        'schema': 'urp.rig/1', 'name': f"{platform['name']} + {implement['name']}", 'platform': platform['id'], 'implement': implement['id'],
        'class': platform['class'], 'tier': platform['tier'], 'adapter': platform.get('adapter'),
        'work_width_m': width, 'body_width_m': body_w, 'clearance_width_m': clear_w, 'length_m': p_len + i_len,
        'track_m': track, 'ground': ground, 'tread_width_m': .6 if ground == 'track' else .4 if mass > 1000 else .1,
        'min_turn_radius_m': radius, 'pivot': pivot, 'reverse': reverse, 'trailed': trailed,
        'speed_work_mps': speed_kmh * KMH, 'speed_turn_mps': turn_kmh * KMH, 'speed_transfer_mps': transfer_kmh * KMH, 'pivot_rate_radps': PIVOT_RATE,
        'efficiency_typical': v(efficiency), 'efficiency_range': (efficiency or {}).get('r'),
        'resource': {'kind': kind, 'name': operation.get('resource_name'), 'unit': (tank or {}).get('u') or operation.get('unit'), 'capacity': capacity, 'rate_per_ha': rate},
        'endurance_s': float(endurance_h) * 3600 if endurance_h else None,
        'mass_kg': mass, 'wheel_load_kg': wheel_load_kg,
        'fill': {'fixed_s': FILL_FIXED_S, 'rate_per_min': FILL_RATE_PER_MIN},
        'derivation': {
            'min_turn_radius_m': _derived(radius, 'm', '牵引机具的跟随半径 × 1.1 与平台半径取大' if trailed else '平台最小转弯半径',
                                          [_src(platform['min_turn_radius_m']), _src(implement.get('turn_radius_m')), 'assumption' if trailed else None]),
            'speed_work_mps': _derived(speed_kmh * KMH, 'm/s', '作业典型速度与平台速度上限取小', [_src(op_speed), _src(platform['speed_work_max_kmh'])]),
            'speed_turn_mps': _derived(turn_kmh * KMH, 'm/s', '作业速度 × 0.75，且不超过转场上限', ['hossain2026']),
            'speed_transfer_mps': _derived(transfer_kmh * KMH, 'm/s', '平台行驶速度与田内 15 km/h 取小', [_src(platform['speed_transport_kmh']), 'assumption']),
            'length_m': _derived(p_len + i_len, 'm', '平台长度 + 机具长度', [_src(platform['length_m']), _src(implement['length_m'])]),
            'clearance_width_m': _derived(clear_w, 'm', '喷杆与撒肥幅宽从作物上方越过，只计车体；其余取车体与机具的最大宽度', [_src(platform['width_m']), _src(implement['width_m'])]),
            'efficiency': _derived(v(efficiency), '', 'ASAE D497 典型田间效率', [_src(efficiency)]),
        },
    }


def headland_for(rig: dict, pitch: float) -> float:
    """Depth that lets the rig turn into the next adjacent track with its body inside the field."""
    depth = turn_depth(rig['min_turn_radius_m'], pitch)
    overhang = rig['length_m'] / 2
    return round(max(depth + overhang, rig['clearance_width_m'] / 2 + .5), 1)


def demand_per_m(rig: dict, pitch: float | None = None) -> float:
    res = rig['resource']
    if res['kind'] == 'none' or not res['rate_per_ha']:
        return 0.0
    return res['rate_per_ha'] * (pitch or rig['work_width_m']) / 10000


def service_s(rig: dict) -> float:
    res = rig['resource']
    if res['kind'] == 'none' or not res['capacity']:
        return 0.0
    return rig['fill']['fixed_s'] + res['capacity'] / rig['fill']['rate_per_min'] * 60


def draft_kw(implement: dict, speed_kmh: float, width_m: float, row_spacing: float | None, soil: int = 1) -> float | None:
    """ASAE D497 eq.: D = F_i (A + B S + C S²) W T, power = D S / 3.6."""
    d = implement.get('draft')
    if not d:
        return None
    units = width_m if d['unit'] == 'm' else max(1, round(width_m / (row_spacing or .75)))
    force = d['F'][soil] * (d['A'] + d['B'] * speed_kmh + d['C'] * speed_kmh ** 2) * units * d['depth_cm']
    return force * speed_kmh / 3.6 / 1000


def pto_kw(implement: dict, width_m: float) -> float | None:
    p = implement.get('pto')
    return p['a'] + p['b'] * width_m if p else None
