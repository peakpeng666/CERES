"""Tool registry: JSON-schema arguments, policy gates and handlers over the existing workbench services.

Handlers return JSON-native dicts built from real service results. A handler raises ``ToolError`` for a refused or
failed call; the runner records it and shows the model the error, so recovery is visible in the call log.
Effects: ``session`` (agent state only), ``compute`` (solver/checker processes), ``write`` (stored scenario),
``physical`` (Gazebo session; always requires explicit user approval).
"""
from __future__ import annotations

import copy
import json
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import physics_stats, time_windows
from ..paths import gazebo_gap
from . import contract as tc
from . import diagnose


ROBUSTNESS_RUNS = 16          # the two best plans of a session; 0 leaves the evaluation out
ROBUSTNESS_INTENSITY = 1.
ROBUSTNESS_WALL_S = 300


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
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f'{path} 须为{"整数" if t == "integer" else "数值"}']
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            return [f'{path} 须为有限数值']
        if t == 'integer' and value != int(value):
            return [f'{path} 须为整数']
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
            for i, item in enumerate(value):
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


def log_arguments(value):
    """Keep refused non-JSON numbers visible without making the session impossible to persist."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: log_arguments(item) for key, item in value.items()}
    if isinstance(value, list):
        return [log_arguments(item) for item in value]
    return copy.deepcopy(value)


# ---------------------------------------------------------------- helpers --
def _scenario(ctx: ToolContext) -> dict:
    from ..planning import fingerprint
    from ..scenarios import get_scenario
    sid = ctx.session.get('scenario_id')
    if not sid:
        raise ToolError('no_scenario', '尚未建立任务地图；请先调用 build_scenario')
    try:
        sc = get_scenario(sid)
    except KeyError:
        raise ToolError('no_scenario', f'任务地图 {sid} 不存在') from None
    if ctx.session.get('built_revision') is not None and ctx.session['built_revision'] != ctx.session['contract']['revision']:
        raise ToolError('stale_scenario', '任务合同已修改；请先按当前合同重新建立任务地图')
    expected = ctx.session.get('scenario_instance_sha256')
    if expected and fingerprint(sc['instance']) != expected:
        raise ToolError('stale_scenario', '任务地图已在会话外修改；请先重新建立地图，再求解和复核当前任务')
    return sc


def current_run_ids(session: dict) -> list[str]:
    """Current build's runs; keep the all-time run_ids separately for history and export."""
    if session.get('built_revision') is not None and session['built_revision'] != session['contract']['revision']:
        return []
    return session.get('active_run_ids', session.get('run_ids', []))


def run_matches_current(session: dict, run: dict) -> bool:
    from ..planning import fingerprint
    if run.get('run_id') not in current_run_ids(session) or run.get('scenario_id') != session.get('scenario_id'):
        return False
    expected = session.get('scenario_instance_sha256')
    return not expected or isinstance(run.get('instance'), dict) and fingerprint(run['instance']) == expected


def _catalog(ctx: ToolContext) -> list[dict]:
    return [a for a in ctx.lab.catalog() if 'fleet_routing' in a.get('problem_classes', [])]


def _run(ctx: ToolContext, run_id: str) -> dict:
    if run_id not in ctx.session.get('run_ids', []):
        raise ToolError('unknown_run', f'运行 {run_id} 不属于本会话')
    run = ctx.lab.store.get_run(run_id)
    if run is None:
        raise ToolError('unknown_run', f'没有运行记录 {run_id}')
    if not run_matches_current(ctx.session, run):
        raise ToolError('stale_run', f'运行 {run_id} 不属于当前任务版本；历史记录仍保留，请使用本轮重新求解的方案')
    return run


def run_summary(run: dict, pre: dict | None = None, verification: dict | None = None) -> dict:
    m = run.get('metrics') or {}
    cause = diagnose.classify(run, pre)
    independent_recheck = None if verification is None else bool(verification.get('valid') and verification.get('agrees'))
    check_valid = (run.get('check') or {}).get('valid')
    if verification is not None:
        check_valid = bool(check_valid and independent_recheck)
        category = verification.get('category') or ('ok' if independent_recheck else 'implementation_error')
        if not independent_recheck and category == 'ok':
            category = 'implementation_error'
        cause = {'category': category}
    return {
        'run_id': run['run_id'], 'algorithm_id': run['algorithm']['id'], 'algorithm': run['algorithm'].get('name'),
        'state': run.get('state'), 'status': run.get('status'), 'check_valid': check_valid,
        'independent_recheck': independent_recheck,
        'objective': (run.get('objective') or {}).get('value'), 'objective_unit': (run.get('objective') or {}).get('unit'),
        'total_distance_m': m.get('total_distance_m'), 'makespan_s': m.get('makespan_s'), 'predicted_makespan_s': m.get('predicted_makespan_s'),
        'execution_estimate': m.get('execution_estimate'),
        'vehicles_used': m.get('vehicles_used'), 'trips': m.get('trips'), 'wall_s': m.get('wall_s'),
        'errors': [str(e) for e in ((run.get('check') or {}).get('errors') or [])][:3] + ([str(run['error'])[:300]] if run.get('error') else []),
        'diagnosis': cause['category'], 'diagnosis_label': diagnose.CATEGORY_LABEL.get(cause['category'], cause['category']),
        'params': run.get('params'), 'reason': ((run.get('raw_result') or {}).get('reason') or '')[:300] or None,
        'time_windows': time_windows.compact((run.get('check') or {}).get('time_windows')),
    }


