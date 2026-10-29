"""Tool registry: JSON-schema arguments, policy gates and handlers over the existing workbench services.

Handlers return JSON-native dicts built from real service results. A handler raises ``ToolError`` for a refused or
failed call; the runner records it and shows the model the error, so recovery is visible in the call log.
Effects: ``session`` (agent state only), ``compute`` (solver/checker processes), ``write`` (stored scenario),
``physical`` (Gazebo session; always requires explicit user approval).
"""
from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import contract as tc
from . import diagnose


class ToolError(Exception):
    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data or {}


@dataclass
class Tool:
    name: str
    label: str
    description: str
    parameters: dict
    handler: Callable[['ToolContext', dict], dict]
    effect: str = 'compute'
    step: str | None = None                 # plan step this tool advances
    needs_scenario: bool = False
    needs_confirmed: bool = False

    def spec(self) -> dict:
        return {'name': self.name, 'description': self.description, 'parameters': self.parameters}


@dataclass
class ToolContext:
    lab: Any
    session: dict
    cancel: threading.Event = field(default_factory=threading.Event)
    emit: Callable[[str, dict], None] = lambda kind, data: None


# ------------------------------------------------------------ validation --
_TYPES = {'object': dict, 'array': list, 'string': str, 'boolean': bool}


def validate(schema: dict, value, path: str = '参数') -> list[str]:
    """Small JSON-schema subset: type, enum, bounds, items, properties, required, additionalProperties."""
    errors: list[str] = []
    t = schema.get('type')
    if t in ('integer', 'number'):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (t == 'integer' and float(value) != int(value)):
            return [f'{path} 须为{"整数" if t == "integer" else "数值"}']
        if 'minimum' in schema and value < schema['minimum']:
            errors.append(f'{path} 须 ≥ {schema["minimum"]}')
        if 'maximum' in schema and value > schema['maximum']:
            errors.append(f'{path} 须 ≤ {schema["maximum"]}')
    elif t in _TYPES:
        if not isinstance(value, _TYPES[t]) or (t != 'boolean' and isinstance(value, bool)):
            return [f'{path} 类型应为 {t}']
    if 'enum' in schema and value not in schema['enum']:
        errors.append(f'{path} 须为 {schema["enum"]} 之一')
    if t == 'string' and isinstance(value, str) and len(value) > schema.get('maxLength', 2000):
        errors.append(f'{path} 过长')
    if t == 'array':
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 10_000):
            errors.append(f'{path} 元素个数须在 {schema.get("minItems", 0)}–{schema.get("maxItems", "∞")}')
        if 'items' in schema:
            for i, item in enumerate(value[:200]):
                errors += validate(schema['items'], item, f'{path}[{i}]')
    if t == 'object':
        props = schema.get('properties', {})
        for key in schema.get('required', []):
            if key not in value:
                errors.append(f'{path}.{key} 缺失')
        extra = schema.get('additionalProperties', True)
        for key, item in value.items():
            if key in props:
                errors += validate(props[key], item, f'{path}.{key}')
            elif extra is False:
                errors.append(f'{path} 不接受字段 {key}')
            elif isinstance(extra, dict):
                errors += validate(extra, item, f'{path}.{key}')
    return errors


def digest(result: Any, limit: int = 6000) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '…（已截断）'


# ---------------------------------------------------------------- helpers --
def _scenario(ctx: ToolContext) -> dict:
    from ..scenarios import get_scenario
    sid = ctx.session.get('scenario_id')
    if not sid:
        raise ToolError('no_scenario', '尚未建立任务地图；请先调用 build_scenario')
    try:
        return get_scenario(sid)
    except KeyError:
        raise ToolError('no_scenario', f'任务地图 {sid} 不存在') from None


def _catalog(ctx: ToolContext) -> list[dict]:
    return [a for a in ctx.lab.catalog() if 'fleet_routing' in a.get('problem_classes', [])]


