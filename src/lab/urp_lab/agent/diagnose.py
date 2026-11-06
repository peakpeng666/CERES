"""Failure diagnosis and repair proposals for fleet-routing runs.

Three causes are kept apart because they call for different actions:

- ``task_infeasible``  the task as stated cannot be served (unreachable task, demand above capacity, single-trip demand
  above fleet capacity); repairs change the task and therefore need the user's approval;
- ``method_not_applicable`` / ``budget_insufficient``  the task may be solvable but this method or budget did not
  produce a verified plan; the agent may switch method or raise the budget within the declared limits;
- ``implementation_error``  a solver crash, a malformed result, or the independent re-check disagreeing with the
  stored verdict; this is reported as a software defect and never "repaired" by relaxing the task.
"""
from __future__ import annotations

import math

MAX_BUDGET_S = 60.0
CATEGORY_LABEL = {
    'ok': '已验证可行', 'task_infeasible': '任务本身不可行', 'method_not_applicable': '方法不适用',
    'budget_insufficient': '预算不足', 'implementation_error': '软件实现错误', 'cancelled': '已取消',
    'pending': '尚未结束',
}


def precheck(instance: dict) -> dict:
    """Cheap necessary conditions on the task map, before any solver runs."""
    from shapely.geometry import Point, Polygon
    findings = []
    veh = instance['vehicles']
    caps = [int(c.get('capacity', veh['capacity'])) * int(c.get('count', 1)) for c in veh.get('fleet') or []] \
        or [int(veh['capacity']) * int(veh['count'])]
    single_caps = [int(c.get('capacity', veh['capacity'])) for c in veh.get('fleet') or []] or [int(veh['capacity'])]
    demand = {t['id']: int(t.get('demand', 1)) for t in instance['tasks']}
    over = [tid for tid, d in demand.items() if d > max(single_caps)]
    if over:
        findings.append({'code': 'demand_above_capacity', 'severity': 'infeasible', 'tasks': over[:20],
                         'message': f'{len(over)} 个任务点的需求超过单车容量 {max(single_caps)}，任何方法都无法服务'})
    total = sum(demand.values())
    if veh.get('trips') == 'single' and total > sum(caps):
        findings.append({'code': 'single_trip_capacity', 'severity': 'infeasible', 'total_demand': total,
                         'fleet_capacity': sum(caps),
                         'message': f'每车单趟时总需求 {total} 超过车队总容量 {sum(caps)}'})
    boundary = Polygon(instance['boundary'])
    obstacles = [(o.get('id'), Polygon(o['polygon'])) for o in instance.get('obstacles') or [] if len(o.get('polygon') or []) >= 3]
    outside, blocked = [], []
    for t in instance['tasks']:
        p = Point(t['xy'])
        if not boundary.buffer(1e-6).contains(p):
            outside.append(t['id'])
        elif any(poly.contains(p) for _, poly in obstacles):
            blocked.append(t['id'])
    if outside:
        findings.append({'code': 'task_outside_field', 'severity': 'infeasible', 'tasks': outside[:20],
                         'message': f'{len(outside)} 个任务点位于场地边界之外'})
    if blocked:
        findings.append({'code': 'task_inside_obstacle', 'severity': 'infeasible', 'tasks': blocked[:20],
                         'message': f'{len(blocked)} 个任务点位于障碍内部'})
    depots = instance.get('depots') or [instance['depot']]
    bad_depots = [i for i, d in enumerate(depots) if not boundary.buffer(1e-6).contains(Point(d))
                  or any(poly.contains(Point(d)) for _, poly in obstacles)]
    if bad_depots:
        findings.append({'code': 'depot_invalid', 'severity': 'infeasible', 'depots': bad_depots,
                         'message': '车库位于场地外或障碍内'})
    return {'feasible_by_precheck': not any(f['severity'] == 'infeasible' for f in findings), 'findings': findings,
            'total_demand': total, 'fleet_capacity_per_trip': sum(caps)}


def classify(run: dict, precheck_result: dict | None = None) -> dict:
    """Map one stored run to a cause with evidence; ``recheck`` may override an apparently valid run."""
    state, status = run.get('state'), run.get('status')
    error = str(run.get('error') or '')
    check = run.get('check') or {}
    reason = ((run.get('raw_result') or {}).get('reason')) or ''
    evidence = []
    if state in ('queued', 'running'):
        return {'category': 'pending', 'evidence': ['运行尚未结束']}
    if state == 'canceled':
        return {'category': 'cancelled', 'evidence': [error or '已取消']}
    if state == 'timeout':
        return {'category': 'budget_insufficient', 'evidence': [error or '超出时间预算']}
    if state == 'error':
        if '不连通' in error or '隔断' in error:
            return {'category': 'task_infeasible', 'evidence': [error], 'code': 'unreachable_tasks'}
        if '只支持' in error or '不适用' in error or '超过' in error and '上限' in error:
            return {'category': 'method_not_applicable', 'evidence': [error]}
        return {'category': 'implementation_error', 'evidence': [error or '工作进程异常结束'], 'log': (run.get('process') or {}).get('evidence')}
    if status in ('infeasible', 'no_solution'):
        pre = precheck_result or {}
        if status == 'infeasible' and (not pre.get('feasible_by_precheck', True) or '超过车队' in reason):
            evidence = [reason] + [f['message'] for f in pre.get('findings', [])]
            return {'category': 'task_infeasible', 'evidence': [e for e in evidence if e]}
        return {'category': 'method_not_applicable' if status == 'infeasible' else 'budget_insufficient',
                'evidence': [reason or '求解器没有返回可行方案']}
    if status == 'feasible':
        if check and not check.get('valid', False):
            errs = [str(e) for e in (check.get('errors') or [])][:6]
            return {'category': 'implementation_error', 'evidence': ['求解器报告可行，但独立检查未通过'] + errs,
                    'code': 'checker_rejected'}
        return {'category': 'ok', 'evidence': []}
    return {'category': 'implementation_error', 'evidence': [f'未知终态 state={state} status={status}']}


