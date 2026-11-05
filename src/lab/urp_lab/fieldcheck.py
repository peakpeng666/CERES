"""Independent checks of recorded field-simulation trajectories, run in the solver environment.

Checks the observed line segments swept by the declared circular footprint. This is not a proof
about unobserved motion between telemetry samples or collision avoidance between robots.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from . import lanes
from .field_traffic import separation

TRACK_LIMIT_M = 0.5


def verify_nonideal_observations(plan, result):
    """Data-validity gates for the non-ideal localization chain. These check that what
    was recorded is consistent with the declared observation model (schedule, latency,
    drops, coverage) — the error magnitudes themselves are experiment outcomes, not
    pass conditions."""
    errors, details = [], []
    exec_cfg = plan.get('execution') or {}
    replay = None
    if exec_cfg.get('schema') == 'urp.field-execution/2':
        from .field_observation_check import verify as replay_verify
        replay = replay_verify(plan, result)
        errors.extend(replay['errors'])
    gnss = (exec_cfg.get('localization') or {}).get('gnss')
    for v in plan['vehicles']:
        name = v['name']
        if gnss is None:
            continue                      # truth-localization control: no estimator by design
        est = (result.get('pose_estimates') or {}).get(name) or []
        obs = (result.get('localization_observations') or {}).get(name) or []
        truth = (result.get('rigid_body_poses') or {}).get(name) or []
        if len(est) < 2:
            errors.append(f'{v["label"]} 缺少定位估计序列')
            continue
        e = np.asarray([r[:4] for r in est], dtype=float)
        p = np.asarray(truth, dtype=float)
        if not np.isfinite(e[:, :4]).all() or np.any(np.diff(e[:, 0]) <= 0):
            errors.append(f'{v["label"]} 估计序列含非有限值或时间倒退')
            continue
        if p.ndim == 2 and len(p) >= 2:
            if e[0, 0] > p[0, 0] + 0.5 or e[-1, 0] < p[-1, 0] - 0.5:
                errors.append(f'{v["label"]} 估计序列未覆盖真值观测区间')
            if np.max(np.diff(e[:, 0])) > 0.25:
                errors.append(f'{v["label"]} 估计序列采样缺口超过 0.25 s')
            # Truth-leak guard: a real estimator never reproduces truth bit-for-bit.
            if float(gnss.get('noise_std_m', 0)) > 0:
                tx = np.interp(e[:, 0], p[:, 0], p[:, 1])
                ty = np.interp(e[:, 0], p[:, 0], p[:, 2])
                if float(np.max(np.hypot(e[:, 1] - tx, e[:, 2] - ty))) < 1e-6:
                    errors.append(f'{v["label"]} 估计轨迹与真值逐点一致，疑似真值直连')
        if not obs:
            errors.append(f'{v["label"]} 声明了绝对定位观测模型但没有观测记录')
        else:
            o = np.asarray([[r[0], r[1], r[4]] for r in obs], dtype=float)
            delivered = o[o[:, 2] == 1]
            dropped = len(obs) - len(delivered)
            latency = float(gnss['latency_s'])
            if len(delivered):
                if float(np.min(delivered[:, 1] - delivered[:, 0])) < latency - 0.05:
                    errors.append(f'{v["label"]} 存在早于声明延迟送达的观测')
            span = float(o[-1, 0] - o[0, 0]) if len(o) > 1 else 0.0
            if span > 20:
                want = float(gnss['fix_hz']) * span
                if not 0.7 * want <= len(obs) <= 1.3 * want:
                    errors.append(f'{v["label"]} 观测生成频率与声明模型不符')
            for r in obs:
                if not r[4] and not r[5]:
                    errors.append(f'{v["label"]} 观测被丢弃但没有声明原因')
                    break
            details.append({'vehicle': name, 'fixes_generated': len(obs), 'fixes_delivered': int(len(delivered)),
                                'fixes_dropped': int(dropped), 'observation_span_s': round(span, 1)})
    report = result.get('execution_report') or {}
    if not report.get('localization') or not report.get('slip'):
        errors.append('缺少非理想执行的评价报告（定位/滑移）')
    events = result.get('execution_events') or []
    counted = sum(1 for e in events if e.get('intervention'))
    if (report.get('interventions') or 0) != counted:
        errors.append('干预计数与事件记录不一致')
    return {'valid': not errors, 'errors': errors, 'observations': details, 'chain_replay': replay,
            'scope': '观测模型一致性（频率/延迟/丢弃/覆盖）与真值隔离检查；误差大小是实验结果，不是门槛'}


def verify_motion_metrics(result):
    """Cross-check online integration with the independently saved pose sequence."""
    errors, details, data_errors = [], [], []
    for v in result.get('vehicles', []):
        trail = result.get('trails', {}).get(v['name'], [])
        traced = sum(math.dist(a[1:3], b[1:3]) for a, b in zip(trail, trail[1:]))
        measured, speed = v.get('distance_m'), v.get('speed')
        tolerance = max(.2, .01 * traced)  # coarser saved poses omit a little curvature
        valid = (isinstance(measured, (int, float)) and math.isfinite(measured)
                 and measured >= 0 and abs(measured - traced) <= tolerance)
        if not valid:
            data_errors.append(f"{v['name']} 在线累计里程与保存的真值轨迹不一致")
        stopped = isinstance(speed, (int, float)) and math.isfinite(speed) and 0 <= speed < .02
        if not stopped:
            errors.append(f"{v['name']} 终点实测速度未稳定停止")
        details.append({'vehicle': v['name'], 'trace_distance_m': traced, 'reported_distance_m': measured,
                        'tolerance_m': tolerance, 'distance_valid': valid, 'stopped': stopped})
    total = result.get('metrics', {}).get('total_distance_m')
    if (not isinstance(total, (int, float)) or not math.isfinite(total)
            or abs(total - sum(v.get('distance_m') or 0 for v in result.get('vehicles', []))) > .02 * max(1, len(details))):
        data_errors.append('总实测里程与各车里程之和不一致')
    errors.extend(data_errors)
    return {'valid': not errors, 'data_valid': not data_errors, 'errors': errors, 'vehicles': details}


def verify_task_truth(plan, result):
    from .field_tracking import Driver
    errors, details = [], []
    for vehicle in plan['vehicles']:
        name = vehicle['name']
        trail = np.asarray((result.get('trails') or {}).get(name, []), dtype=float)
        if trail.ndim != 2 or len(trail) < 2 or trail.shape[1] < 4:
            errors.append(f'{name} 缺少任务真值证据')
            continue
        observed = {r['id']:r for r in result.get('tasks',[]) if r['vehicle']==name}
        for task in Driver(vehicle, 1.0).tasks:
            r = observed.get(task['id'], {})
            begin, end = r.get('service_started_at'), r.get('serviced_at')
            if task['service_s'] <= 0: begin = r.get('t_sim')
            if begin is None or end is None or not all(isinstance(t,(int,float)) and math.isfinite(t) for t in (begin,end)):
                errors.append(f'{name} 任务 {task["id"]} 无真实停留区间')
                continue
            valid = end-begin+1e-6 >= task['service_s'] and begin >= trail[0,0] and end <= trail[-1,0]
            times = np.r_[begin, trail[(trail[:,0]>begin)&(trail[:,0]<end),0], end]
            xy = np.column_stack([np.interp(times,trail[:,0],trail[:,c]) for c in (1,2)])
            yaw = np.interp(times,trail[:,0],np.unwrap(trail[:,3]))
            distances = np.linalg.norm(xy-np.asarray(task['goal']),axis=1)
            valid = valid and float(distances.max()) <= task['radius'] + .005
            speeds = np.linalg.norm(np.diff(xy,axis=0),axis=1)/np.maximum(np.diff(times),1e-9)
            yaw_rates = np.abs(np.diff(yaw))/np.maximum(np.diff(times),1e-9)
            valid = valid and (task['service_s']<=0 or (float(speeds.max()) <= .03 and float(yaw_rates.max()) <= .05))
            details.append({'vehicle':name,'task':task['id'],'truth_radius_m':task['radius'],
                'max_distance_m':float(distances.max()),'max_speed_mps':float(speeds.max()),
                'max_yaw_rate_radps':float(yaw_rates.max()),'valid':bool(valid)})
            if not valid: errors.append(f'{name} 任务 {task["id"]} 真值位置或静止停留不满足声明条件')
        # A wheel-derived zero velocity is not evidence that a slipping body stopped.
        start = max(trail[0,0],trail[-1,0]-.5)
        p = trail[trail[:,0]>=start]
        if (len(p)<2 or np.max(np.linalg.norm(np.diff(p[:,1:3],axis=0),axis=1)/np.diff(p[:,0])) >= .02
                or np.max(np.abs(np.diff(np.unwrap(p[:,3])))/np.diff(p[:,0])) >= .05):
            errors.append(f'{name} 终点刚体真值未稳定静止')
    return {'valid':not errors,'errors':errors,'tasks':details,'scope':'truth position and stop/dwell, distinct from estimated arrival'}


def track_check(plan, result):
    """Footprint, path completeness and task dwell of the recorded trails. The same test for every physics engine."""
    inst = plan['instance']
    boundary = Polygon(inst['boundary'])
    solids = [Polygon(o['polygon']) for o in inst.get('obstacles', [])]
    if plan['travel'] == 'rows':
        solids += lanes.Network(inst).rows()
    solids = np.array(solids, dtype=object)
    radius = plan['robot_radius_m']
    errors, details = [], []
    for v in plan['vehicles']:
        trail = result.get('trails', {}).get(v['name'], [])
        if not trail or any(len(p) < 4 or not all(math.isfinite(x) for x in p[:4]) for p in trail):
            errors.append(f"{v['label']} 缺少有效真值轨迹")
            continue
        pts = [p[1:3] for p in trail]
        actual = LineString(pts) if len(pts) > 1 else Point(pts[0])
        intended = LineString(v['path'])
        # A stationary trajectory is evidence of no motion, not an empty geometry after segmentize.
        if actual.length <= 1e-12:
            actual = Point(pts[0])
        if intended.length <= 1e-12:
            intended = Point(v['path'][0])
        obstacle_clearance = float(shapely.distance(solids, actual).min()) if len(solids) else None
        boundary_clearance = actual.distance(boundary.boundary)
        footprint = boundary.covers(actual) and boundary_clearance >= radius - 1e-3
        footprint = footprint and (obstacle_clearance is None or obstacle_clearance >= radius - 1e-3)
        # Sample plan segments too: observing just endpoints must not certify a skipped leg.
        dense = shapely.segmentize(intended, 0.1)
        reverse = float(shapely.distance(shapely.points(shapely.get_coordinates(dense)), actual).max())
        actual_dense = shapely.segmentize(actual, 0.1)
        forward = float(shapely.distance(shapely.points(shapely.get_coordinates(actual_dense)), intended).max())
        final_error = math.dist(pts[-1], v['path'][-1])
        if not footprint:
            errors.append(f"{v['label']} 的实测轨迹圆足迹侵入障碍/作物或越出边界")
        # Distance to a fixed set is 1-Lipschitz: at most 0.05 m can be missed between samples.
        if max(reverse, forward) + 0.05 > TRACK_LIMIT_M or final_error > 0.35:
            errors.append(f"{v['label']} 未完整跟踪计划路径或未回到终点")
        details.append({'vehicle': v['name'], 'samples': len(trail), 'footprint_clear': footprint,
                        'obstacle_clearance_m': obstacle_clearance, 'boundary_clearance_m': boundary_clearance,
                        'planned_to_actual_max_m': reverse, 'actual_to_planned_max_m': forward,
                        'final_error_m': final_error})
    observed = {(t['vehicle'], t['id']): t for t in result.get('tasks', [])}
    for v in plan['vehicles']:
        for task in v['tasks']:
            t = observed.get((v['name'], task['id'])) or {}
            arrival, complete = t.get('t_sim'), t.get('serviced_at')
            if (t.get('state') != 'reached' or arrival is None or complete is None
                    or complete - arrival + 1e-6 < task['service_s']):
                errors.append(f"{v['label']} 任务 {task['id']} 未完成规定的作业停留时间")
    return errors, details


def verify(plan, result):
    radius = plan['robot_radius_m']
    errors = []
    motion_metrics = verify_motion_metrics(result) if 'truth_timing' in result else None
    if result.get('execution') != plan.get('execution'):
        errors.append('执行配置与冻结计划不一致；不得用结果标志改变验收分支')
    if plan.get('execution') and not (plan.get('ground_zones_audit') or {}).get('valid'):
        errors.append('缺少冻结地面分区的 SDF 审计')
    dynamics = None
    if plan.get('physics') or result.get('physics'):
        if result.get('physics') != plan.get('physics') or not (plan.get('physics_audit') or {}).get('valid'):
            errors.append('执行结果物理模型与冻结计划或 SDF 核验不一致')
        if (result.get('physics_runtime') or {}).get('valid') is not True:
            errors.append('缺少物理实现加载及配置核验，不能认证执行结果')
        from .field_dynamics import verify as verify_dynamics
        dynamics = verify_dynamics({**result, 'execution': plan.get('execution')})
        errors.extend(dynamics['errors'])
    frozen_valid = not errors and (motion_metrics is None or motion_metrics['data_valid'])
    if motion_metrics:
        errors.extend(motion_metrics['errors'])
    nonideal = None
    if (plan.get('execution') or {}).get('mode') == 'nonideal':
        nonideal = verify_nonideal_observations(plan, result)
        errors.extend(nonideal['errors'])
    from .ground_execution import verify as verify_ground
    ground_speed = verify_ground(plan, result)
    if ground_speed:
        errors.extend(ground_speed['errors'])
        frozen_valid = frozen_valid and ground_speed['data_valid']
    task_truth = None
    if ((plan.get('execution') or {}).get('schema') == 'urp.field-execution/2'
            or (plan.get('physics') or {}).get('actuator_mode') == 'torque_limited'):
        task_truth = verify_task_truth(plan, result)
        errors.extend(task_truth['errors'])
    track_errors, details = track_check(plan, result)
    errors.extend(track_errors)
    traffic = None
    if plan.get('traffic', {}).get('robot_collisions'):
        try:
            traffic = separation({v['name']: result.get('trails', {}).get(v['name'], []) for v in plan['vehicles']}, radius)
            if not traffic['valid']:
                errors.append('车间实测时间轨迹的足迹净空或采样完整性检查未通过')
        except ValueError as exc:
            errors.append(str(exc))
        if result.get('contacts'):
            errors.append('Gazebo 记录到车辆与实体障碍或其他车辆接触')
        if not all(result.get('telemetry', {}).get('contacts', {}).get(v['name'], 0) > 0 for v in plan['vehicles']):
            errors.append('缺少接触传感器消息，不能把未观测到的接触判为零')
    return {'valid': not errors, 'data_valid': frozen_valid and (nonideal is None or nonideal['valid']), 'errors': errors, 'vehicles': details, 'radius_m': radius,
            'traffic': traffic, 'motion_metrics': motion_metrics, 'dynamics': dynamics,
            'nonideal_observations': nonideal, 'task_truth': task_truth, 'ground_speed': ground_speed,
            'track_tolerance_m': TRACK_LIMIT_M, 'distance_sampling_m': 0.1, 'distance_error_bound_m': 0.05,
            'scope': '实测轨迹线段的圆足迹、完整路径；有交通策略时另检车间同步距离与接触记录；非理想模式另检观测模型一致性'}


if __name__ == '__main__':
    runtime = Path(sys.argv[1])
    print(json.dumps(verify(json.loads((runtime / 'plan.json').read_text()),
                            json.loads((runtime / 'result-candidate.json').read_text())), ensure_ascii=False, allow_nan=False))
