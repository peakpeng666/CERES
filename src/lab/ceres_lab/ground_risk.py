"""Ground-risk-aware planning: declared known-map ground information enters the
fleet solver's edge costs and the executed speed profile.

Semantics (versioned, one policy this round):
- The instance may declare ``ground``: axis-aligned zones with a friction
  coefficient μ and optional label, plus ``mu_reference`` (default 1.0) and a
  ``knowledge`` tag. This is *the map the planner is allowed to read* — a declared
  known-map condition, not a prediction of execution.
- With ``ground_policy='risk_adjust'`` a visible edge's solver weight becomes the
  *risk-adjusted length*:  geometric length + w times the integral of max(0, 1 - mu_worst/mu_ref).
  Units stay metres (equivalent metres); the geometric length is still computed and
  reported separately. This is a declared cost preference — NOT a failure
  probability and NOT an energy value.
- ``speed_policy`` maps low-friction zones to a speed cap for execution
  (v_limit while inside). Caps are engineering assumptions from friction margins
  for acceleration/turning; they are enforced on the controller, and the executed
  trajectory is checked against them.
- The world generator consumes the *same* zone rectangles as actual physics
  friction partitions, and the frozen plan records both the planner map and the
  world map so any deliberate mismatch is auditable.
"""
from __future__ import annotations

import math

from shapely.geometry import LineString

from .ground_map import SCHEMA, SPEED_POLICY, digest, normalize_ground, number


def exposure_line(seg, zones, footprint_aware=True, mu_reference=1.0):
    """Integral of max(0, mu_ref-mu) over the worst overlapping footprint zone.

    Physical zones do not overlap, but their conservative square expansions can.
    Their overlap uses the lowest mu, never a double-counted penalty. Physical
    centre-line exposure is a union length, distinct from footprint exposure.
    """
    from shapely.geometry import box
    from shapely.ops import unary_union
    line = LineString(seg)
    inside = line.intersection(unary_union([box(*z['rect']) for z in zones])).length
    covered, deficit = LineString(), 0.
    for zone in sorted(zones, key=lambda z: z['mu']):
        rect = zone.get('risk_rect', zone['rect']) if footprint_aware else zone['rect']
        part = line.intersection(box(*rect))
        deficit += part.difference(covered).length * max(0., mu_reference-zone['mu'])
        covered = covered.union(part)
    return inside, deficit


def risk_factor(seg, zones, mu_reference):
    length = math.dist(*seg)
    if length < 1e-9:
        return 0.
    return exposure_line(seg, zones, mu_reference=mu_reference)[1] / length / mu_reference


def speed_profile(path, zones, speed_policy, base_speed):
    """Arc-length intervals with a speed cap while the path is inside low-μ zones.

    Returns [[s0, s1, v_limit], ...] merged, ascending, v_limit <= base_speed.
    """
    threshold = float(speed_policy.get('mu_threshold', SPEED_POLICY['mu_threshold']))
    v_low = min(float(speed_policy.get('v_limit_mps', SPEED_POLICY['v_limit_mps'])), float(base_speed))
    low = [z for z in zones if z['mu'] < threshold]
    if not low or v_low >= base_speed - 1e-9:
        return []
    caps = []
    s = 0.0
    for a, b in zip(path, path[1:]):
        seg_len = math.dist(a, b)
        if seg_len < 1e-9:
            continue
        _inside, deficit = exposure_line([a, b], low)
        # a segment touching low ground at all is capped (conservative at boundaries)
        if deficit > 0 or _inside > 0:
            caps.append([round(s, 3), round(s + seg_len, 3), round(v_low, 3)])
        s += seg_len
    merged = []
    for c in caps:
        if merged and abs(merged[-1][1] - c[0]) < 1e-6 and abs(merged[-1][2] - c[2]) < 1e-9:
            merged[-1][1] = c[1]
        else:
            merged.append(c)
    return merged


def route_ground_stats(path, zones, mu_reference, weight=1.0):
    """Geometric exposure and average friction deficit of a finished polyline."""
    inside = deficit = total = 0.0
    for a, b in zip(path, path[1:]):
        seg = math.dist(a, b)
        total += seg
        i, d = exposure_line([a, b], zones, mu_reference=mu_reference)
        inside += i
        deficit += d
    return {'geo_length_m': round(total, 3),
            'in_zone_length_m': round(inside, 3),
            'mean_mu_deficit': round(deficit / total, 4) if total > 1e-6 else 0.0,
            'risk_adjusted_length_m': round(total + weight * deficit / mu_reference, 3)}


