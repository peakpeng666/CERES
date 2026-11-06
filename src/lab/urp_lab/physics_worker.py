"""One physics run in its own process, so a native fault or a cancel never reaches the gateway.

Usage: python -m urp_lab.physics_worker <plan.json> <out.json> [draw-json]. Progress goes to stdout, one JSON object per line.
With a draw ``{"seed": ..., "intensity": ..., "factors": [...]}`` the plan runs perturbed and the output is the batch row.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def check(plan: dict, result: dict) -> dict:
    """The independent checks of one record; the same for a plain run, a perturbed run and a Gazebo record."""
    from . import fieldcheck
    from .field_traffic import separation
    errors, details = fieldcheck.track_check(plan, result)
    motion = fieldcheck.verify_motion_metrics(result)
    errors += motion['errors']
    traffic = None
    if len(plan['vehicles']) > 1:
        try:
            traffic = separation({v['name']: result['trails'][v['name']] for v in plan['vehicles']}, plan['robot_radius_m'])
            if not traffic['valid']:
                errors.append('车间实测时间轨迹的足迹净空或采样完整性检查未通过')
        except ValueError as exc:
            errors.append(str(exc))
    if result['contacts']:
        errors.append('MuJoCo 记录到车辆与实体障碍或其他车辆接触')
    return {'valid': not errors, 'errors': errors, 'vehicles': details, 'motion_metrics': motion, 'traffic': traffic,
            'scope': '实测轨迹线段的圆足迹、完整路径、任务停留与接触；与 Gazebo 记录同一检查'}


def sample(plan: dict, seed: int, intensity: float = 1., factors=None) -> dict:
    """One draw of a batch, reduced to its row."""
    from . import mujoco_field, physics_stats, time_windows
    actual = mujoco_field.perturb(plan, seed, intensity, mujoco_field.FACTORS if factors is None else factors)
    result = mujoco_field.execute(actual)
    result['check'] = check(actual, result)
    result['time_windows'] = time_windows.execution(actual, result)
    return {'seed': seed, **physics_stats.sample_row(actual, result)}


def main(plan_path: str, out_path: str, draw: str | None = None) -> int:
    from . import mujoco_field, time_windows
    plan = json.loads(Path(plan_path).read_text(encoding='utf8'))
    if draw:
        row = sample(plan, **json.loads(draw))
        Path(out_path).write_text(json.dumps(row, ensure_ascii=False, allow_nan=False), encoding='utf8')
        return 0
    try:
        result = mujoco_field.execute(plan, progress=lambda p: print(json.dumps(p), flush=True))
    except mujoco_field.Unsupported as exc:
        print(json.dumps({'unsupported': str(exc)}), flush=True)
        return 3
    result['check'] = check(plan, result)
    result['time_windows'] = time_windows.execution(plan, result)
    Path(out_path).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf8')
    return 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:4]))