def _run(ctx: ToolContext, run_id: str) -> dict:
    if run_id not in ctx.session.get('run_ids', []):
        raise ToolError('unknown_run', f'运行 {run_id} 不属于本会话')
    run = ctx.lab.store.get_run(run_id)
    if run is None:
        raise ToolError('unknown_run', f'没有运行记录 {run_id}')
    return run


def run_summary(run: dict, pre: dict | None = None) -> dict:
    m = run.get('metrics') or {}
    cause = diagnose.classify(run, pre)
    return {
        'run_id': run['run_id'], 'algorithm_id': run['algorithm']['id'], 'algorithm': run['algorithm'].get('name'),
        'state': run.get('state'), 'status': run.get('status'), 'check_valid': (run.get('check') or {}).get('valid'),
        'objective': (run.get('objective') or {}).get('value'), 'objective_unit': (run.get('objective') or {}).get('unit'),
        'total_distance_m': m.get('total_distance_m'), 'makespan_s': m.get('makespan_s'),
        'vehicles_used': m.get('vehicles_used'), 'trips': m.get('trips'), 'wall_s': m.get('wall_s'),
        'errors': [str(e) for e in ((run.get('check') or {}).get('errors') or [])][:3] + ([str(run['error'])[:300]] if run.get('error') else []),
        'diagnosis': cause['category'], 'diagnosis_label': diagnose.CATEGORY_LABEL.get(cause['category'], cause['category']),
        'params': run.get('params'),
    }


GEOMETRY_FIELDS = {'task_kind', 'field_size_m', 'field_area_m2', 'field_shape', 'real_field_id', 'plot_count', 'task_count',
                   'task_distribution', 'obstacles', 'depot_location', 'seed'}


# ---------------------------------------------------------------- handlers --
def update_task_contract(ctx: ToolContext, args: dict) -> dict:
    current = ctx.session['contract']
    new, accepted, rejected = tc.apply(current, args.get('fields') or {}, 'llm', args.get('evidence') or {}, '模型理解')
    new = tc.add_unsupported(new, args.get('unsupported') or [])
    ctx.session['contract'] = new
    return {'accepted': accepted, 'rejected': rejected, 'missing': [tc.FIELDS[k].label for k in tc.missing(new)],
            'unsupported': new['unsupported'], 'status': new['status'], 'revision': new['revision']}


def ask_user(ctx: ToolContext, args: dict) -> dict:
    questions = [{'question': str(q.get('question', ''))[:300], 'field': q.get('field'),
                  'options': [str(o)[:60] for o in (q.get('options') or [])][:6]} for q in args['questions']]
    ctx.session['pending'] = {'type': 'questions', 'questions': questions, 'reason': str(args.get('reason') or '')[:300]}
    return {'status': 'awaiting_user', 'questions': len(questions)}


def propose_task_change(ctx: ToolContext, args: dict) -> dict:
    values = args['fields']
    probe, accepted, rejected = tc.apply(ctx.session['contract'], values, 'repair')
    if rejected:
        raise ToolError('invalid_change', '；'.join(r['reason'] for r in rejected))
    if not accepted:
        raise ToolError('no_change', '提议与当前任务相同')
    ctx.session['pending'] = {'type': 'approval', 'kind': 'contract_change', 'fields': values,
                              'changes': accepted, 'reason': str(args.get('reason') or '')[:300]}
    return {'status': 'awaiting_approval', 'changes': accepted}


def find_real_fields(ctx: ToolContext, args: dict) -> dict:
    from ..library import fields
    if not fields.status().get('downloaded'):
        raise ToolError('library_unavailable', '真实田块库尚未下载；可在「资料 → 真实田块」中获取')
    items = [f for f in fields.listing('nl', (args.get('query') or '').strip().lower() or None) if not tc.sealed_field(f['id'])]
    limit = int(args.get('limit') or 8)
    keep = ('id', 'country_name', 'area_ha', 'size_m', 'vertices', 'holes', 'convexity')
    return {'fields': [{k: f.get(k) for k in keep if k in f} for f in items[:limit]], 'total': len(items),
            'note': '只列出荷兰田块；爱沙尼亚、立陶宛田块属于封存测试集，评测协议冻结前不用于规划运行'}