GEOMETRY_FIELDS = {'task_kind', 'field_size_m', 'field_area_m2', 'field_shape', 'real_field_id', 'plot_count', 'task_count',
                   'task_distribution', 'obstacles', 'depot_location', 'vehicle_width_m', 'seed'}


def _set_time_windows(inst: dict, contract: dict, built_from: dict):
    """The contract's window on every task. A map built without one keeps any windows set on it task by task in the editor."""
    window = tc.window(contract)
    if not window and not any(built_from.get(k) for k in tc.TIME_FIELDS):
        return
    for t in inst['tasks']:
        t.pop('time_window', None)
        if window:
            t['time_window'] = list(window)
    start = tc.value(contract, 'start_clock')
    inst.pop('start_clock', None)
    if window and start:
        inst['start_clock'] = start


# ---------------------------------------------------------------- handlers --
def update_task_contract(ctx: ToolContext, args: dict) -> dict:
    current = ctx.session['contract']
    values = dict(args.get('fields') or {})
    evidence = args.get('evidence') or {}
    rejected_evidence = []
    latest_user = next((m.get('text', '') for m in reversed(ctx.session.get('messages', [])) if m.get('role') == 'user'), '')
    for key, raw in list(values.items()):
        old = current['fields'].get(key)
        quote = evidence.get(key)
        if (old and old['source'] in ('user_text', 'user_edit') and old['value'] != raw and quote
                and quote not in latest_user):
            rejected_evidence.append({'key': key, 'value': raw, 'reason': '修改已明确的用户要求须引用本轮用户原话；该片段未出现'})
            values.pop(key)
    new, accepted, rejected = tc.apply(current, values, 'llm', evidence, '模型理解')
    rejected.extend(rejected_evidence)
    new = tc.add_unsupported(new, args.get('unsupported') or [])
    ctx.session['contract'] = new
    return {'accepted': accepted, 'rejected': rejected, 'missing': [tc.FIELDS[k].label for k in tc.missing(new)],
            'unsupported': new['unsupported'], 'status': new['status'], 'revision': new['revision']}