def recheck(scenario: dict, run: dict) -> dict:
    """Independent re-verification of a stored fleet run: re-run the checker on the stored routes and instance."""
    from ..solvers import fleet
    if run.get('problem_class') != 'fleet_routing' or run.get('state') != 'done' or run.get('status') != 'feasible':
        return {'performed': False, 'reason': '仅对已完成且报告可行的多车方案复核'}
    inst = run.get('instance') or scenario.get('instance')
    try:
        fresh = fleet.check(inst, run.get('routes') or [], run.get('params') or {})
    except Exception as exc:  # noqa: BLE001 - a checker crash is itself a finding
        return {'performed': True, 'valid': False, 'agrees': False, 'errors': [f'复核时检查器异常：{type(exc).__name__}: {exc}']}
    stored = run.get('check') or {}
    cost_ok = fresh.get('computed_cost') is None or stored.get('computed_cost') is None or \
        math.isclose(fresh['computed_cost'], stored['computed_cost'], rel_tol=1e-6, abs_tol=1e-6)
    agrees = bool(fresh.get('valid')) == bool(stored.get('valid')) and cost_ok
    served = sorted(t for r in run.get('routes') or [] for t in r.get('sequence') or [])
    expected = sorted(t['id'] for t in inst['tasks'])
    items = [
        {'id': 'coverage', 'label': '每个任务点恰好服务一次', 'ok': served == expected,
         'detail': f'服务 {len(served)} / {len(expected)}，重复 {len(served) - len(set(served))}'},
        {'id': 'checker', 'label': '路线、载重、避障与里程独立复算', 'ok': bool(fresh.get('valid')),
         'detail': '；'.join(str(e) for e in (fresh.get('errors') or [])[:4]) or '无违规'},
        {'id': 'agreement', 'label': '复核与存档判定一致', 'ok': agrees,
         'detail': f"复算里程 {fresh.get('computed_cost')}，存档 {stored.get('computed_cost')}"},
    ]
    collisions = sum(int(r.get('collisions') or 0) for r in fresh.get('routes') or [])
    items.append({'id': 'collision', 'label': '车体足迹不与障碍相交', 'ok': collisions == 0, 'detail': f'碰撞段 {collisions}'})
    return {'performed': True, 'valid': bool(fresh.get('valid')) and served == expected, 'agrees': agrees,
            'computed_cost': fresh.get('computed_cost'), 'items': items, 'errors': fresh.get('errors') or [],
            'checker': fresh.get('checker')}


def repairs(category: str, run: dict, precheck_result: dict | None, catalog: list[dict], tried: set[str],
            budget_s: float) -> list[dict]:
    """Ordered repair actions; ``needs_user`` marks changes to the task itself."""
    actions = []
    applicable = [a for a in catalog if 'fleet_routing' in a.get('problem_classes', []) and a.get('available')]
    untried = [a['id'] for a in applicable if a['id'] not in tried]
    if category == 'budget_insufficient' and budget_s < MAX_BUDGET_S:
        actions.append({'action': 'raise_budget', 'time_limit_s': min(MAX_BUDGET_S, round(budget_s * 3, 1)),
                        'algorithms': [run['algorithm']['id']], 'needs_user': False,
                        'why': f'在 {min(MAX_BUDGET_S, round(budget_s * 3, 1)):g} s 预算内重试同一方法'})
    if category in ('method_not_applicable', 'budget_insufficient', 'implementation_error') and untried:
        preferred = [a for a in ('pyvrp.fleet', 'ortools.fleet', 'panel.savings_fleet') if a in untried] or untried
        actions.append({'action': 'switch_method', 'algorithms': preferred[:2], 'needs_user': False,
                        'why': '换用尚未尝试的方法，并以同一独立检查器判定'})
    if category == 'task_infeasible':
        codes = {f['code'] for f in (precheck_result or {}).get('findings', [])}
        if 'single_trip_capacity' in codes or '超过车队' in str((run.get('raw_result') or {}).get('reason', '')):
            actions.append({'action': 'allow_multi_trip', 'needs_user': True, 'why': '允许回车库补给后多趟往返'})
            actions.append({'action': 'add_vehicles', 'needs_user': True, 'why': '增加车辆以提高单趟总容量'})
        if 'demand_above_capacity' in codes:
            actions.append({'action': 'raise_capacity', 'needs_user': True, 'why': '提高单车容量或拆分超量任务点'})
        if codes & {'task_inside_obstacle', 'task_outside_field'} or run.get('state') == 'error':
            actions.append({'action': 'edit_map', 'needs_user': True, 'why': '在地图上移动或删除不可达任务点，或改为自由通行'})
    return actions
