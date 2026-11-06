"""One physics run in its own process, so a native fault or a cancel never reaches the gateway.

Usage: python -m urp_lab.physics_worker <plan.json> <result.json>. Progress goes to stdout, one JSON object per line.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(plan_path: str, result_path: str) -> int:
    from . import fieldcheck, mujoco_field
    from .field_traffic import separation
    plan = json.loads(Path(plan_path).read_text(encoding='utf8'))
    try:
        result = mujoco_field.execute(plan, progress=lambda p: print(json.dumps(p), flush=True))
    except mujoco_field.Unsupported as exc:
        print(json.dumps({'unsupported': str(exc)}), flush=True)
        return 3
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
    result['check'] = {'valid': not errors, 'errors': errors, 'vehicles': details, 'motion_metrics': motion, 'traffic': traffic,
                       'scope': '实测轨迹线段的圆足迹、完整路径、任务停留与接触；与 Gazebo 记录同一检查'}
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf8')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