def ask_user(ctx: ToolContext, args: dict) -> dict:
    gaps = set(tc.missing(ctx.session['contract']))
    if ctx.session.get('phase') == 'understand' and not gaps:
        raise ToolError('question_not_needed', '任务点数、车辆数与场地大小均已确定；其余字段使用默认值，由用户在确认合同时修改，不需要提问')
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
    from ..planning import fingerprint
    from ..scenarios import get_scenario, save_user_scenario
    contract = ctx.session['contract']
    if contract['status'] != 'confirmed':
        raise ToolError('contract_not_confirmed', '任务合同尚未确认；先请用户确认')
    execution = tc.execution(contract)
    overrides, _ = compose.fill_defaults(tc.compose_overrides(contract))
    existing = ctx.session.get('scenario_id')
    built_from = ctx.session.get('scenario_contract_fields') or {}
    geometry_changed = any(built_from.get(k) != tc.value(contract, k) for k in GEOMETRY_FIELDS)
    # Removing an explicit demand must restore the generator's task-kind default, not retain the old value.
    geometry_changed = geometry_changed or built_from.get('task_demand') is not None and tc.value(contract, 'task_demand') is None
    if existing and not geometry_changed:
        sc = get_scenario(existing)
        inst = sc['instance']
        veh = inst['vehicles']
        if tc.value(contract, 'vehicle_count') is not None and int(tc.value(contract, 'vehicle_count')) != veh['count']:
            n = int(tc.value(contract, 'vehicle_count'))
            names = list(veh.get('names') or [])
            veh['names'] = (names + [f'车{i + 1}' for i in range(len(names), n)])[:n]
            veh['count'] = n
        veh['speed_mps'] = overrides['speed_mps']
        if tc.value(contract, 'task_demand') is not None:
            for t in inst['tasks']:
                t['demand'] = int(tc.value(contract, 'task_demand'))
        veh['capacity'] = tc.value(contract, 'vehicle_capacity') or max(
            max(t['demand'] for t in inst['tasks']), math.ceil(sum(t['demand'] for t in inst['tasks']) / veh['count'] * 1.3))
        inst['objective'] = overrides['objective']
        veh['trips'], veh['travel'] = execution['trip_mode'], execution['travel_mode']
        _set_time_windows(inst, contract, built_from)
        # Imported and asset-built maps carry no generator record of their own.
        provenance = sc.setdefault('provenance', {})
        provenance['agent'] = {'session_id': ctx.session['session_id'], 'contract_revision': contract['revision']}
        provenance.setdefault('params', {})['spec'] = overrides
        sc['summary'] = f"{veh['count']} 台车 · " + sc['summary'].split(' · ', 1)[-1]
        sc.setdefault('compose', {})['cards'] = compose.describe(overrides)
        stored = save_user_scenario(sc, overwrite=True, expected_revision=sc.get('revision'))
        mode = 'updated'
    else:
        text = contract.get('request_text') or '智能体任务'
        if tc.value(contract, 'real_field_id'):
            sc = compose.from_field(tc.value(contract, 'real_field_id'), '', execution['seed'], overrides)
        else:
            sc = compose.compose('', execution['seed'], overrides)
        # The contract is authoritative: reparsing its initial text would resurrect cleared or superseded fields.
        sc['title'] = text[:22] + ('…' if len(text) > 22 else '')
        sc['compose']['text'] = text
        sc['provenance']['params']['text'] = text
        sc['id'] += f"-{ctx.session['session_id'][-6:]}-r{contract['revision']}"
        sc['instance']['vehicles']['trips'] = execution['trip_mode']
        sc['instance']['vehicles']['travel'] = execution['travel_mode']
        _set_time_windows(sc['instance'], contract, built_from)
        sc['provenance']['agent'] = {'session_id': ctx.session['session_id'], 'contract_revision': contract['revision']}
        stored = save_user_scenario(sc, overwrite=False)
        mode = 'replaced' if existing else 'created'
        if existing:
            ctx.session.setdefault('previous_scenarios', []).append(existing)
    ctx.session['scenario_id'] = stored['id']
    ctx.session['scenario_contract_fields'] = {k: tc.value(contract, k) for k in tc.FIELDS}
    instance_sha256 = fingerprint(stored['instance'])
    changed = (ctx.session.get('built_revision') != contract['revision']
               or ctx.session.get('scenario_instance_sha256') != instance_sha256)
    ctx.session['built_revision'] = contract['revision']
    ctx.session['scenario_instance_sha256'] = instance_sha256
    if changed:
        ctx.session['active_run_ids'] = []
        ctx.session['active_sim_runs'] = []
        ctx.session.setdefault('verification_history', {}).update(ctx.session.get('verifications') or {})
        ctx.session['verifications'] = {}
        ctx.session['recommendation'] = None
        ctx.session['tried_algorithms'] = []
        ctx.session['solved_since_build'] = False
    inst = stored['instance']
    pre = diagnose.precheck(inst)
    ctx.session['precheck'] = pre
    ctx.emit('scenario', {'scenario_id': stored['id']})
    return {'scenario_id': stored['id'], 'mode': mode, 'title': stored['title'], 'summary': stored['summary'],
            'units': stored['frame'].get('units'), 'tasks': len(inst['tasks']), 'vehicles': inst['vehicles']['count'],
            'capacity': inst['vehicles']['capacity'], 'trips': inst['vehicles'].get('trips'),
            'travel': inst['vehicles'].get('travel'), 'obstacles': len(inst.get('obstacles') or []),
            'objective': inst.get('objective'), 'time_window': tc.window(contract), 'start_clock': inst.get('start_clock'), 'precheck': pre}


def list_algorithms(ctx: ToolContext, args: dict) -> dict:
    out = []
    for a in _catalog(ctx):
        out.append({'id': a['id'], 'name': a['name'], 'library': a.get('library'), 'family': a.get('family'),
                    'guarantee': a.get('guarantee'), 'available': a.get('available'),
                    'unavailable_reason': a.get('unavailable_reason'), 'summary': a.get('summary', '')[:160],
                    'params': [p['name'] for p in a.get('params', [])], 'time_windows': (a.get('limits') or {}).get('time_windows')})
    return {'algorithms': out}