def build_scenario(ctx: ToolContext, args: dict) -> dict:
    from .. import compose
    from ..scenarios import get_scenario, save_user_scenario
    contract = ctx.session['contract']
    if contract['status'] != 'confirmed':
        raise ToolError('contract_not_confirmed', '任务合同尚未确认；先请用户确认')
    execution = tc.execution(contract)
    overrides = tc.compose_overrides(contract)
    existing = ctx.session.get('scenario_id')
    built_from = ctx.session.get('scenario_contract_fields') or {}
    geometry_changed = any(built_from.get(k) != tc.value(contract, k) for k in GEOMETRY_FIELDS)
    if existing and not geometry_changed:
        sc = get_scenario(existing)
        inst = sc['instance']
        veh = inst['vehicles']
        if tc.value(contract, 'vehicle_count') is not None and int(tc.value(contract, 'vehicle_count')) != veh['count']:
            n = int(tc.value(contract, 'vehicle_count'))
            names = list(veh.get('names') or [])
            veh['names'] = (names + [f'车{i + 1}' for i in range(len(names), n)])[:n]
            veh['count'] = n
        for key, target in (('vehicle_capacity', 'capacity'), ('vehicle_speed_mps', 'speed_mps')):
            if tc.value(contract, key) is not None:
                veh[target] = tc.value(contract, key)
        if tc.value(contract, 'task_demand') is not None:
            for t in inst['tasks']:
                t['demand'] = int(tc.value(contract, 'task_demand'))
        if tc.value(contract, 'objective'):
            inst['objective'] = tc.value(contract, 'objective')
        veh['trips'], veh['travel'] = execution['trip_mode'], execution['travel_mode']
        stored = save_user_scenario(sc, overwrite=True, expected_revision=sc.get('revision'))
        mode = 'updated'
    else:
        text = contract.get('request_text') or '智能体任务'
        if tc.value(contract, 'real_field_id'):
            sc = compose.from_field(tc.value(contract, 'real_field_id'), text, tc.value(contract, 'seed'), overrides)
        else:
            sc = compose.compose(text, tc.value(contract, 'seed'), overrides)
        sc['instance']['vehicles']['trips'] = execution['trip_mode']
        sc['instance']['vehicles']['travel'] = execution['travel_mode']
        sc['provenance']['agent'] = {'session_id': ctx.session['session_id'], 'contract_revision': contract['revision']}
        stored = save_user_scenario(sc, overwrite=False)
        mode = 'replaced' if existing else 'created'
        if existing:
            ctx.session.setdefault('previous_scenarios', []).append(existing)
    ctx.session['scenario_id'] = stored['id']
    ctx.session['scenario_contract_fields'] = {k: tc.value(contract, k) for k in GEOMETRY_FIELDS}
    inst = stored['instance']
    pre = diagnose.precheck(inst)
    ctx.session['precheck'] = pre
    ctx.emit('scenario', {'scenario_id': stored['id']})
    return {'scenario_id': stored['id'], 'mode': mode, 'title': stored['title'], 'summary': stored['summary'],
            'units': stored['frame'].get('units'), 'tasks': len(inst['tasks']), 'vehicles': inst['vehicles']['count'],
            'capacity': inst['vehicles']['capacity'], 'trips': inst['vehicles'].get('trips'),
            'travel': inst['vehicles'].get('travel'), 'obstacles': len(inst.get('obstacles') or []),
            'objective': inst.get('objective'), 'precheck': pre}