def resolve_policy(inst, params):
    """Validate ground-policy solver params against the instance; returns
    (policy, weight, normalized_ground_or_None). Raises ValueError with a clear
    reason when the policy is unsupported for this instance."""
    policy = str(params.get('ground_policy', 'ignore'))
    if policy not in ('ignore', 'risk_adjust'):
        raise ValueError('ground_policy 仅支持 ignore、risk_adjust')
    weight = number(params.get('ground_risk_weight', 1.0), 'ground_risk_weight', 0, 10)
    ground = normalize_ground(inst) if inst.get('ground') else None
    if policy == 'risk_adjust':
        if ground is None:
            raise ValueError('ground_policy=risk_adjust 需要 instance 声明 ground（已知地面图）')
        if str(inst.get('objective') or 'total_distance') == 'makespan':
            raise ValueError('ground_policy=risk_adjust 本轮仅支持 total_distance 目标；当量米未定义时间口径，makespan 请用 ignore')
        if any(c.get('min_turn_radius_m', 0) > 0 for c in inst['vehicles'].get('fleet', [])):
            raise ValueError('ground_policy=risk_adjust 暂不支持曲率约束车队：曲线实现未参与风险优化')
        if inst['vehicles'].get('travel') == 'rows':
            raise ValueError('ground_policy=risk_adjust 暂只支持自由通行；沿行间模式的边为固定通道，不支持绕行风险调整')
    return policy, weight, ground


def route_metadata(ground, policy_active, weight):
    return {'schema': SCHEMA, 'policy': 'risk_adjust' if policy_active else 'ignore',
            'weight': weight, 'mu_reference': ground['mu_reference'], 'knowledge': ground['knowledge'],
            'ground_sha256': digest(ground),
            'cost_scope': 'declared weighted exposure, counterfactual score when policy=ignore'}


def check_route_ground(route, inst, pts, errors, base_speed, params=None):
    """Recompute every ground field; absence is an error on a ground-map instance.

    When available, frozen solver params bind policy and weight to the original
    request. Legacy records without this contract must be re-solved for execution.
    """
    if inst.get('ground') is None:
        if any(route.get(k) for k in ('ground_risk', 'ground_stats', 'speed_profile')):
            errors.append('路线声明地面风险但 instance 没有 ground')
        return
    try:
        meta = route.get('ground_risk')
        if not isinstance(meta, dict) or meta.get('schema') != SCHEMA:
            raise ValueError('缺少当前版本 ground_risk；历史路线须重求解')
        candidate = {'ground_policy': meta.get('policy'), 'ground_risk_weight': meta.get('weight')}
        policy, weight, ground = resolve_policy(inst, candidate)
        if params is not None and (policy, weight) != resolve_policy(inst, params)[:2]:
            raise ValueError('策略或权重与原求解请求不一致')
        stats = route_ground_stats(pts, ground['zones'], ground['mu_reference'], weight)
        expected = {'geo_length_m': stats['geo_length_m'], 'risk_cost': stats['risk_adjusted_length_m'],
                    'ground_stats': stats, 'ground_risk': route_metadata(ground, policy == 'risk_adjust', weight),
                    'speed_profile': (speed_profile(pts, ground['zones'], ground['speed_policy'], base_speed)
                                      if policy == 'risk_adjust' else [])}
        for key, value in expected.items():
            if route.get(key) != value:
                raise ValueError(f'{key} 与冻结输入/独立复算不一致')
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(f'路线的地面风险口径无效：{exc}')


def attach_route_ground(route, pts, ground, policy_active, base_speed, weight=1.0):
    stats = route_ground_stats(pts, ground['zones'], ground['mu_reference'], weight)
    route.update(geo_length_m=stats['geo_length_m'], risk_cost=stats['risk_adjusted_length_m'],
                 ground_stats=stats, ground_risk=route_metadata(ground, policy_active, weight),
                 speed_profile=(speed_profile(pts, ground['zones'], ground['speed_policy'], base_speed)
                                if policy_active else []))
    return route