def describe_algorithm(ctx: ToolContext, args: dict) -> dict:
    """One algorithm's conditions and what this machine has measured about it."""
    from .. import algorithm_stats
    a = next((x for x in ctx.lab.catalog() if x['id'] == args['algorithm_id']), None)
    if a is None:
        raise ToolError('unknown_algorithm', f"没有算法 {args['algorithm_id']}；可先调用 list_algorithms")
    record = algorithm_stats.history(ctx.lab.store.run_digest(), a['id'], ctx.lab.store.list_physics_runs(limit=2000))
    return {'id': a['id'], 'name': a['name'], 'problem': a.get('problem_label'), 'family': a.get('family'), 'guarantee': a.get('guarantee'),
            'limits': a.get('limits'), 'randomness': (a.get('randomness') or {}).get('note'), 'summary': a.get('summary'),
            'params': [{k: p.get(k) for k in ('name', 'label', 'default', 'min', 'max')} for p in a.get('params', [])],
            'available': a.get('available'), 'unavailable_reason': a.get('unavailable_reason'),
            'record': {k: record[k] for k in ('runs', 'completed', 'valid', 'failed', 'wall_s', 'head_to_head', 'physical')}}


def list_capabilities(ctx: ToolContext, args: dict) -> dict:
    """Simulators, learned models and transfers, each with its status. Algorithms are listed by list_algorithms."""
    from .capabilities import capabilities
    found = capabilities(ctx.lab)
    return {k: [{f: x.get(f) for f in ('id', 'name', 'status', 'reason', 'summary', 'approval')} for x in found[k]] for k in ('simulators', 'learners', 'transfers')}


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
    ctx.session.setdefault('active_run_ids', []).extend(run_ids)
    ctx.session['solved_since_build'] = True
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
    ctx.session.setdefault('verifications', {})[run['run_id']] = {'valid': bool(result.get('valid') and result.get('agrees')), 'agrees': result.get('agrees'),
                                                                   'category': cause['category']}
    return {'run_id': run['run_id'], 'recheck': result, 'diagnosis': cause['category'],
            'diagnosis_label': diagnose.CATEGORY_LABEL.get(cause['category']), 'evidence': cause.get('evidence', []),
            'time_windows': time_windows.compact(result.get('time_windows'))}


def prediction_group_model(rows: list[dict]) -> str | None:
    """A best prediction exists only when the entire eligible group shares one explicit model."""
    if not rows:
        return None
    models = set()
    for row in rows:
        estimate = row.get('execution_estimate')
        value = row.get('predicted_makespan_s')
        if not isinstance(estimate, dict) or estimate.get('status') != 'estimated':
            return None
        model = estimate.get('model')
        if (not isinstance(model, str) or not model.strip() or type(value) not in (int, float)
                or not math.isfinite(value) or value <= 0):
            return None
        models.add(model)
    return next(iter(models)) if len(models) == 1 else None


def compare_runs(ctx: ToolContext, args: dict) -> dict:
    from ..planning import comparison_key
    ids = args.get('run_ids') or current_run_ids(ctx.session)
    runs = [_run(ctx, rid) for rid in ids]
    groups: dict[str, list[dict]] = {}
    for r in runs:
        groups.setdefault(comparison_key(r) if r.get('state') == 'done' else 'unfinished', []).append(r)
    tables = []
    for key, members in groups.items():
        rows = [run_summary(r, ctx.session.get('precheck'), (ctx.session.get('verifications') or {}).get(r['run_id']))
                for r in members]
        valid = [row for row in rows if row['status'] == 'feasible' and row['check_valid']]
        best = {}
        prediction_model = prediction_group_model(valid) if key != 'unfinished' else None
        if key != 'unfinished' and len(valid) > 1:
            for metric in ('total_distance_m', 'makespan_s', 'predicted_makespan_s', 'wall_s'):
                if metric == 'predicted_makespan_s' and prediction_model is None:
                    continue
                vals = [row[metric] for row in valid if isinstance(row[metric], (int, float))]
                if vals:
                    best[metric] = min(valid, key=lambda row: row[metric] if isinstance(row[metric], (int, float)) else float('inf'))['run_id']
        tables.append({'comparison_key': key[:16], 'comparable': key != 'unfinished', 'rows': rows, 'best': best,
                       'prediction_comparable': prediction_model is not None, 'prediction_model': prediction_model})
    return {'groups': tables, 'rule': '只在相同输入、目标、单位与约束的已验证方案之间标记最好值；预测工期还要求整组有效且模型版本相同'}


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
    if not verdict or not verdict.get('valid') or not verdict.get('agrees'):
        raise ToolError('unverified', '执行前须先用 verify_run 独立复核且通过')
    _scenario(ctx)
    gap = gazebo_gap()
    if gap:
        raise ToolError('simulator_unavailable', gap)
    body = {'run_id': run['run_id'], 'real_time_factor': float(args.get('real_time_factor') or 4.0),
            'traffic': args.get('traffic', 'serial')}
    try:
        state = ctx.lab.fieldsim.start(body)
    except Exception as exc:  # noqa: BLE001 - ServiceError carries a user-facing message
        raise ToolError(getattr(exc, 'code', 'sim_failed'), getattr(exc, 'message', str(exc))) from None
    ctx.session.setdefault('sim_runs', []).append(run['run_id'])
    ctx.session.setdefault('active_sim_runs', []).append(run['run_id'])
    return {'phase': state.get('phase'), 'session_id': state.get('session_id'), 'run_id': run['run_id'],
            'traffic_mode': state.get('traffic_mode', body['traffic']),
            'note': '已启动 Gazebo 田间会话；规划预览、实时仿真与记录回放分别显示'}


