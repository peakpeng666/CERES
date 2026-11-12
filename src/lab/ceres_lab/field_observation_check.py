"""Replay acceptance for the actual observation -> estimate -> command chain.

Reconstruction detects wrong wiring; analytic cases in tests separately validate
the estimator mathematics. Replaying the same estimator alone is not that proof.
"""
from __future__ import annotations
import math
import numpy as np
from .field_nonideal import LocalizationModel, Estimator


def verify(plan, result):
    errors, rows = [], []
    cfg = plan['execution']
    gnss = cfg['localization']['gnss']
    if gnss is None:
        return {'valid': True, 'errors': [], 'vehicles': [], 'scope': 'explicit truth-localization control'}
    for idx, vehicle in enumerate(plan['vehicles']):
        name = vehicle['name']
        try:
            inputs = result['localization_inputs'][name]
            truth = np.asarray(inputs['truth_sensor'], dtype=float)
            odom = np.asarray(inputs['odometry'], dtype=float)
            est_rows = result['pose_estimates'][name]
            observations = result['localization_observations'][name]
            for a, n in ((truth, 3), (odom, 3)):
                if a.ndim != 2 or a.shape[1] != n or len(a) < 2 or not np.isfinite(a).all() or not (np.diff(a[:,0]) > 0).all():
                    raise ValueError('定位输入格式或源时间无效')
            # Sensor truth is an evaluation input only. Every execution-time input
            # must agree with the separately recorded rigid trajectory.
            rigid = {r[0]: r for r in result['rigid_body_poses'][name]}
            shared = [r for r in truth if r[0] in rigid]
            if len(shared) != len(rigid) or any(np.max(np.abs(r[1:]-np.asarray(rigid[r[0]][1:3]))) > 1e-9 for r in shared):
                raise ValueError('观测生成输入与独立刚体真值不一致')
            wheels = {r[0]:r for r in result['wheel_odometry'][name]}
            shared_odom = [r for r in odom if r[0] in wheels]
            if len(wheels) < 2 or len(shared_odom) != len(wheels) or any(np.max(np.abs(r[1:]-np.asarray(wheels[r[0]][4:6]))) > 1e-9 for r in shared_odom):
                raise ValueError('估计器输入与记录的实际轮式里程计不一致')
            model = LocalizationModel(gnss, cfg['seed'] + idx)
            for t,x,y in truth: model.on_truth(float(x),float(y),float(t))
            if len(model.observations) != len(observations):
                raise ValueError('观测数量与声明模型重建不符')
            fixes = []
            for actual, expected in zip(observations, model.observations):
                if len(actual) != 9 or not np.allclose(actual[:4], expected[:4], atol=1e-8, rtol=0) or actual[7:] != expected[7:]:
                    raise ValueError('观测值、偏置、种子或源时间不符合声明模型')
                if actual[4] not in (0,1): raise ValueError('无效送达标记')
                if expected[5] in ('outage','truth_gap'):
                    if actual[4] or actual[5] != expected[5] or actual[6] is not None:
                        raise ValueError('失锁/真值缺流观测被错误送达')
                elif actual[4]:
                    if actual[5] is not None or not isinstance(actual[6],(int,float)) or not math.isfinite(actual[6]) or actual[6] + 1e-8 < max(actual[1],actual[8]):
                        raise ValueError('观测在可用时间/生成时间之前送达')
                    fixes.append(actual)
                elif actual[5] != 'pending_at_end' or actual[6] is not None or actual[1] <= odom[-1,0] - .25:
                    raise ValueError('观测无故丢弃或未记录实际送达时间')
            if any(b[6] < a[6] for a,b in zip(fixes,fixes[1:])):
                raise ValueError('送达时间倒退')
            start = vehicle['start']
            estimator = Estimator(start[:2], start[2], gnss)
            wanted = {r[0]: r for r in est_rows}
            reconstructed = {}
            cursor, maximum = 0, 0.0
            for t,v,w in odom:
                estimator.on_odom(float(v),float(w),float(t))
                while cursor < len(fixes) and fixes[cursor][6] <= t + 1e-8:
                    f = fixes[cursor]
                    if abs(f[6]-t) > 1e-8 or not estimator.on_fix(f[2],f[3],f[0],float(t)):
                        raise ValueError('观测送达与里程计处理时序不符')
                    cursor += 1
                rounded_t = round(float(t),4)
                pose = estimator.pose()
                reconstructed[rounded_t] = pose
                if rounded_t in wanted:
                    saved = wanted.pop(rounded_t)
                    delta = np.asarray(saved[1:4], dtype=float)-pose
                    delta[2] = (delta[2]+math.pi)%(2*math.pi)-math.pi
                    if not np.isfinite(delta).all(): raise ValueError('估计含非有限值')
                    maximum = max(maximum,float(np.max(np.abs(delta))))
            if wanted or cursor != len(fixes) or maximum > .002:
                raise ValueError('记录的估计无法从已送达观测和轮式输入重建')
            commands = result['control_traces'][name]
            if not commands: raise ValueError('缺少控制输入证据')
            for c in commands:
                if len(c) < 9 or c[7] is None or c[8] is None:
                    raise ValueError('控制周期缺少实际使用的定位姿态和时间')
                expected = reconstructed.get(round(c[8],4))
                delta = np.asarray(c[7],dtype=float)-expected if expected is not None else np.array([math.inf]*3)
                delta[2] = (delta[2]+math.pi)%(2*math.pi)-math.pi
                if expected is None or not np.isfinite(delta).all() or np.max(np.abs(delta)) > .003:
                    raise ValueError('控制器使用姿态与可重建估计不同，存在旁路')
                if abs(c[0]-c[8]) > .100001 and (abs(c[3]) > 1e-9 or abs(c[4]) > 1e-9):
                    raise ValueError('使用过期定位发出非零运动指令')
            rows.append({'vehicle':name,'observations':len(observations),'delivered':len(fixes),
                         'estimator_replay_max_abs_error':maximum,'control_cycles':len(commands)})
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            errors.append(f'{name} 定位链路独立重建失败：{exc}')
    return {'valid':not errors,'errors':errors,'vehicles':rows,
            'scope':'reconstruct seeded sensor and causal estimator; compare every recorded controller input; not a sensor calibration'}
