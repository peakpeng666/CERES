# CERES 规划算法、动力学仿真器与元学习模型能力发现与长时任务监控
from __future__ import annotations

import json
import logging

from .. import paths, physics

log = logging.getLogger('urp_lab.agent')

STUDY_SCAN = 40


def _learners() -> list[dict]:
    out = []
    names = {'cvrp-knn-v1': ('CVRP 算法选择器', 'kNN 选择器加保形风险门控，只在其训练分布内启用'),
             'coverage-order-v1': ('覆盖配置排序', '按田块特征排序覆盖配置；开发侧分组交叉验证')}
    for key, (name, summary) in names.items():
        path = paths.LAB / 'models' / f'{key}.json'
        try:
            data = json.loads(path.read_text(encoding='utf8'))
        except (OSError, ValueError):
            out.append({'id': key, 'kind': 'learner', 'name': name, 'summary': summary, 'status': 'unavailable', 'reason': '模型文件不可读'})
            continue
        evaluation = data.get('evaluation') or {}
        out.append({'id': key, 'kind': 'learner', 'name': name, 'summary': summary, 'status': 'ready', 'reason': None,
                    'detail': data.get('scope_note') or evaluation.get('caveat'), 'version': (data.get('artifact_sha256') or '')[:12]})
    ledger = paths.LAB / 'models' / 'library-ledger.json'
    if ledger.exists():
        data = json.loads(ledger.read_text(encoding='utf8'))
        out.append({'id': 'library-ledger', 'kind': 'learner', 'name': '算法库进化账本', 'status': 'ready', 'reason': None,
                    'summary': f"{len(data.get('members') or [])} 个成员的来源、证据与贡献", 'detail': data.get('scope'), 'version': data.get('generated_utc')})
    return out


def _simulators(lab) -> list[dict]:
    gap = paths.gazebo_gap()
    ros = {} if gap else lab.services.ros()
    ready = not gap and ros.get('state') in ('ready', 'degraded')
    reason = None if ready else gap or ros.get('detail')
    mujoco = physics.available()
    return [
        {'id': 'gazebo.field', 'kind': 'simulator', 'name': 'Gazebo 田间多车', 'status': 'ready' if ready else 'unavailable', 'reason': reason, 'approval': True,
         'summary': 'Gazebo Fortress 中按方案执行，ROS 2 话题与接触传感器全程记录', 'href': '#/sim'},
        {'id': 'gazebo.tb4', 'kind': 'simulator', 'name': 'TurtleBot4 + Nav2', 'status': 'ready' if ready else 'unavailable', 'reason': reason, 'approval': True,
         'summary': '单车 Nav2 导航验证；地图由官方世界提供', 'href': '#/sim'},
        {'id': 'mujoco.field', 'kind': 'simulator', 'name': 'MuJoCo 物理预演', 'status': 'ready' if mujoco['available'] else 'unavailable', 'reason': mujoco['reason'], 'approval': False,
         'version': mujoco['version'], 'summary': '同一方案、同一路径跟踪器与车辆参数，数秒跑完，不需要 ROS', 'href': '#/sim'},
    ]


def capabilities(lab) -> dict:
    algorithms = [{'id': a['id'], 'kind': 'algorithm', 'name': a['name'], 'problem_class': a['problem_classes'][0], 'library': a.get('library'),
                   'status': 'ready' if a.get('available') else 'unavailable', 'reason': a.get('unavailable_reason'), 'summary': a.get('summary'),
                   'href': f"#/algorithms/{a['id']}"} for a in lab.catalog()]
    simulators = _simulators(lab)
    gazebo, mujoco = simulators[0], simulators[2]
    both = gazebo['status'] == mujoco['status'] == 'ready'
    transfers = [{'id': 'sim2sim.gazebo-mujoco', 'kind': 'transfer', 'name': 'Gazebo → MuJoCo', 'status': 'ready' if both else 'unavailable',
                  'reason': None if both else (gazebo['reason'] or mujoco['reason']), 'href': '#/sim',
                  'summary': '把已结束的 Gazebo 会话按冻结方案在 MuJoCo 重放，量化两引擎的轨迹差、时间差与里程差'}]
    return {'algorithms': algorithms, 'simulators': simulators, 'learners': _learners(), 'transfers': transfers}


