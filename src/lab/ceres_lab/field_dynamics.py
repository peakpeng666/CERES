"""Independent observations of rigid pose and wheel-odometry consistency.

Wheel odometry is integrated wheel kinematics, not an estimated localization
system. Its error versus truth exposes slip; it is not hidden by aligning the
entire trajectory. Only the first common pose is used for SE(2) alignment.
"""
from __future__ import annotations
import math
import numpy as np


def verify(result):
    errors, rows = [], []
    from .field_physics import MODEL, ODOMETRY_CONTRACT
    physics = result.get('physics') or {}
    if physics.get('schema') == MODEL and physics.get('odometry_contract') != ODOMETRY_CONTRACT:
        errors.append('当前物理模型缺少或修改了必需的里程计一致性协议')
    # Non-ideal execution deliberately allows wheel odometry to diverge from truth
    # (that divergence *is* the slip observable). Data validity still gates; the
    # no-slip reference bound applies only to the frozen reference configuration.
    from .field_nonideal import slip_experiment
    nonideal = slip_experiment(result.get('execution'), result.get('physics'))
    timing = result.get('truth_timing') or {}
    if timing.get('latest_clock_samples') != 0 or timing.get('tf_header_samples', 0) <= 0:
        errors.append('物理模型验收要求源仿真时间，不接受接收时间近似')
    if not result.get('vehicles'):
        errors.append('缺少待验收车辆，不能返回空集合成功')
    for v in result.get('vehicles', []):
        name = v['name']
        try:
            p = np.asarray(result.get('rigid_body_poses', {}).get(name, []), dtype=float)
            o = np.asarray(result.get('wheel_odometry', {}).get(name, []), dtype=float)
        except (ValueError, TypeError):
            errors.append(f'{name} 刚体真值或轮式里程计格式无效')
            continue
        valid = (p.ndim == o.ndim == 2 and p.shape[1:] == (7,) and o.shape[1:] == (6,)
                 and len(p) >= 2 and len(o) >= 2 and np.isfinite(p).all() and np.isfinite(o).all()
                 and (np.diff(p[:,0]) > 0).all() and (np.diff(o[:,0]) > 0).all())
        if not valid:
            errors.append(f'{name} 刚体真值或轮式里程计数据缺失/时间无效')
            continue
        # Equal endpoints do not establish stationarity throughout an unobserved interval.
        if (np.diff(p[:, 0]) > .25).any() or (np.diff(o[:, 0]) > .25).any():
            errors.append(f'{name} 真值或里程计出现超过 0.25 s 的采样间断，不能依靠跨缺口插值验收')
        if o[0, 0] > p[0, 0] + .25 or o[-1, 0] < p[-1, 0] - .25:
            errors.append(f'{name} 里程计未覆盖完整真值观测区间，不能只验收共同区间的一小段')
        if 'trails' in result:
            trail = np.asarray(result['trails'].get(name, []), dtype=float)
            if trail.shape != (len(p),4) or not np.allclose(trail, p[:,[0,1,2,6]], atol=1e-9, rtol=0):
                errors.append(f'{name} 二维轨迹与保存的刚体真值不一致')
        roll, pitch, z = np.max(np.abs(p[:,4])), np.max(np.abs(p[:,5])), np.max(np.abs(p[:,3]))
        if max(roll, pitch) > .35 or z > .5:
            errors.append(f'{name} 在平坦地面出现超出模型有效域的倾斜或高度')
        times = o[(o[:,0] >= p[0,0]) & (o[:,0] <= p[-1,0]), 0]
        if len(times) < 2:
            errors.append(f'{name} 真值与轮式里程计没有足够共同时间')
            continue
        truth = np.stack([np.interp(times, p[:,0], p[:,c]) for c in (1,2)], axis=1)
        truth_yaw = np.interp(times, p[:,0], np.unwrap(p[:,6]))
        odom = np.stack([np.interp(times, o[:,0], o[:,c]) for c in (1,2)], axis=1)
        odom_yaw = np.interp(times, o[:,0], np.unwrap(o[:,3]))
        angle = truth_yaw[0]-odom_yaw[0]
        rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        aligned = (odom-odom[0]) @ rotation.T + truth[0]
        distance = np.linalg.norm(aligned-truth, axis=1)
        yaw_error = (odom_yaw+angle-truth_yaw+math.pi) % (2*math.pi)-math.pi
        contract = (result.get('physics') or {}).get('odometry_contract')
        position_max, yaw_max = float(distance.max()), float(np.abs(yaw_error).max())
        consistency = None
        if contract is not None:
            # Thresholds are versioned implementation policy, not mutable per-result numbers.
            if contract != ODOMETRY_CONTRACT:
                errors.append(f'{name} 未知或被修改的里程计一致性协议')
                consistency = False
            else:
                consistency = (position_max <= contract['max_position_error_m']
                               and yaw_max <= contract['max_yaw_error_rad'])
                if not consistency and not nonideal:
                    errors.append(f'{name} 轮式运动学与真值不一致：最大位置误差 {position_max:.4f} m、'
                                  f'航向误差 {yaw_max:.4f} rad；超出无滑移模型有效域')
                if nonideal:
                    consistency = None        # not applicable: slip is expected and measured
        rows.append({'vehicle':name, 'truth_samples':len(p), 'odom_samples':len(o), 'paired_samples':len(times),
                     'max_roll_rad':float(roll), 'max_pitch_rad':float(pitch), 'max_abs_z_m':float(z),
                     'odom_position_rmse_m':float(np.sqrt(np.mean(distance**2))),
                     'odom_position_final_m':float(distance[-1]), 'odom_position_max_m':position_max,
                     'odom_yaw_rmse_rad':float(np.sqrt(np.mean(yaw_error**2))),
                     'odom_yaw_max_rad':yaw_max, 'no_slip_consistent':consistency,
                     'max_truth_gap_s':float(np.max(np.diff(p[:,0])))})
    return {'valid':not errors, 'errors':errors, 'vehicles':rows,
            'odometry_contract':(result.get('physics') or {}).get('odometry_contract'),
            'execution_mode': 'nonideal' if nonideal else 'ideal',
            'scope':('non-ideal mode: flat-world attitude/data gates; wheel-vs-truth divergence recorded as the slip observable, '
                     'not gated; first-common-pose alignment only' if nonideal else
                     'flat-world attitude/data and versioned no-slip consistency; wheel odometry aligned only at first common timestamp; legacy records have no new success claim'),
            'thresholds':{'tilt_rad':.35, 'absolute_model_origin_z_m':.5, 'max_sample_gap_s':.25}}