def physics_rows(session: dict) -> list[dict]:
    """Physics results of the session's current runs, in run order."""
    done = session.get('physics') or {}
    return [done[r] for r in current_run_ids(session) if r in done]


def physics_check(ctx: ToolContext, args: dict) -> dict:
    """Execute a verified plan in MuJoCo with the follower and vehicle Gazebo uses. Seconds, no ROS process, no approval."""
    from ..physics import PhysicsError
    run = _run(ctx, args['run_id'])
    verdict = (ctx.session.get('verifications') or {}).get(run['run_id'])
    if not verdict or not verdict.get('valid') or not verdict.get('agrees'):
        raise ToolError('unverified', '物理预演前须先用 verify_run 独立复核且通过')
    try:
        rec = ctx.lab.physics.submit(run_id=run['run_id'])
    except PhysicsError as exc:
        raise ToolError(exc.code, exc.message) from None
    together = _start_coordinated(ctx, run) if len(run.get('routes') or []) > 1 else None   # separate processes: the two runs overlap
    rec = ctx.lab.physics.wait(rec['physics_run_id'], ctx.cancel)
    if rec.get('state') == 'canceled':
        raise ToolError('cancelled', '已取消')
    metrics = rec.get('metrics') or {}
    row = {'run_id': run['run_id'], 'physics_run_id': rec.get('physics_run_id'), 'algorithm': (run.get('algorithm') or {}).get('name'),
           'engine': rec.get('engine'), 'engine_version': rec.get('engine_version'), 'state': rec.get('state'),
           'check_valid': (rec.get('check') or {}).get('valid'), 'errors': (rec.get('errors') or [])[:3] + ((rec.get('check') or {}).get('errors') or [])[:3],
           'wall_s': rec.get('wall_s'), 'metrics': {k: round(v, 3) if isinstance(v, float) else v for k, v in (
               (k, metrics.get(k)) for k in ('tasks_total', 'tasks_completed', 'total_distance_m', 'planned_total_distance_m', 'makespan_s', 'planned_makespan_s',
                                             'cross_track_mean_m', 'cross_track_max_m', 'energy_wh'))},
           **({'time_windows': rec['time_windows']} if rec.get('time_windows') else {})}
    if together:
        row['coordinated'] = _coordinated_row(ctx, together)
    ctx.session.setdefault('physics', {})[run['run_id']] = row
    return row


def _robustness_row(run: dict, rec: dict) -> dict:
    s, spec = rec.get('summary') or {}, rec.get('batch') or {}
    return {'run_id': run['run_id'], 'physics_run_id': rec.get('physics_run_id'), 'algorithm': (run.get('algorithm') or {}).get('name'), 'state': rec.get('state'),
            'traffic': rec.get('traffic_mode'), 'seed': spec.get('seed'), 'intensity': spec.get('intensity'), 'factors': spec.get('factors'), 'predicted_makespan_s': rec.get('predicted_makespan_s'),
            **{k: s.get(k) for k in ('requested', 'finished', 'cut_off', 'passed', 'success_rate', 'success_ci95', 'makespan_s', 'min_gap_m', 'failures', 'order_violations', 'scope')},
            'errors': rec.get('errors') or []}