def list_algorithms(ctx: ToolContext, args: dict) -> dict:
    out = []
    for a in _catalog(ctx):
        out.append({'id': a['id'], 'name': a['name'], 'library': a.get('library'), 'family': a.get('family'),
                    'guarantee': a.get('guarantee'), 'available': a.get('available'),
                    'unavailable_reason': a.get('unavailable_reason'), 'summary': a.get('summary', '')[:160],
                    'params': [p['name'] for p in a.get('params', [])]})
    return {'algorithms': out}


def recommend_algorithms(ctx: ToolContext, args: dict) -> dict:
    from ..recommend import recommend
    sc = _scenario(ctx)
    rec = recommend(sc, sc['instance'])
    ctx.session['recommendation'] = {'recommended': rec.get('recommended'), 'compare': rec.get('compare'), 'policy': rec.get('policy')}
    ranking = [{'algorithm_id': r['algorithm_id'], 'rank': r.get('rank'), 'applicable': r.get('applicable'),
                'suitability': r.get('suitability'), 'reasons': (r.get('reasons') or r.get('why') or [])[:3]}
               for r in rec.get('ranking', [])[:8]]
    learned = rec.get('learned_selector') or {}
    return {'policy': rec.get('policy'), 'recommended': rec.get('recommended'), 'compare': rec.get('compare'),
            'explanation': rec.get('explanation'), 'ranking': ranking,
            'features': [{k: f.get(k) for k in ('name', 'value', 'unit')} for f in rec.get('features', [])][:10],
            'learned_selector': {'status': learned.get('status'), 'reason': learned.get('reason')},
            'recommender_kind': '规则推荐（rules-v2）；学习型选择器只在其训练覆盖的问题类型上启用'}


def solve(ctx: ToolContext, args: dict) -> dict:
    from ..jobs import resolve_params
    sc = _scenario(ctx)
    execution = tc.execution(ctx.session['contract'])
    budget = float(args.get('time_limit_s') or execution['time_limit_s'])
    seed = int(args['seed']) if args.get('seed') is not None else int(execution['seed'])
    catalog = {a['id']: a for a in _catalog(ctx)}
    resolved = []
    for aid in dict.fromkeys(args['algorithms']):
        algo = catalog.get(aid)
        if algo is None:
            raise ToolError('unknown_algorithm', f'{aid} 不是可用于多车任务的算法；可先调用 list_algorithms')
        if not algo.get('available'):
            raise ToolError('algorithm_unavailable', f"{algo['name']} 不可用：{algo.get('unavailable_reason') or '未说明'}")
        params = {}
        names = {p['name'] for p in algo.get('params', [])}
        if 'time_limit_s' in names:
            params['time_limit_s'] = budget
        if 'seed' in names:
            params['seed'] = seed
        try:
            resolved.append((algo, resolve_params(algo, params)))
        except ValueError as exc:
            raise ToolError('invalid_params', str(exc)) from None
    from ..jobs import SourceChangedError
    try:
        job_id, run_ids = ctx.lab.jobs.submit(sc, sc['instance'], resolved, f"智能体 {ctx.session['session_id']}")
    except SourceChangedError as exc:
        raise ToolError('source_changed', f'执行环境与网关启动时不一致，求解被拒绝：{exc}') from None
    except RuntimeError as exc:
        raise ToolError('shutting_down', f'求解服务正在关闭：{exc}') from None
    ctx.session.setdefault('run_ids', []).extend(run_ids)
    ctx.session.setdefault('tried_algorithms', [])
    ctx.session['tried_algorithms'] = list(dict.fromkeys(ctx.session['tried_algorithms'] + [a['id'] for a, _ in resolved]))
    ctx.emit('runs', {'run_ids': run_ids, 'scenario_id': sc['id']})
    deadline = time.monotonic() + budget * max(1, len(resolved)) + 90
    runs = {}
    while time.monotonic() < deadline:
        runs = {rid: ctx.lab.store.get_run(rid) for rid in run_ids}
        if all(r and r['state'] not in ('queued', 'running') for r in runs.values()):
            break
        if ctx.cancel.is_set():
            for rid in run_ids:
                ctx.lab.jobs.cancel(rid)
            raise ToolError('cancelled', '求解已按用户请求取消', {'run_ids': run_ids})
        time.sleep(0.25)
    else:
        raise ToolError('wait_timeout', '等待求解结束超时；运行仍在后台，可稍后用 verify_run 查看', {'run_ids': run_ids})
    pre = ctx.session.get('precheck')
    return {'job_id': job_id, 'time_limit_s': budget, 'seed': seed,
            'runs': [run_summary(runs[rid], pre) for rid in run_ids]}


