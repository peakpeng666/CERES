"""Known-map/world binding and independent truth-speed acceptance."""
import copy
import math

from shapely.geometry import LineString, box
from shapely.ops import unary_union

from .ground_map import SCHEMA, digest, normalize_ground
from .ground_risk import speed_profile

SPEED_TOLERANCE_MPS = .06  # engineering numerical/control allowance, not a physical guarantee


def resolve(body, inst, routes):
    """Localization-only changes inherit the declared ground. Explicit ground wins."""
    from .field_nonideal import validate_execution
    ground = normalize_ground(inst)
    inherit = ground is not None and (body is None or isinstance(body, dict) and
                                     not {'ground_preset', 'ground_zones'}.intersection(body))
    request = copy.deepcopy(body)
    if inherit:
        request = request or {}
        request['ground_zones'] = [{'rect': z['rect'], 'mu': z['mu']} for z in ground['zones']]
    execution = validate_execution(request, inst, routes)
    if execution:
        execution['ground']['origin'] = 'instance_ground' if inherit else 'execution_body'
        execution['planner_knowledge'] = ('规划器可读取 instance.ground；控制器执行冻结限速；世界覆盖不反馈给求解器'
                                           if ground else '规划器没有地面图；控制器仅使用冻结路线与所选定位输入')
    return execution


def vehicle_fields(inst, route, path, speed):
    ground = normalize_ground(inst)
    if not ground:
        return {'speed_profile': []}
    meta = route['ground_risk']
    return {'ground_risk': copy.deepcopy(meta),
            'speed_profile': (speed_profile(path, ground['zones'], ground['speed_policy'], speed)
                              if meta['policy'] == 'risk_adjust' else [])}


def knowledge(inst, execution):
    ground = normalize_ground(inst)
    planner = [{'rect': z['rect'], 'mu': z['mu']} for z in ground['zones']] if ground else None
    actual = [{'rect': z['rect'], 'mu': z['mu']} for z in (execution or {}).get('ground', {}).get('zones', [])]
    return {'planner': planner, 'world': actual, 'maps_match': planner == actual if planner is not None else None,
            'planner_sha256': digest(planner), 'world_sha256': digest(actual),
            'world_origin': (execution or {}).get('ground', {}).get('origin', 'explicit_uniform' if ground else 'reference'),
            'scope': 'known-map declaration vs actual friction partitions; explicit mismatch is retained, never hidden'}


def verify(plan, result):
    """Reconstruct frozen caps and inspect truth segments intersecting low-mu bands.

    Truth is an evaluation input only. The controller sees no evaluation result.
    Reported speeds are segment averages at recorded source times, not certified
    continuous-time peaks. The circumscribed square expansion is conservative.
    """
    ground = normalize_ground(plan['instance'])
    if ground is None:
        return None
    errors, data_errors, rows = [], [], []
    if plan.get('ground_knowledge') != knowledge(plan['instance'], plan.get('execution')):
        data_errors.append('规划地面/世界地面的冻结映射记录不一致')
    for vehicle in plan['vehicles']:
        meta = vehicle.get('ground_risk') or {}
        if meta.get('schema') != SCHEMA or meta.get('ground_sha256') != digest(ground) or meta.get('policy') not in ('ignore', 'risk_adjust'):
            data_errors.append(f"{vehicle['name']} 缺少有效的冻结地面规划策略")
            continue
        expected = vehicle_fields(plan['instance'], vehicle, vehicle['path'], plan['speed_mps'])['speed_profile']
        if vehicle.get('speed_profile') != expected:
            data_errors.append(f"{vehicle['name']} 完整执行路径的限速剖面与重新计算不一致")
        if meta['policy'] == 'ignore':
            continue
        regions = [box(*z['risk_rect']) for z in ground['zones'] if z['mu'] < ground['speed_policy']['mu_threshold']]
        area = unary_union(regions)
        limit = min(plan['speed_mps'], ground['speed_policy']['v_limit_mps'])
        samples = result.get('rigid_body_poses', {}).get(vehicle['name'], [])
        maxima, exposure, count, violations = 0., 0., 0, 0
        if len(samples) < 2:
            data_errors.append(f"{vehicle['name']} 缺少限速独立验收所需的真值")
        for a, b in zip(samples, samples[1:]):
            if len(a) < 3 or len(b) < 3 or not all(math.isfinite(v) for v in a[:3] + b[:3]) or not 0 < b[0]-a[0] <= .25:
                data_errors.append(f"{vehicle['name']} 真值源时间或采样间隔无效")
                break
            segment = LineString([a[1:3], b[1:3]])
            if segment.intersects(area):
                speed = math.dist(a[1:3], b[1:3]) / (b[0]-a[0])
                maxima = max(maxima, speed)
                exposure += segment.intersection(area).length
                count += 1
                violations += speed > limit + SPEED_TOLERANCE_MPS
        if violations:
            errors.append(f"{vehicle['name']} 低附着足迹区域真值速度 {maxima:.3f} m/s 超过 {limit + SPEED_TOLERANCE_MPS:.2f} m/s")
        rows.append({'vehicle': vehicle['name'], 'truth_max_segment_speed_mps': maxima,
                     'footprint_exposure_m': exposure, 'segments': count, 'violations': violations,
                     'limit_mps': limit, 'tolerance_mps': SPEED_TOLERANCE_MPS})
    errors.extend(data_errors)
    return {'valid': not errors, 'data_valid': not data_errors, 'errors': errors, 'vehicles': rows,
            'schema': 'urp.ground-speed-check/1', 'scope': 'source-time truth segment speeds in conservative footprint bands, not an instantaneous speed guarantee'}