def physics_robustness(ctx: ToolContext, args: dict) -> dict:
    """Run a verified plan many times under seeded perturbations of the ground, the load, the wheels and the working times.
    The seed is the session's, so the plans of one session meet the same draws and the difference between them is theirs."""
    from ..physics import PhysicsError
    run = _run(ctx, args['run_id'])
    verdict = (ctx.session.get('verifications') or {}).get(run['run_id'])
    if not verdict or not verdict.get('valid') or not verdict.get('agrees'):
        raise ToolError('unverified', '鲁棒性评估前须先用 verify_run 独立复核且通过')
    traffic = args.get('traffic') or ('coordinated' if len(run.get('routes') or []) > 1 else 'serial')
    try:
        rec = ctx.lab.physics.submit_batch(run_id=run['run_id'], traffic=traffic, runs=ROBUSTNESS_RUNS, seed=int(tc.execution(ctx.session['contract'])['seed']),
                                           intensity=ROBUSTNESS_INTENSITY, wall_limit_s=ROBUSTNESS_WALL_S)
    except PhysicsError as exc:
        raise ToolError(exc.code, exc.message) from None
    rec = ctx.lab.physics.wait(rec['physics_run_id'], ctx.cancel, ROBUSTNESS_WALL_S + 30)
    if rec.get('state') == 'canceled':
        raise ToolError('cancelled', '已取消')
    row = _robustness_row(run, rec)
    ctx.session.setdefault('robustness', {})[run['run_id']] = {**row, 'samples': rec.get('samples') or []}
    return row


def robustness_evidence(session: dict) -> dict | None:
    """The evaluated plans, best first, and how they differ draw by draw."""
    done, current = session.get('robustness') or {}, set(current_run_ids(session))
    rows = [done[r] for r in session.get('robustness_order') or [] if r in done and r in current]
    if not rows:
        return None
    measured = [r for r in rows if r.get('samples')]
    difference = None
    if len(measured) == 2:
        a, b = measured
        difference = {'a': a.get('algorithm'), 'b': b.get('algorithm'), **physics_stats.paired(a['samples'], b['samples'])}
    return {'rows': [{k: v for k, v in r.items() if k != 'samples'} for r in rows], 'difference': difference}


def _start_coordinated(ctx: ToolContext, run: dict) -> dict:
    """The same plan with every vehicle moving at once, passing in turn where routes meet. It sits beside the one-at-a-time
    row instead of replacing it, because the Gazebo session still executes one mission at a time."""
    from ..physics import PhysicsError
    try:
        return ctx.lab.physics.submit(run_id=run['run_id'], traffic='coordinated')
    except PhysicsError as exc:
        return {'state': 'unavailable', 'errors': [exc.message]}


def _coordinated_row(ctx: ToolContext, started: dict) -> dict:
    if 'physics_run_id' not in started:
        return started
    rec = ctx.lab.physics.wait(started['physics_run_id'], ctx.cancel)
    metrics = rec.get('metrics') or {}
    return {'physics_run_id': rec.get('physics_run_id'), 'state': rec.get('state'), 'check_valid': (rec.get('check') or {}).get('valid'),
            'errors': (rec.get('errors') or [])[:3] + ((rec.get('check') or {}).get('errors') or [])[:3],
            'makespan_s': metrics.get('makespan_s'), 'energy_wh': metrics.get('energy_wh'), **(rec.get('traffic') or {}),
            **({'time_windows': rec['time_windows']} if rec.get('time_windows') else {})}


def simulation_status(ctx: ToolContext, args: dict) -> dict:
    state = ctx.lab.fieldsim.state()
    keep = ('phase', 'session_id', 'run_id', 'error', 'progress', 'vehicles', 'check')
    return {k: state.get(k) for k in keep if k in state}