def verify_run(ctx: ToolContext, args: dict) -> dict:
    run = _run(ctx, args['run_id'])
    sc = _scenario(ctx)
    result = diagnose.recheck(sc, run)
    cause = diagnose.classify(run, ctx.session.get('precheck'))
    if result.get('performed') and not result.get('agrees'):
        cause = {'category': 'implementation_error', 'evidence': ['独立复核与存档判定不一致'] + result.get('errors', [])[:3]}
    ctx.session.setdefault('verifications', {})[run['run_id']] = {'valid': result.get('valid'), 'agrees': result.get('agrees'),
                                                                   'category': cause['category']}
    return {'run_id': run['run_id'], 'recheck': result, 'diagnosis': cause['category'],
            'diagnosis_label': diagnose.CATEGORY_LABEL.get(cause['category']), 'evidence': cause.get('evidence', [])}


def compare_runs(ctx: ToolContext, args: dict) -> dict:
    from ..planning import comparison_key
    ids = args.get('run_ids') or ctx.session.get('run_ids', [])
    runs = [_run(ctx, rid) for rid in ids]
    groups: dict[str, list[dict]] = {}
    for r in runs:
        groups.setdefault(comparison_key(r) if r.get('state') == 'done' else 'unfinished', []).append(r)
    tables = []
    for key, members in groups.items():
        rows = [run_summary(r, ctx.session.get('precheck')) for r in members]
        valid = [row for row in rows if row['status'] == 'feasible' and row['check_valid']]
        best = {}
        if key != 'unfinished' and len(valid) > 1:
            for metric in ('total_distance_m', 'makespan_s', 'wall_s'):
                vals = [row[metric] for row in valid if isinstance(row[metric], (int, float))]
                if vals:
                    best[metric] = min(valid, key=lambda row: row[metric] if isinstance(row[metric], (int, float)) else float('inf'))['run_id']
        tables.append({'comparison_key': key[:16], 'comparable': key != 'unfinished', 'rows': rows, 'best': best})
    return {'groups': tables, 'rule': '只在相同输入、目标、单位与约束的已验证方案之间标记最好值'}


def diagnose_run(ctx: ToolContext, args: dict) -> dict:
    run = _run(ctx, args['run_id'])
    pre = ctx.session.get('precheck') or diagnose.precheck(_scenario(ctx)['instance'])
    cause = diagnose.classify(run, pre)
    budget = float((run.get('params') or {}).get('time_limit_s') or tc.execution(ctx.session['contract'])['time_limit_s'])
    actions = diagnose.repairs(cause['category'], run, pre, _catalog(ctx), set(ctx.session.get('tried_algorithms', [])), budget)
    return {'run_id': run['run_id'], 'diagnosis': cause['category'], 'diagnosis_label': diagnose.CATEGORY_LABEL.get(cause['category']),
            'evidence': cause.get('evidence', []), 'precheck': pre.get('findings', []), 'repairs': actions}


