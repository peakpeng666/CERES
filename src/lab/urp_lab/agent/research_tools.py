"""Workbench tools for the research agent (CONTRACT §9.2).

Each tool is a thin wrapper over the internals that the /lab/api endpoints already use, so the agent
and the panel see the same scenarios, catalogue, checks and run records. Results are summaries that
keep every run/scenario/study id, so the panel can always open the full record.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..jobs import FINAL
from ..scenarios.validate import PROBLEM_CLASSES
from ..services import ServiceError
from ..solve_request import SolveRequestError, check_label, resolve_algorithms

TIERS = ('read', 'compute', 'actuate')
MAX_SOLVE_ALGORITHMS = 6
MAX_STUDY_RUNS = 60
MAX_WAIT_S = 120.0
SPEC_KEYS = {'kind', 'size', 'shape', 'plots', 'tasks', 'distribution', 'vehicles', 'capacity', 'obstacles',
             'depot', 'objective', 'speed_mps', 'robot_width'}


class ToolError(Exception):
    """A tool refused or failed; the message goes back to the model, which may correct its call."""


@dataclass
class ToolContext:
    lab: Any
    session_id: str
    cancelled: Callable[[], bool] = lambda: False


@dataclass(frozen=True)
class Tool:
    name: str
    tier: str
    label: str
    description: str
    parameters: dict
    handler: Callable[[ToolContext, dict], dict]

    def schema(self) -> dict:
        return {'type': 'function', 'function': {'name': self.name, 'description': self.description,
                                                 'parameters': self.parameters}}

    def public(self) -> dict:
        return {'name': self.name, 'tier': self.tier, 'label': self.label, 'description': self.description}


def _obj(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


STR = {'type': 'string'}
IDS = {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 24}


# ----------------------------------------------------------------- helpers --
def _int(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f'{key} 须为数字')
    return max(lo, min(hi, int(value)))


def _str(args: dict, key: str, required: bool = True, limit: int = 600) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f'缺少 {key}')
    return value.strip()[:limit]


def _scenario(scenario_id: str) -> dict:
    from .. import scenarios
    try:
        return scenarios.get_scenario(scenario_id)
    except KeyError:
        raise ToolError(f'没有场景 {scenario_id}；先用 list_scenarios 查找或用 compose_scenario 生成') from None


def _scalar_metrics(metrics: dict | None, limit: int = 20) -> dict:
    out = {}
    for key, value in (metrics or {}).items():
        if isinstance(value, bool) or isinstance(value, (str, int)) or (isinstance(value, float) and math.isfinite(value)):
            out[key] = round(value, 4) if isinstance(value, float) else value
        if len(out) >= limit:
            break
    return out


def run_brief(run: dict) -> dict:
    check = run.get('check') or None
    map_check = run.get('map_check') or None
    objective = run.get('objective') or {}
    return {
        'run_id': run.get('run_id'), 'job_id': run.get('job_id'), 'scenario_id': run.get('scenario_id'),
        'problem_class': run.get('problem_class'),
        'algorithm': {k: (run.get('algorithm') or {}).get(k) for k in ('id', 'name', 'source', 'version')},
        'params': run.get('params'), 'state': run.get('state'), 'status': run.get('status'),
        'objective': {'value': objective.get('value'), 'unit': objective.get('unit')},
        'check': None if check is None else {'valid': check.get('valid'), 'errors': (check.get('errors') or [])[:3],
                                              'checker': check.get('checker')},
        'map_check': None if map_check is None else {k: map_check.get(k) for k in ('checked', 'collision_free', 'min_clearance_m')},
        'metrics': _scalar_metrics(run.get('metrics')),
        'error': run.get('error'), 'finished_utc': run.get('finished_utc'),
    }


def _instance_overview(sc: dict) -> dict:
    inst, pc = sc.get('instance') or {}, sc.get('problem_class')
    if pc == 'fleet_routing':
        vehicles = inst.get('vehicles') or {}
        xs = [p[0] for p in inst.get('boundary') or []]
        ys = [p[1] for p in inst.get('boundary') or []]
        return {'tasks': len(inst.get('tasks') or []), 'demand_total': sum(t.get('demand', 0) for t in inst.get('tasks') or []),
                'vehicles': {k: vehicles.get(k) for k in ('count', 'capacity', 'speed_mps', 'trips') if k in vehicles},
                'vehicle_classes': len(vehicles.get('classes') or []) or 1, 'objective': inst.get('objective'),
                'obstacles': len(inst.get('obstacles') or []), 'plots': len(inst.get('plots') or []),
                'bbox_m': [round(max(xs) - min(xs), 1), round(max(ys) - min(ys), 1)] if xs and ys else None}
    if pc == 'euclidean_cvrp':
        return {'customers': max(0, len(inst.get('coordinates') or []) - 1), 'capacity': inst.get('capacity'),
                'objective': inst.get('objective'), 'fleet': inst.get('fleet')}
    if pc == 'turn_routing':
        return {'customers': len(inst.get('customers') or []), 'capacity': inst.get('capacity'),
                'arcs': len(inst.get('arcs') or []), 'fleet_limit': inst.get('fleet_limit')}
    if pc == 'coverage':
        return {k: inst.get(k) for k in ('headland_width', 'swath_width', 'turn_radius', 'robot_width', 'route_pattern')} | {
            'field_vertices': len(inst.get('field') or []), 'obstacles': len(inst.get('obstacles') or [])}
    return {'keys': sorted(inst)[:20]}


def _get_runs(ctx: ToolContext, run_ids: list[str]) -> list[dict]:
    runs = {r['run_id']: r for r in ctx.lab.store.runs_by_ids([str(i) for i in run_ids])}
    missing = [i for i in run_ids if i not in runs]
    if missing:
        raise ToolError(f'没有运行记录：{", ".join(missing[:5])}')
    return [runs[i] for i in run_ids]


def _analysis(runs: list[dict]) -> dict:
    from .. import analysis
    result = analysis.analyze(runs, 0.05)
    if result.get('empty'):
        return {'empty': True, 'n_runs': 0}
    keep = ('algorithm', 'id', 'name', 'instances', 'runs', 'successes', 'success_rate', 'gap_mean', 'gap_median',
            'gap_best', 'time_median', 'mean_rank', 'wins')
    rows = [{k: row.get(k) for k in keep} for row in result.get('summary') or []]
    block = result.get('block_contract') or {}
    block_summary = {k: block.get(k) for k in ('schema', 'n_comparison_blocks', 'n_complete_blocks',
                    'n_declared_groups', 'alias_blocks_collapsed', 'inference_eligible', 'reason_code', 'note')}
    friedman = (result.get('tests') or {}).get('friedman') or {}
    return {'n_runs': result.get('n_runs', len(runs)), 'n_instances': result.get('n_instances'),
            'n_complete_instances': result.get('n_complete_instances'), 'algorithms': rows,
            'block_contract': block_summary,
            'friedman': {k: friedman.get(k) for k in ('status', 'testable', 'reason_code', 'reason', 'p_value', 'significant')},
            'reference_note': result.get('reference_note'),
            'note': 'gap 为相对参照的百分比差距（1.5 即 1.5%）；成功指运行结束且通过独立检查'}


# ------------------------------------------------------------------- read --
def list_scenarios(ctx, args):
    from .. import scenarios
    pc, query = args.get('problem_class'), (args.get('query') or '').strip().lower()
    limit = _int(args, 'limit', 20, 1, 50)
    rows = []
    for s in scenarios.list_scenarios():
        if pc and s.get('problem_class') != pc:
            continue
        if query and query not in ' '.join(str(s.get(k) or '') for k in ('id', 'title', 'summary')).lower():
            continue
        rows.append({'id': s['id'], 'title': s.get('title'), 'summary': s.get('summary'), 'problem_class': s.get('problem_class'),
                     'task_count': s.get('task_count'), 'units': (s.get('frame') or {}).get('units'), 'origin': s.get('origin')})
    return {'total': len(rows), 'scenarios': rows[-limit:] if not query else rows[:limit],
            'note': '未给 query 时返回最近的场景' if not query else None}


def get_scenario(ctx, args):
    from .. import scenarios
    sc = _scenario(_str(args, 'scenario_id'))
    summary = scenarios.summary(sc)
    return {**{k: summary.get(k) for k in ('id', 'title', 'summary', 'problem_class', 'frame', 'cost_unit', 'task_count', 'origin', 'bks')},
            'sim_supported': (sc.get('sim') or {}).get('supported'), 'instance': _instance_overview(sc),
            'compose': {k: (sc.get('compose') or {}).get(k) for k in ('text', 'seed')} if sc.get('compose') else None}


def parse_task(ctx, args):
    from ..compose import describe
    from ..compose.nl import fill_defaults, parse
    parsed = parse(_str(args, 'text'))
    spec, defaults = fill_defaults(parsed['spec'])
    return {'spec': spec, 'found': parsed['found'], 'defaults': defaults, 'cards': describe(spec),
            'note': '规则解析结果；found 以外的字段为默认值，可在 compose_scenario 的 overrides 里修正'}


def list_algorithms(ctx, args):
    pc = args.get('problem_class')
    rows = []
    for a in ctx.lab.catalog():
        if pc and pc not in a.get('problem_classes', []):
            continue
        rows.append({'id': a['id'], 'name': a.get('name'), 'problem_classes': a.get('problem_classes'), 'family': a.get('family'),
                     'source': a.get('source'), 'origin': a.get('origin'), 'guarantee': a.get('guarantee'),
                     'available': a.get('available'), 'unavailable_reason': a.get('unavailable_reason'), 'limits': a.get('limits'),
                     'randomness': a.get('randomness'),
                     'params': [{k: p.get(k) for k in ('name', 'type', 'default', 'min', 'max', 'options', 'label', 'description')} for p in a.get('params') or []]})
    return {'algorithms': rows}


def recommend(ctx, args):
    from .. import recommend as rec
    sc = _scenario(_str(args, 'scenario_id'))
    out = rec.recommend(sc, sc['instance'])
    learned = out.get('learned_selector') or {}
    return {'policy': out.get('policy'), 'recommended': out.get('recommended'), 'explanation': out.get('explanation'),
            'features': [{k: f.get(k) for k in ('name', 'key', 'value', 'unit')} for f in out.get('features') or []],
            'ranking': [{k: r.get(k) for k in ('algorithm_id', 'rank', 'suitability', 'reasons', 'suggested_params')}
                        for r in (out.get('ranking') or [])[:6]],
            'learned_selector': {k: learned.get(k) for k in ('id', 'status', 'reason')},
            'note': 'policy 以 rules- 开头时是规则建议，不是学习模型；收益需在同实例同预算的对比求解中验证'}


def validate_planning(ctx, args):
    from ..planning import context, validate_frame
    sc = _scenario(_str(args, 'scenario_id'))
    try:
        validate_frame(sc['problem_class'], sc.get('frame') or {})
    except ValueError as exc:
        return {'valid': False, 'reason': str(exc)}
    planning = context(sc, sc['instance'])
    return {'valid': True, 'planning': {k: v for k, v in planning.items() if not isinstance(v, (list, dict)) or k in ('objective', 'frame')}}


def get_runs(ctx, args):
    return {'runs': [run_brief(r) for r in _get_runs(ctx, args.get('run_ids') or [])]}


def list_runs(ctx, args):
    limit = _int(args, 'limit', 10, 1, 20)
    runs = ctx.lab.store.list_runs(scenario_id=_str(args, 'scenario_id', required=False), limit=limit)
    return {'runs': [run_brief(r) for r in runs]}


def compare_runs(ctx, args):
    runs = _get_runs(ctx, args.get('run_ids') or [])
    if any(r.get('state') not in FINAL for r in runs):
        raise ToolError('还有运行未结束；先调用 wait_for_runs')
    return _analysis(runs)


def list_fields(ctx, args):
    from ..library import fields
    status = fields.status()
    rows = fields.listing(args.get('country'), (args.get('query') or None))
    limit = _int(args, 'limit', 10, 1, 30)
    return {'status': status, 'total': len(rows), 'fields': rows[:limit]}


def get_study(ctx, args):
    study = ctx.lab.studies.get(_str(args, 'study_id'))
    if study is None:
        raise ToolError('没有这个课题')
    return {k: study.get(k) for k in ('study_id', 'title', 'spec', 'skipped', 'progress', 'created_utc')}


def study_analysis(ctx, args):
    study_id = _str(args, 'study_id')
    if ctx.lab.studies.get(study_id) is None:
        raise ToolError('没有这个课题')
    runs = ctx.lab.studies.runs(study_id)
    if any(r.get('state') not in FINAL for r in runs):
        raise ToolError('课题还有运行未结束；先用 get_study 查看进度')
    return _analysis(runs)


# ---------------------------------------------------------------- compute --
def _overrides(raw) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ToolError('overrides 须为对象')
    unknown = sorted(set(raw) - SPEC_KEYS)
    if unknown:
        raise ToolError(f'overrides 不支持这些字段：{", ".join(unknown)}；可用 {", ".join(sorted(SPEC_KEYS))}')
    return raw


def compose_scenario(ctx, args):
    from .. import compose, scenarios
    text, seed = _str(args, 'text'), args.get('seed')
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 10**6):
        raise ToolError('seed 须为 0–999999 的整数')
    try:
        if args.get('field_id'):
            if args.get('overrides'):
                raise ToolError('真实田块模式不接受 overrides，请把要求写进 text')
            sc = compose.from_field(_str(args, 'field_id'), text, seed)
        else:
            sc = compose.compose(text, seed, _overrides(args.get('overrides')) or None)
        saved = scenarios.save_user_scenario(sc, overwrite=False)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise ToolError(f'无法生成任务地图：{exc}') from None
    return {'scenario_id': saved['id'], 'title': saved.get('title'), 'summary': saved.get('summary'),
            'problem_class': saved.get('problem_class'), 'cards': (saved.get('compose') or {}).get('cards'),
            'seed': (saved.get('compose') or {}).get('seed'), 'instance': _instance_overview(saved)}


def solve(ctx, args):
    sc = _scenario(_str(args, 'scenario_id'))
    requested = args.get('algorithms')
    if isinstance(requested, list) and len(requested) > MAX_SOLVE_ALGORITHMS:
        raise ToolError(f'智能体一次最多提交 {MAX_SOLVE_ALGORITHMS} 个算法')
    try:
        label = check_label(args.get('label') or f'agent:{ctx.session_id}')
        resolved = resolve_algorithms(ctx.lab.catalog(), sc['problem_class'], requested, MAX_SOLVE_ALGORITHMS)
    except SolveRequestError as exc:
        raise ToolError(exc.message) from None
    try:
        job_id, run_ids = ctx.lab.jobs.submit(sc, sc['instance'], resolved, label)
    except RuntimeError as exc:  # SourceChangedError or shutting down
        raise ToolError(str(exc)) from None
    return {'job_id': job_id, 'run_ids': run_ids, 'scenario_id': sc['id'],
            'algorithms': [{'id': a['id'], 'params': p} for a, p in resolved],
            'note': '运行已排队；用 wait_for_runs 等待结果'}


def wait_for_runs(ctx, args):
    run_ids = [str(i) for i in args.get('run_ids') or []]
    if not run_ids:
        raise ToolError('缺少 run_ids')
    timeout = min(MAX_WAIT_S, max(1.0, float(args.get('timeout_s') or 60)))
    deadline = time.monotonic() + timeout
    while True:
        runs = _get_runs(ctx, run_ids)
        done = all(r.get('state') in FINAL for r in runs)
        if done or time.monotonic() >= deadline or ctx.cancelled():
            return {'all_finished': done, 'waited_limit_s': timeout, 'runs': [run_brief(r) for r in runs],
                    'note': None if done else '仍有运行未结束，可再次调用 wait_for_runs'}
        time.sleep(0.25)


def create_study(ctx, args):
    spec = {'title': _str(args, 'title', limit=120), 'scenarios': args.get('scenario_ids') or [],
            'algorithms': args.get('algorithms') or [], 'budgets': args.get('budgets') or [],
            'seeds': args.get('seeds') or [], 'note': f'由智能体会话 {ctx.session_id} 建立'}
    from .. import scenarios
    try:
        cells, _ = ctx.lab.studies.plan(spec, scenarios.get_scenario, ctx.lab.catalog())
    except KeyError as exc:
        raise ToolError(f'没有场景 {exc}') from None
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if len(cells) > MAX_STUDY_RUNS:
        raise ToolError(f'课题共 {len(cells)} 次运行，超过智能体上限 {MAX_STUDY_RUNS}；减少场景、算法、预算或种子')
    study = ctx.lab.studies.create(spec, scenarios.get_scenario, ctx.lab.catalog())
    return {'study_id': study['study_id'], 'title': study['title'], 'progress': study.get('progress'),
            'skipped': study.get('skipped'), 'note': '用 get_study 查看进度，全部结束后用 study_analysis 汇总'}


# ---------------------------------------------------------------- actuate --
def start_field_simulation(ctx, args):
    body = {'run_id': _str(args, 'run_id')}
    if args.get('real_time_factor') is not None:
        body['real_time_factor'] = args['real_time_factor']
    try:
        out = ctx.lab.fieldsim.start(body)
    except ServiceError as exc:
        raise ToolError(f'{exc.code}：{exc.message}') from None
    return {'session_id': out.get('session_id'), 'run_id': body['run_id'], 'phase': out.get('phase'),
            'note': '田间仿真已启动；结果在“田间仿真”面板与 /lab/api/fieldsim/results 中查看'}


PC = {'type': 'string', 'enum': list(PROBLEM_CLASSES)}
ALGOS = {'type': 'array', 'minItems': 1, 'maxItems': MAX_SOLVE_ALGORITHMS,
         'items': {'type': 'object', 'properties': {'id': STR, 'params': {'type': 'object'}}, 'required': ['id']}}

TOOLS: list[Tool] = [
    Tool('list_scenarios', 'read', '查找场景', '列出工作台里的场景（内置、用户生成、已打开的 CVRPLIB 与真实田块），可按问题类型和关键词过滤。',
         _obj({'problem_class': PC, 'query': STR, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}), list_scenarios),
    Tool('get_scenario', 'read', '查看场景', '读取一个场景的摘要、坐标系与单位、任务规模、车队和目标。',
         _obj({'scenario_id': STR}, ('scenario_id',)), get_scenario),
    Tool('parse_task', 'read', '解析任务描述', '用工作台的规则解析器把一句话任务描述解析成结构化参数，不生成地图。',
         _obj({'text': STR}, ('text',)), parse_task),
    Tool('list_algorithms', 'read', '查看算法库', '列出算法目录：适用问题、来源、最优性保证、是否可用和可调参数。',
         _obj({'problem_class': PC}), list_algorithms),
    Tool('recommend', 'read', '规则推荐', '对场景给出规则推荐、实例特征和候选排序（学习选择器仅在已评估范围内启用，policy 字段注明实际策略）。',
         _obj({'scenario_id': STR}, ('scenario_id',)), recommend),
    Tool('validate_planning', 'read', '检查规划输入', '检查场景的坐标系、单位与规划目标口径是否可以求解。',
         _obj({'scenario_id': STR}, ('scenario_id',)), validate_planning),
    Tool('get_runs', 'read', '读取运行结果', '读取运行记录的状态、目标值、独立检查、地图净空和主要指标。',
         _obj({'run_ids': IDS}, ('run_ids',)), get_runs),
    Tool('list_runs', 'read', '最近的运行', '列出最近的运行记录，可限定场景。',
         _obj({'scenario_id': STR, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}), list_runs),
    Tool('compare_runs', 'read', '对比运行', '对已结束的一组运行做统计对比：成功率、相对本批最优的差距、耗时、名次和胜场。',
         _obj({'run_ids': IDS}, ('run_ids',)), compare_runs),
    Tool('list_fields', 'read', '查找真实田块', '列出 Fields2Benchmark 真实田块（需已下载数据）。',
         _obj({'country': {'type': 'string', 'enum': ['nl', 'ee', 'lt']}, 'query': STR,
               'limit': {'type': 'integer', 'minimum': 1, 'maximum': 30}}), list_fields),
    Tool('get_study', 'read', '查看课题', '查看批量课题的设计、跳过项和进度。', _obj({'study_id': STR}, ('study_id',)), get_study),
    Tool('study_analysis', 'read', '课题统计', '课题全部运行结束后做统计对比。', _obj({'study_id': STR}, ('study_id',)), study_analysis),
    Tool('compose_scenario', 'compute', '生成任务地图', '按一句话描述生成并保存一个多车农田任务地图；可用 overrides 修正解析出的参数，或给 field_id 使用真实田块边界。',
         _obj({'text': STR, 'seed': {'type': 'integer', 'minimum': 0, 'maximum': 999999}, 'field_id': STR,
               'overrides': {'type': 'object', 'description': '可用字段：' + '、'.join(sorted(SPEC_KEYS))}}, ('text',)), compose_scenario),
    Tool('solve', 'compute', '提交求解', f'在场景上提交求解（最多 {MAX_SOLVE_ALGORITHMS} 个算法，可设 params，如 time_limit_s）。每个运行都由独立检查器验算。',
         _obj({'scenario_id': STR, 'algorithms': ALGOS, 'label': STR}, ('scenario_id', 'algorithms')), solve),
    Tool('wait_for_runs', 'compute', '等待结果', f'等待运行结束（最多 {MAX_WAIT_S:g} 秒），返回每个运行的结果摘要。',
         _obj({'run_ids': IDS, 'timeout_s': {'type': 'number', 'minimum': 1, 'maximum': MAX_WAIT_S}}, ('run_ids',)), wait_for_runs),
    Tool('create_study', 'compute', '建立课题', f'建立批量课题：场景 × 算法 × 时间预算；仅使用 seed 的算法按种子展开，不使用 seed 的算法每格运行一次（不是普通重复）（智能体上限 {MAX_STUDY_RUNS} 次运行）。',
         _obj({'title': STR, 'scenario_ids': {'type': 'array', 'items': STR, 'minItems': 1, 'maxItems': 5}, 'algorithms': ALGOS,
               'budgets': {'type': 'array', 'items': {'type': 'number'}, 'maxItems': 4},
               'seeds': {'type': 'array', 'items': {'type': 'integer'}, 'maxItems': 5}}, ('title', 'scenario_ids', 'algorithms')), create_study),
    Tool('start_field_simulation', 'actuate', '启动田间仿真', '在 Gazebo 中执行一个已完成且通过检查的多车方案。需要用户批准，并且需要本机 ROS 2 / Gazebo。',
         _obj({'run_id': STR, 'real_time_factor': {'type': 'number', 'minimum': 0.5, 'maximum': 20}}, ('run_id',)), start_field_simulation),
]

BY_NAME = {t.name: t for t in TOOLS}
assert len(BY_NAME) == len(TOOLS) and all(t.tier in TIERS for t in TOOLS)