def run_study(ctx: ToolContext, args: dict) -> dict:
    """Replicated comparison on the current map: algorithms × budgets × seeds through the existing study engine."""
    import statistics
    from ..scenarios import get_scenario
    sc = _scenario(ctx)
    execution = tc.execution(ctx.session['contract'])
    budgets = args.get('budgets') or [execution['time_limit_s']]
    seeds = args.get('seeds') or [1, 2, 3]
    cells = len(args['algorithms']) * len(budgets) * len(seeds)
    if cells > 60:
        raise ToolError('study_too_large', f'{cells} 个运行超过单次上限 60；请减少算法、预算或种子')
    spec = {'title': f"智能体 {ctx.session['session_id']} 重复实验", 'scenarios': [sc['id']], 'algorithms': args['algorithms'],
            'budgets': budgets, 'seeds': seeds}
    try:
        study = ctx.lab.studies.create(spec, get_scenario, ctx.lab.catalog())
    except ValueError as exc:
        raise ToolError('invalid_study', str(exc)) from None
    sid = study['study_id']
    ctx.session.setdefault('study_ids', []).append(sid)
    run_ids = [cell['run_id'] for cell in study['design']]
    ctx.session.setdefault('run_ids', []).extend(run_ids)
    ctx.session.setdefault('active_run_ids', []).extend(run_ids)
    ctx.session['solved_since_build'] = bool(run_ids)
    ctx.emit('runs', {'run_ids': run_ids, 'scenario_id': sc['id']})
    deadline = time.monotonic() + max(budgets) * cells + 120
    while time.monotonic() < deadline:
        view = ctx.lab.studies.get(sid)
        if view['progress']['state'] == 'done':
            break
        if ctx.cancel.is_set():
            ctx.lab.studies.cancel(sid)
            raise ToolError('cancelled', '重复实验已按用户请求取消', {'study_id': sid})
        time.sleep(0.5)
    else:
        raise ToolError('wait_timeout', '等待重复实验结束超时；运行仍在后台，可在「算法实验」中查看', {'study_id': sid})
    groups: dict[tuple, list[dict]] = {}
    for run in ctx.lab.studies.runs(sid):
        key = (run['algorithm']['id'], (run.get('params') or {}).get('time_limit_s'))
        groups.setdefault(key, []).append(run)
    rows = []
    for (aid, budget), runs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        ok = [r for r in runs if r.get('state') == 'done' and r.get('status') == 'feasible' and (r.get('check') or {}).get('valid')]
        vals = [r['objective']['value'] for r in ok if isinstance((r.get('objective') or {}).get('value'), (int, float))]
        rows.append({'algorithm_id': aid, 'time_limit_s': budget, 'runs': len(runs), 'verified_feasible': len(ok),
                     'objective_median': round(statistics.median(vals), 3) if vals else None,
                     'objective_min': round(min(vals), 3) if vals else None,
                     'objective_max': round(max(vals), 3) if vals else None,
                     'wall_s_mean': round(statistics.fmean([(r.get('metrics') or {}).get('wall_s') or 0 for r in runs]), 3)})
    return {'study_id': sid, 'runs': len(run_ids), 'requested_runs': cells, 'skipped': study.get('skipped', []),
            'seeds': seeds, 'budgets': budgets, 'rows': rows,
            'note': '统计检验、性能剖面与临界差图见「算法实验 → 统计分析」'}


# ---------------------------------------------------------------- registry --
RUN_ID = {'type': 'object', 'properties': {'run_id': {'type': 'string', 'maxLength': 80}}, 'required': ['run_id'],
          'additionalProperties': False}