def start_simulation(ctx: ToolContext, args: dict) -> dict:
    run = _run(ctx, args['run_id'])
    verdict = (ctx.session.get('verifications') or {}).get(run['run_id'])
    if not verdict or not verdict.get('valid'):
        raise ToolError('unverified', '执行前须先用 verify_run 独立复核且通过')
    body = {'run_id': run['run_id'], 'real_time_factor': float(args.get('real_time_factor') or 4.0)}
    try:
        state = ctx.lab.fieldsim.start(body)
    except Exception as exc:  # noqa: BLE001 - ServiceError carries a user-facing message
        raise ToolError(getattr(exc, 'code', 'sim_failed'), getattr(exc, 'message', str(exc))) from None
    ctx.session.setdefault('sim_runs', []).append(run['run_id'])
    return {'phase': state.get('phase'), 'session_id': state.get('session_id'), 'run_id': run['run_id'],
            'note': '已启动 Gazebo 田间会话；规划预览、实时仿真与记录回放分别显示'}


def simulation_status(ctx: ToolContext, args: dict) -> dict:
    state = ctx.lab.fieldsim.state()
    keep = ('phase', 'session_id', 'run_id', 'error', 'progress', 'vehicles', 'check')
    return {k: state.get(k) for k in keep if k in state}


# ---------------------------------------------------------------- registry --
RUN_ID = {'type': 'object', 'properties': {'run_id': {'type': 'string', 'maxLength': 80}}, 'required': ['run_id'],
          'additionalProperties': False}
EMPTY = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def registry() -> dict[str, Tool]:
    tools = [
        Tool('update_task_contract', '更新任务合同',
             '把用户原话中的任务要素写入任务合同（单位统一为米、秒、平方米）。只填写本轮读到或被用户改动的字段；'
             '无法用字段表达的要求放进 unsupported。', tc.tool_schema(), update_task_contract, 'session', 'understand'),
        Tool('ask_user', '向用户澄清',
             '关键信息缺失或有歧义时向用户提问（最多 3 个问题，可给出选项）。调用后等待用户回答。',
             {'type': 'object', 'properties': {
                 'questions': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'items': {
                     'type': 'object', 'properties': {'question': {'type': 'string', 'maxLength': 300},
                                                      'field': {'type': 'string', 'enum': list(tc.FIELDS)},
                                                      'options': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 6}},
                     'required': ['question'], 'additionalProperties': False}},
                 'reason': {'type': 'string', 'maxLength': 300}},
              'required': ['questions'], 'additionalProperties': False}, ask_user, 'session', 'understand'),
        Tool('find_real_fields', '检索真实田块', '在 Fields2Benchmark 荷兰真实田块中检索可用作场地边界的田块（按编号片段过滤）。',
             {'type': 'object', 'properties': {'query': {'type': 'string', 'maxLength': 60},
                                               'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}},
              'additionalProperties': False}, find_real_fields, 'session', 'understand'),
        Tool('build_scenario', '建立任务地图',
             '按已确认的任务合同生成或更新任务地图。只改车队、容量、目标时原地更新；改场地、任务点或障碍时另存新地图。',
             EMPTY, build_scenario, 'write', 'scenario', needs_confirmed=True),
        Tool('list_algorithms', '查看算法库', '列出可用于多车任务访问的算法、来源、保证与可用状态。', EMPTY,
             list_algorithms, 'session', 'recommend'),
        Tool('recommend_algorithms', '推荐算法', '根据任务地图特征给出规则推荐排序、理由与建议对比集合。', EMPTY,
             recommend_algorithms, 'compute', 'recommend', needs_scenario=True),
        Tool('solve', '求解对比',
             '用同一输入、预算与种子运行 1–6 个算法并等待结束；返回每个方案的状态、指标与独立检查结论。',
             {'type': 'object', 'properties': {
                 'algorithms': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {'type': 'string', 'maxLength': 80}},
                 'time_limit_s': {'type': 'number', 'minimum': 0.2, 'maximum': 60},
                 'seed': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647}},
              'required': ['algorithms'], 'additionalProperties': False}, solve, 'compute', 'solve', needs_scenario=True),
        Tool('verify_run', '独立复核', '对一个方案重新运行独立检查器：任务恰好服务一次、载重、避障、边界、里程复算及与存档一致性。',
             RUN_ID, verify_run, 'compute', 'verify', needs_scenario=True),
        Tool('compare_runs', '方案对比', '在可比方案之间比较里程、工期与耗时，只在同口径的已验证方案中标记最好值。',
             {'type': 'object', 'properties': {'run_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 24}},
              'additionalProperties': False}, compare_runs, 'session', 'verify'),
        Tool('diagnose_run', '诊断失败', '区分任务不可行、方法不适用、预算不足与软件实现错误，并给出修复动作。',
             RUN_ID, diagnose_run, 'compute', 'repair', needs_scenario=True),
        Tool('propose_task_change', '提议修改任务',
             '当任务本身不可行时，提出修改任务合同的方案（如允许多趟、增加车辆、提高容量），等待用户批准。',
             {'type': 'object', 'properties': {'fields': tc.tool_schema()['properties']['fields'],
                                               'reason': {'type': 'string', 'maxLength': 300}},
              'required': ['fields', 'reason'], 'additionalProperties': False}, propose_task_change, 'session', 'repair'),
        Tool('start_simulation', '启动田间仿真', '把已通过独立复核的方案送入 Gazebo 田间仿真（需要用户批准）。',
             {'type': 'object', 'properties': {'run_id': {'type': 'string', 'maxLength': 80},
                                               'real_time_factor': {'type': 'number', 'minimum': 0.5, 'maximum': 8}},
              'required': ['run_id'], 'additionalProperties': False}, start_simulation, 'physical', 'simulate', needs_scenario=True),
        Tool('simulation_status', '查看仿真状态', '读取当前田间仿真会话的阶段与结果。', EMPTY, simulation_status,
             'session', 'simulate'),
    ]
    return {t.name: t for t in tools}