def _study_rows(lab) -> list[dict]:
    rows = []
    for study in lab.store.list_studies(STUDY_SCAN):
        view = lab.studies.view(study)
        p = view['progress']
        if p['state'] == 'running':
            rows.append({'id': view['study_id'], 'kind': 'study', 'label': view.get('title') or '课题', 'state': 'running',
                         'progress': p['finished'] / p['total'] if p['total'] else None, 'detail': f"{p['finished']}/{p['total']} 个运行",
                         'started_utc': view.get('created_utc'), 'href': f"#/experiments/{view['study_id']}"})
    return rows


def jobs(lab) -> dict:
    """Long-running work that is active now, newest first."""
    rows = []
    in_study = {d['run_id'] for study in lab.store.list_studies(STUDY_SCAN) for d in study.get('design', [])}
    for r in lab.store.active_runs():
        if r['run_id'] in in_study:
            continue
        rows.append({'id': r['run_id'], 'kind': 'solve', 'label': (r.get('algorithm') or {}).get('name') or r['run_id'], 'state': r['state'], 'progress': None,
                     'detail': (r.get('scenario_snapshot') or {}).get('title') or r.get('scenario_id'), 'started_utc': r.get('created_utc'),
                     'href': f"#/plan/{r['scenario_id']}"})
    rows += _study_rows(lab)
    for r in lab.physics.active():
        p = r.get('progress') or {}
        batch = r.get('kind') == 'batch'
        total = p.get('runs_total' if batch else 'tasks_total')
        detail = f"{p.get('runs_done', 0)}/{total} 次" if batch else f"仿真时钟 {p['sim_time']} s" if p.get('sim_time') else None
        rows.append({'id': r['physics_run_id'], 'kind': 'physics', 'label': f"{'鲁棒性评估' if batch else '物理预演'} · {r.get('algorithm') or r.get('run_id')}", 'state': r['state'],
                     'progress': p.get('runs_done' if batch else 'tasks_done', 0) / total if total else None, 'detail': detail,
                     'started_utc': r.get('created_utc'), 'href': '#/sim'})
    for p in lab.packs.list():
        if p['state'] in ('downloading', 'installing'):
            rows.append({'id': p['id'], 'kind': 'pack', 'label': f"下载 {p['name']}", 'state': p['state'], 'progress': p['progress'], 'detail': '校验并解压' if p['state'] == 'installing' else None,
                         'started_utc': None, 'href': '#/system/data'})
    field = lab.fieldsim.state()
    if field.get('phase') in ('starting', 'ready', 'running'):
        vehicles = field.get('vehicles') or []
        total = sum(int(v.get('tasks_total') or 0) for v in vehicles)
        done = sum(int(v.get('tasks_done') or 0) for v in vehicles)
        rows.append({'id': field.get('session_id'), 'kind': 'fieldsim', 'label': 'Gazebo 田间仿真', 'state': field['phase'], 'progress': done / total if total else None,
                     'detail': f"{done}/{total} 个任务点" if total else None, 'started_utc': None, 'href': '#/sim'})
    for r in lab.store.active_sim_runs():
        rows.append({'id': r['sim_run_id'], 'kind': 'tb4', 'label': 'TurtleBot4 执行', 'state': r['state'], 'progress': None, 'detail': r.get('world'),
                     'started_utc': r.get('created_utc'), 'href': '#/sim'})
    rows.sort(key=lambda x: x.get('started_utc') or '', reverse=True)
    return {'jobs': rows}