EMPTY = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def registry() -> dict[str, Tool]:
    tools = [
        Tool('update_task_contract', '更新任务合同',
             '把用户原话中的任务要素写入任务合同（单位统一为米、秒、平方米）。只填写本轮读到或被用户改动的字段；'
             '所有任务共用的时间要求写入 task_deadline、task_earliest、start_clock；无法用字段表达的要求放进 unsupported。',
             tc.tool_schema(), update_task_contract, 'session', 'understand'),
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
        Tool('describe_algorithm', '查阅算法', '读取一个算法的适用条件、最优性、参数范围，以及它在本机的运行次数、耗时与同题对比。',
             {'type': 'object', 'properties': {'algorithm_id': {'type': 'string', 'maxLength': 80}}, 'required': ['algorithm_id'], 'additionalProperties': False},
             describe_algorithm, 'session', 'recommend'),
        Tool('list_capabilities', '查看能力', '列出可用的仿真器、学习模型与引擎迁移及其状态。', EMPTY, list_capabilities, 'session', 'recommend'),
        Tool('recommend_algorithms', '推荐算法', '根据任务地图特征给出规则推荐排序、理由与建议对比集合。', EMPTY,
             recommend_algorithms, 'compute', 'recommend', needs_scenario=True),
        Tool('solve', '求解对比',
             '用同一输入、预算与种子运行 1–6 个算法并等待结束；返回每个方案的状态、指标与独立检查结论。',
             {'type': 'object', 'properties': {
                 'algorithms': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {'type': 'string', 'maxLength': 80}},
                 'time_limit_s': {'type': 'number', 'minimum': 0.2, 'maximum': 60},
                 'seed': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647}},
              'required': ['algorithms'], 'additionalProperties': False}, solve, 'compute', 'solve', needs_scenario=True),
        Tool('run_study', '重复实验',
             '在当前任务地图上按 算法 × 预算 × 随机种子 批量运行（最多 60 个），返回每组经检查可行的次数与目标值分布。',
             {'type': 'object', 'properties': {
                 'algorithms': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {'type': 'string', 'maxLength': 80}},
                 'budgets': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': {'type': 'number', 'minimum': 0.2, 'maximum': 60}},
                 'seeds': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'items': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647}}},
              'required': ['algorithms'], 'additionalProperties': False}, run_study, 'compute', 'study', needs_scenario=True),
        Tool('verify_run', '独立复核', '对一个方案重新运行独立检查器：任务恰好服务一次、载重、避障、边界、到达时间、里程复算及与存档一致性。',
             RUN_ID, verify_run, 'compute', 'verify', needs_scenario=True),
        Tool('compare_runs', '方案对比', '在可比方案之间比较里程、工期与耗时，只在同口径的已验证方案中标记最好值。',
             {'type': 'object', 'properties': {'run_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 24}},
              'additionalProperties': False}, compare_runs, 'session', 'verify'),
        Tool('diagnose_run', '诊断失败', '区分任务不可行、方法不适用、预算不足与软件实现错误，并给出修复动作。',
             RUN_ID, diagnose_run, 'compute', 'repair', needs_scenario=True),
        Tool('propose_task_change', '提议修改任务',
             '当任务本身不可行时，提出修改任务合同的方案（如允许多趟、增加车辆、提高容量、放宽截止时间），等待用户批准。',
             {'type': 'object', 'properties': {'fields': tc.tool_schema()['properties']['fields'],
                                               'reason': {'type': 'string', 'maxLength': 300}},
              'required': ['fields', 'reason'], 'additionalProperties': False}, propose_task_change, 'session', 'repair'),
        Tool('physics_check', '物理预演', '在 MuJoCo 中用同一路径跟踪器和车辆参数执行已复核的方案，返回实际里程、工期、横向偏差、接触和实测到达是否按时。数秒完成，不启动 ROS，无需批准。',
             RUN_ID, physics_check, 'compute', 'physics', needs_scenario=True),
        Tool('physics_robustness', '鲁棒性评估', '在 MuJoCo 中把已复核的方案放到随机化的地面摩擦、载荷、轮径和作业时长下多次执行，返回成功率、工期中位数与 90 分位、最小车距分布和失败原因。同一会话的方案面对同一组扰动。这是仿真内的敏感度，不是实车可靠性。无需批准。',
             {'type': 'object', 'properties': {'run_id': {'type': 'string', 'maxLength': 80}, 'traffic': {'type': 'string', 'enum': ['serial', 'coordinated']}},
              'required': ['run_id'], 'additionalProperties': False}, physics_robustness, 'compute', 'robustness', needs_scenario=True),
        Tool('start_simulation', '启动田间仿真', '把已通过独立复核的方案送入 Gazebo 田间仿真（需要用户批准）。',
             {'type': 'object', 'properties': {'run_id': {'type': 'string', 'maxLength': 80},
                                               'real_time_factor': {'type': 'number', 'minimum': 0.5, 'maximum': 8},
                                               'traffic': {'type': 'string', 'enum': ['serial', 'coordinated']}},
              'required': ['run_id'], 'additionalProperties': False}, start_simulation, 'physical', 'simulate', needs_scenario=True),
        Tool('simulation_status', '查看仿真状态', '读取当前田间仿真会话的阶段与结果。', EMPTY, simulation_status,
             'session', 'simulate'),
    ]
    return {t.name: t for t in tools}


PHASE_TOOLS = {
    'understand': ['update_task_contract', 'ask_user', 'find_real_fields'],
    'execute': ['build_scenario', 'list_algorithms', 'describe_algorithm', 'list_capabilities', 'recommend_algorithms', 'solve', 'run_study', 'verify_run', 'compare_runs',
                'diagnose_run', 'propose_task_change', 'physics_check', 'physics_robustness', 'start_simulation', 'simulation_status', 'ask_user'],
}


def execute(tools: dict[str, Tool], ctx: ToolContext, name: str, args: dict) -> dict:
    """Validate and run one call; returns the call record (status ok / error / refused / awaiting_approval)."""
    started = time.monotonic()
    record = {'name': name, 'arguments': log_arguments(args), 'started_utc': _now()}
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
        record.update(status='refused' if exc.code in ('contract_not_confirmed', 'no_scenario', 'stale_scenario', 'stale_run', 'unverified', 'question_not_needed',
                                                         'invalid_arguments', 'unknown_tool') else 'error',
                      error={'code': exc.code, 'message': exc.message, **({'data': exc.data} if exc.data else {})})
    except Exception as exc:  # noqa: BLE001 - recorded as an implementation error of the tool itself
        record.update(status='error', error={'code': 'tool_exception', 'message': f'{type(exc).__name__}: {exc}'[:600]})
    record['duration_s'] = round(time.monotonic() - started, 3)
    return record


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