PHASE_TOOLS = {
    'understand': ['update_task_contract', 'ask_user', 'find_real_fields'],
    'execute': ['build_scenario', 'list_algorithms', 'recommend_algorithms', 'solve', 'verify_run', 'compare_runs',
                'diagnose_run', 'propose_task_change', 'start_simulation', 'simulation_status', 'ask_user'],
}


def execute(tools: dict[str, Tool], ctx: ToolContext, name: str, args: dict) -> dict:
    """Validate and run one call; returns the call record (status ok / error / refused / awaiting_approval)."""
    started = time.monotonic()
    record = {'name': name, 'arguments': copy.deepcopy(args), 'started_utc': _now()}
    tool = tools.get(name)
    try:
        if tool is None:
            raise ToolError('unknown_tool', f'没有工具 {name}')
        record['label'] = tool.label
        errors = validate(tool.parameters, args)
        if errors:
            raise ToolError('invalid_arguments', '；'.join(errors[:6]))
        if tool.needs_confirmed and ctx.session['contract']['status'] != 'confirmed':
            raise ToolError('contract_not_confirmed', '任务合同尚未确认')
        if tool.needs_scenario and not ctx.session.get('scenario_id'):
            raise ToolError('no_scenario', '尚未建立任务地图')
        result = tool.handler(ctx, args)
        record.update(status='ok', result=result)
    except ToolError as exc:
        record.update(status='refused' if exc.code in ('contract_not_confirmed', 'no_scenario', 'unverified',
                                                         'invalid_arguments', 'unknown_tool') else 'error',
                      error={'code': exc.code, 'message': exc.message, **({'data': exc.data} if exc.data else {})})
    except Exception as exc:  # noqa: BLE001 - recorded as an implementation error of the tool itself
        record.update(status='error', error={'code': 'tool_exception', 'message': f'{type(exc).__name__}: {exc}'[:600]})
    record['duration_s'] = round(time.monotonic() - started, 3)
    return record


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
