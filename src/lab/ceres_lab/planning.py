"""Problem identity and objective semantics shared by records and comparisons.

These descriptors explain a result; they do not replace its independent checker.
"""
from __future__ import annotations

import copy
import hashlib
import json


def fingerprint(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_frame(problem_class: str, frame: dict):
    if problem_class in ('fleet_routing', 'coverage', 'point_to_point', 'field_operation') and (
            frame.get('kind') not in ('map', 'field') or frame.get('units') != 'm'):
        raise ValueError('该几何规划模型使用米制尺寸：请声明并转换到 map/field 米坐标，不能把无量纲基准坐标当作米')
    if problem_class == 'point_to_point' and (frame.get('kind') != 'map' or not frame.get('world')):
        raise ValueError('当前点到点求解器需要已知仿真地图；尚未适配任意田块或无地图空间')


def context(scenario: dict, instance: dict, result: dict | None = None, *, resolve_map: bool = True) -> dict:
    result = result or {}
    pc = scenario.get('problem_class')
    frame = copy.deepcopy(scenario.get('frame') or {})
    unit = (result.get('objective') or {}).get('unit', scenario.get('cost_unit'))
    if pc == 'fleet_routing':
        rule = instance.get('objective', 'total_distance')
        unit = 's' if rule == 'makespan' else 'm'
        label = '最大单车规划工期（匀速与服务时间代理）' if rule == 'makespan' else '绕障几何总里程'
        service = 'point_visit'
    elif pc == 'euclidean_cvrp':
        rule = instance.get('objective', 'sum_unrounded_euclidean_distance')
        label = '逐边 EUC_2D 取整距离之和' if rule == 'sum_rounded_euclidean_distance' else '未取整欧氏距离之和'
        service = 'point_visit'
    elif pc == 'turn_routing':
        rule = 'directed_arc_cost_plus_turn_penalty'
        label, service = '有向路段成本与转弯惩罚之和', 'point_visit'
    elif pc == 'field_operation':
        rule = instance.get('objective', 'nonworking')
        unit = 's' if rule == 'makespan' else 'm'
        label, service = ('最晚完成时间（含转弯、转场与补给）' if rule == 'makespan' else '非作业里程（转弯、转场与补给往返）'), 'line_service'
    elif pc == 'coverage':
        rule = 'working_and_transit_geometry_length'
        label, service = '作业与非作业几何总长度', 'area_coverage'
    else:
        rule = 'geometry_path_length'
        label, service = '起终点之间的几何路径长度', 'navigation'
    metrics = result.get('metrics') or {}
    relaxed = []
    if pc == 'coverage' and instance.get('turn_radius', 0) > 0 and metrics.get('turn_radius_enforced') is False:
        relaxed.append('minimum_turn_radius')
    environment = None
    if frame.get('kind') == 'map' and resolve_map:
        from .scenarios import maps
        environment = maps.fingerprint(frame['world'])
    identity = dict(problem_class=pc, instance_sha256=fingerprint(instance), frame=frame, environment_sha256=environment,
                    objective_rule=rule, objective_unit=unit, relaxed_constraints=relaxed)
    if pc == 'fleet_routing' and instance.get('ground'):
        policy = metrics.get('ground_policy', result.get('params', {}).get('ground_policy', 'ignore'))
        weight = metrics.get('ground_risk_weight', result.get('params', {}).get('ground_risk_weight', 1.))
        identity['ground_policy'] = {'policy': policy, **({'weight': weight} if policy == 'risk_adjust' else {})}
        if policy == 'risk_adjust':
            label += f'（地面风险偏好搜索，权重 {weight:g}；风险当量米另列）'
    if pc == 'coverage':
        identity['validation_policy'] = 'urp.coverage-interior90/2'
    return dict(schema='urp.planning/1', service_kind=service, comparison_key=fingerprint(identity),
                **identity, objective_label=label, axes='x right / y up', angle_unit='rad',
                route_semantics='solver_geometry; task sequence alone is not a collision-free path',
                metric_scope='planning; execution records have a separate clock and trajectory')


def comparison_key(run: dict) -> str:
    """Use the complete instance hash and semantics, including old self-contained records."""
    instance = run.get('instance')
    frozen = run.get('planning') or {}
    if (isinstance(instance, dict) and frozen.get('schema') == 'urp.planning/1'
            and not instance.get('ground')
            and frozen.get('instance_sha256') == fingerprint(instance)
            and frozen.get('objective_unit') == (run.get('objective') or {}).get('unit')):
        return frozen['comparison_key']
    scenario = run.get('scenario_snapshot') or run
    if isinstance(instance, dict):
        scenario = {**scenario, 'problem_class': run.get('problem_class', scenario.get('problem_class')),
                    'frame': run.get('frame', scenario.get('frame')),
                    'cost_unit': (run.get('objective') or {}).get('unit', scenario.get('cost_unit'))}
        # Missing historical map hashes stay unknown; reading an archive must
        # neither require ROS files nor silently substitute today's map.
        return context(scenario, instance, run, resolve_map=False)['comparison_key']
    # Legacy summaries cannot reconstruct the model; at least isolate objective
    # units/problem class/frame, and never shorten the recorded input hash.
    return fingerprint({k: run.get(k) for k in ('scenario_id', 'instance_sha256', 'problem_class', 'frame')}
                       | {'unit': (run.get('objective') or {}).get('unit')})


def trace_context(pc: str, instance: dict) -> dict:
    exact_objective = pc == 'euclidean_cvrp' and instance.get('objective') == 'sum_rounded_euclidean_distance'
    return dict(schema='urp.search-trace/1',
                semantic='reported_objective' if exact_objective else 'solver_search_proxy',
                note='搜索中的可行解目标；最终值仍以独立复算为准' if exact_objective else
                     '求解器内部代理成本，可能含距离量化、方向松弛或加权跨度；不能作为最终验算目标或跨算法增益',
                independent_check_at_each_sample=False)


def requires_coverage_recheck(run: dict) -> bool:
    """Keep historical output, but do not reuse the truncated full-pipeline geometry."""
    return ((run.get('algorithm') or {}).get('id') == 'f2c.full'
            and (run.get('raw_result') or {}).get('adapter_contract') != 'urp.f2c-full/2')


def without_stale_reference(scenario: dict, instance: dict) -> dict:
    """An edited instance cannot inherit the original benchmark's BKS."""
    out = copy.deepcopy(scenario)
    if out.get('reference'):
        expected = out['reference'].get('instance_sha256') or fingerprint(out.get('instance') or {})
        if fingerprint(instance) != expected:
            old = out.pop('reference')
            out['provenance'] = {**out.get('provenance', {}),
                                 'reference_invalidated': {'reason': '规划输入已改变，原 BKS 不再适用', 'original': old}}
        else:
            out['reference']['instance_sha256'] = expected
    return out
