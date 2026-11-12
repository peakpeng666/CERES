"""A server-assembled experiment record from immutable planning inputs and saved executions."""
from __future__ import annotations

from datetime import datetime, timezone

from .assets.bindings import digest, snapshot
from .result_integrity import without_invalidated_results
from .services import ServiceError


def record(lab, run):
    warnings = []
    assets = run.get('asset_snapshot')
    if assets is None:
        warnings.append('历史运行未保存提交时资产快照；以下版本在导出时解析，不冒充当时快照')
        try:
            assets = snapshot(run.get('instance') or {})
        except ServiceError as exc:
            assets = []
            warnings.append(f'历史资产无法补齐：{exc.message}')
    fields = [lab.fieldsim.result(row['session_id']) for row in lab.fieldsim.results(run['run_id'])]
    nav = lab.store.list_sim_runs(run_id=run['run_id'], limit=10001)
    if len(nav) > 10000:
        raise ServiceError(413, 'record_too_large', '该规划超过 10000 次仿真，请按会话分别导出')
    planning = without_invalidated_results(run)
    payload = {'schema': 'agri-workbench.experiment-record/2', 'run_id': run['run_id'],
               'planning': planning, 'scenario': run.get('scenario_snapshot'), 'assets': assets,
               'decision': run.get('recommendation_at_submission'),
               'simulations': {'field': fields, 'nav2': nav}, 'warnings': warnings,
               'scope': '规划输入以本次运行保存的 instance 为准；不读取后来编辑的场景；规划/执行/清理状态分别保留'}
    if planning is not run:
        # A complete experiment record must retain even rejected evidence, but
        # consumers of planning must not mistake it for usable result values.
        payload['invalidated_planning_evidence'] = run
        warnings.append('来源身份核对失败；planning 不含有效求解指标；invalidated_planning_evidence 保留原记录，仅供审计，不得用于结果比较')
    return {'record': payload, 'integrity': {'algorithm': 'sha256', 'sha256': digest(payload),
            'canonicalization': 'UTF-8 JSON; sorted object keys; separators comma/colon; ensure_ascii=false; allow_nan=false',
            'scope': 'record object only; content integrity, not a digital signature or proof of authorship'},
            'assembled_utc': datetime.now(timezone.utc).isoformat()}
