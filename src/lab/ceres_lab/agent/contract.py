"""CERES 强类型农艺任务契约与多源证据追溯 (task-contract)

将自然语言意图编译为结构化契约。每个字段携带国际单位制数值及证据来源链
(用户原句 / LLM原文引证 / 表单修正 / 默认推导 / 自动修复)。不可行需求显式保留挂起。
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field

from .. import time_windows

SCHEMA = 'urp.task-contract/1'
SOURCES = ('user_text', 'llm', 'user_edit', 'default', 'repair')

KIND_OPTIONS = {'sampling': '采样', 'inspection': '巡检', 'spraying': '植保', 'harvest': '采收', 'transport': '运输',
                'seeding': '播种'}
DIST_OPTIONS = {'uniform': '均匀分布', 'clustered': '成片聚集', 'rows': '沿作物行'}
SHAPE_OPTIONS = {'rect': '矩形', 'L': 'L 形', 'irregular': '不规则'}
DEPOT_OPTIONS = {'sw': '西南角', 'se': '东南角', 'nw': '西北角', 'ne': '东北角', 'center': '中部', 'n': '北侧',
                 's': '南侧', 'e': '东侧', 'w': '西侧'}
OBJECTIVE_OPTIONS = {'total_distance': '总里程最短', 'makespan': '最晚完成时间最短'}
TRIP_OPTIONS = {'multi': '允许多趟往返', 'single': '每车单趟'}
TRAVEL_OPTIONS = {'rows': '沿行间与地头', 'free': '自由通行（只避障）'}
OBSTACLE_OPTIONS = {'pond': '池塘', 'building': '建筑', 'greenhouse': '温室', 'tree': '树木', 'ditch': '沟渠', 'rock': '石块'}


@dataclass(frozen=True)
class FieldDef:
    label: str
    kind: str                      # enum | int | number | size | bool | text | counts | clock | time
    unit: str | None = None
    lo: float | None = None
    hi: float | None = None
    options: dict = field(default_factory=dict)
    spec: str | None = None        # key in the composer spec (ceres_lab.compose.nl)
    describe: str = ''


FIELDS: dict[str, FieldDef] = {
    'field_mission': FieldDef('田间行作任务', 'mission', describe='全覆盖行作的田块、作物、作业、平台、机具及机组数；经用户确认后才执行'),
    'task_kind': FieldDef('作业类型', 'enum', options=KIND_OPTIONS, spec='kind', describe='作业类型'),
    'field_size_m': FieldDef('场地尺寸', 'size', 'm', 5, 5000, spec='size', describe='矩形外包尺寸 [宽, 高]，米'),
    'field_area_m2': FieldDef('场地面积', 'number', 'm²', 25, 25_000_000, spec='area_m2',
                              describe='只给出面积时使用，平方米；1 亩 = 666.67 m²，1 公顷 = 10000 m²'),
    'field_shape': FieldDef('边界形状', 'enum', options=SHAPE_OPTIONS, spec='shape'),
    'real_field_id': FieldDef('真实田块', 'text', describe='Fields2Benchmark 荷兰田块编号，例如 nl_field_12'),
    'plot_count': FieldDef('地块数', 'int', '块', 1, 40, spec='plots'),
    'task_count': FieldDef('任务点数', 'int', '个', 1, 400, spec='tasks'),
    'task_distribution': FieldDef('任务分布', 'enum', options=DIST_OPTIONS, spec='distribution'),
    'task_demand': FieldDef('单点需求', 'int', '单位', 1, 1000, spec='demand', describe='每个任务点占用的容量单位'),
    'vehicle_count': FieldDef('车辆数', 'int', '台', 1, 30, spec='vehicles'),
    'vehicle_capacity': FieldDef('单车容量', 'int', '单位', 1, 100000, spec='capacity', describe='每趟可服务的需求单位'),
    'vehicle_speed_mps': FieldDef('车速', 'number', 'm/s', 0.05, 10, spec='speed_mps', describe='米每秒；km/h 除以 3.6'),
    'vehicle_width_m': FieldDef('车宽', 'number', 'm', 0.2, 6, spec='robot_width'),
    'trip_mode': FieldDef('趟次', 'enum', options=TRIP_OPTIONS),
    'travel_mode': FieldDef('通行方式', 'enum', options=TRAVEL_OPTIONS),
    'depot_location': FieldDef('车库位置', 'enum', options=DEPOT_OPTIONS, spec='depot'),
    'obstacles': FieldDef('障碍', 'counts', options=OBSTACLE_OPTIONS, spec='obstacles', describe='各类障碍的数量'),
    'objective': FieldDef('优化目标', 'enum', options=OBJECTIVE_OPTIONS, spec='objective'),
    'start_clock': FieldDef('出发时刻', 'clock', describe='车辆出发的钟点，24 小时制 HH:MM；截止或最早时间写成钟点时必须给出'),
    'task_deadline': FieldDef('截止时间', 'time', describe='每个任务点最迟到达的时间：钟点写 HH:MM（如 09:00），从出发算起的时长写秒数加 s（如 1800s）'),
    'task_earliest': FieldDef('最早时间', 'time', describe='每个任务点最早开始作业的时间，写法同截止时间；车辆早到就地等待'),
    'time_limit_s': FieldDef('每个算法的时间预算', 'number', 's', 0.2, 120),
    'seed': FieldDef('随机种子', 'int', None, 0, 2_147_483_647, spec='seed'),
    'compare_count': FieldDef('对比算法数', 'int', '个', 1, 6),
    'simulate': FieldDef('规划后进入仿真执行', 'bool'),
}
# Fields that change what is solved; the agent asks rather than defaulting them silently.
KEY_FIELDS = ('task_count', 'vehicle_count')
SIZE_FIELDS = ('field_size_m', 'field_area_m2', 'real_field_id')
TIME_FIELDS = ('start_clock', 'task_deadline', 'task_earliest')
RULE_KEYS = {'kind': 'task_kind', 'size': 'field_size_m', 'area_m2': 'field_area_m2', 'shape': 'field_shape',
             'plots': 'plot_count', 'tasks': 'task_count', 'distribution': 'task_distribution', 'demand': 'task_demand',
             'vehicles': 'vehicle_count', 'capacity': 'vehicle_capacity', 'speed_mps': 'vehicle_speed_mps',
             'robot_width': 'vehicle_width_m', 'depot': 'depot_location', 'obstacles': 'obstacles',
             'objective': 'objective', 'seed': 'seed', 'start_clock': 'start_clock', 'deadline': 'task_deadline',
             'earliest': 'task_earliest'}
EXECUTION_DEFAULTS = {'trip_mode': 'multi', 'travel_mode': 'rows', 'time_limit_s': 2.0, 'seed': 42, 'compare_count': 3,
                      'simulate': False}


OPEN_SEALED = {'ee_field_130'}  # the single Estonian development field released before the protocol freeze


def sealed_field(field_id: str) -> bool:
    return field_id.startswith(('ee_', 'lt_')) and field_id not in OPEN_SEALED


def _finite(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f'应为有限数值，实际 {v!r}')
    try:
        number = float(v)
    except OverflowError:
        raise ValueError('数值超出支持范围') from None
    if not math.isfinite(number):
        raise ValueError(f'应为有限数值，实际 {v!r}')
    return number


def _time_value(spec: FieldDef, value) -> str:
    """``HH:MM`` for a time of day, ``1800s`` for a time after the departure; a number is read as seconds after it."""
    from ..compose.nl import clock_value, duration_value
    if spec.kind == 'time' and not isinstance(value, bool) and isinstance(value, (int, float)):
        seconds = _finite(value)
    elif not isinstance(value, str) or not value.strip() or len(value) > 40:
        raise ValueError(f'{spec.label}须为钟点 HH:MM' + ('或从出发算起的时长' if spec.kind == 'time' else ''))
    elif spec.kind == 'time' and re.fullmatch(r'\s*\d+(\.\d+)?\s*[sS]\s*', value):
        seconds = float(value.strip()[:-1])
    else:
        clock = clock_value(value)
        if clock:
            return clock
        seconds = duration_value(value) if spec.kind == 'time' else None
        if seconds is None:
            raise ValueError(f'无法把 {value!r} 读作{spec.label}')
    if not 0 < seconds <= 86400:
        raise ValueError(f'{spec.label}须在出发后 24 小时之内，实际 {seconds:g} s')
    return f'{seconds:g}s'


def normalize(key: str, value):
    """Return the normalized value of one field or raise ValueError with a Chinese reason."""
    spec = FIELDS.get(key)
    if spec is None:
        raise ValueError(f'未知字段 {key}')
    if spec.kind == 'mission':
        from .field_contract import normalize as normalize_mission
        return normalize_mission(value)
    if spec.kind in ('clock', 'time'):
        return _time_value(spec, value)
    if spec.kind == 'enum':
        if not isinstance(value, str) or value not in spec.options:
            raise ValueError(f'{spec.label}须为 {", ".join(spec.options)} 之一，实际 {value!r}')
        return value
    if spec.kind == 'bool':
        if not isinstance(value, bool):
            raise ValueError(f'{spec.label}须为布尔值')
        return value
    if spec.kind == 'text':
        if not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise ValueError(f'{spec.label}须为 1–80 字的字符串')
        if key == 'real_field_id':
            fid = value.strip().lower()
            if not re.fullmatch(r'(nl|ee|lt)_field_\d+', fid):
                raise ValueError('真实田块编号形如 nl_field_12')
            if sealed_field(fid):
                raise ValueError(f'{fid} 属于封存测试集（爱沙尼亚、立陶宛田块），评测协议冻结前不得用于规划运行；请选用荷兰田块')
            return fid
        return value.strip()
    if spec.kind == 'counts':
        if not isinstance(value, dict):
            raise ValueError(f'{spec.label}须为 {{类别: 数量}} 对象')
        out = {}
        for kind, count in value.items():
            if kind not in spec.options:
                raise ValueError(f'障碍类别须为 {", ".join(spec.options)} 之一，实际 {kind!r}')
            n = _finite(count)
            if n != int(n) or not 0 <= n <= 30:
                raise ValueError(f'{spec.options[kind]}数量须为 0–30 的整数')
            if n:
                out[kind] = int(n)
        return out
    if spec.kind == 'size':
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f'{spec.label}须为 [宽, 高]（米）')
        out = [round(_finite(v), 3) for v in value]
        if not all(spec.lo <= v <= spec.hi for v in out):
            raise ValueError(f'{spec.label}每边须在 {spec.lo:g}–{spec.hi:g} m')
        return out
    number = _finite(value)
    if spec.kind == 'int':
        if number != int(number):
            raise ValueError(f'{spec.label}须为整数，实际 {value!r}')
        number = int(number)
    if spec.lo is not None and number < spec.lo or spec.hi is not None and number > spec.hi:
        raise ValueError(f'{spec.label}须在 {spec.lo:g}–{spec.hi:g}{spec.unit or ""}，实际 {number:g}')
    return number


def empty(text: str = '') -> dict:
    return {'schema': SCHEMA, 'status': 'draft', 'revision': 0, 'request_text': text, 'fields': {},
            'unsupported': [], 'history': [], 'confirmed_revision': None}


def value(contract: dict, key: str, fallback=None):
    item = contract['fields'].get(key)
    return item['value'] if item else fallback


def apply(contract: dict, values: dict, source: str, evidence: dict | None = None, note: str = '') -> tuple[dict, list, list]:
    """Return (new contract, accepted changes, rejected items). Confirmed contracts become drafts again on change."""
    if source not in SOURCES:
        raise ValueError(f'未知来源 {source}')
    out = copy.deepcopy(contract)
    accepted, rejected = [], []
    evidence = evidence or {}
    for key, raw in (values or {}).items():
        if key not in FIELDS:
            rejected.append({'key': key, 'value': raw, 'reason': f'未知字段 {key}'})
            continue
        if raw is None:
            if key in out['fields'] and source in ('user_edit', 'llm'):
                if source == 'llm' and out['fields'][key]['source'] in ('user_text', 'user_edit') and not evidence.get(key):
                    rejected.append({'key': key, 'value': raw, 'reason': '保留用户已明确给出的值；如需清除请引用新的原话'})
                    continue
                old = out['fields'].pop(key)
                accepted.append({'key': key, 'old': old['value'], 'new': None, 'source': source})
            continue
        try:
            new = normalize(key, raw)
        except ValueError as exc:
            rejected.append({'key': key, 'value': raw, 'reason': str(exc)})
            continue
        old = out['fields'].get(key)
        if old and old['value'] == new:
            continue
        # A value the user stated or edited is never overwritten by a model guess without new evidence.
        if old and old['source'] in ('user_text', 'user_edit') and source == 'llm' and not evidence.get(key):
            rejected.append({'key': key, 'value': raw, 'reason': '保留用户已明确给出的值；如需修改请引用新的原话'})
            continue
        out['fields'][key] = {'value': new, 'source': source, 'evidence': str(evidence.get(key) or '')[:160]}
        accepted.append({'key': key, 'old': old['value'] if old else None, 'new': new, 'source': source})
    if accepted:
        out['revision'] += 1
        out['history'].append({'revision': out['revision'], 'source': source, 'note': note[:200], 'changes': accepted})
        if out['status'] == 'confirmed':
            out['status'] = 'draft'
    return out, accepted, rejected


def add_unsupported(contract: dict, items: list[str], source: str = 'llm') -> dict:
    out = copy.deepcopy(contract)
    added = []
    for item in items or []:
        text = str(item).strip()[:200]
        if text and text not in out['unsupported']:
            out['unsupported'].append(text)
            added.append(text)
    if added:
        out['revision'] += 1
        out['history'].append({'revision': out['revision'], 'source': source, 'note': '记录当前模型不支持的用户要求',
                               'changes': [], 'unsupported_added': added})
        if out['status'] == 'confirmed':
            out['status'] = 'draft'
    return out


def window(contract: dict) -> list | None:
    """``[earliest_s, latest_s]`` for every task, or None when the request sets no time; only for a contract without time gaps."""
    return time_windows.resolve(value(contract, 'task_deadline'), value(contract, 'task_earliest'), value(contract, 'start_clock'))


def time_gaps(contract: dict) -> list[str]:
    """What keeps the stated times from forming a window. The departure is asked for and never assumed: a time of day only
    becomes a constraint measured from it, so any default would decide how tight the task is."""
    deadline, earliest, start = (value(contract, key) for key in ('task_deadline', 'task_earliest', 'start_clock'))
    if not start and any(v and not v.endswith('s') for v in (deadline, earliest)):
        return ['start_clock']
    if not deadline and not earliest:
        return []
    early, late = window(contract)
    if early < 0 or late is not None and late <= 0:
        return ['start_clock']
    return ['task_deadline'] if late is not None and late < early else []


def later_deadline(contract: dict, seconds: float) -> str | None:
    """The deadline, written the way the user wrote it, that allows this many seconds after the departure."""
    current, start = value(contract, 'task_deadline'), value(contract, 'start_clock')
    minutes = math.ceil(seconds / 60)
    if current is None or current.endswith('s'):
        return f'{minutes * 60}s'
    total = int(start[:2]) * 60 + int(start[3:]) + minutes
    return f'{total // 60:02d}:{total % 60:02d}' if total < 24 * 60 else None


def missing(contract: dict) -> list[str]:
    """Key fields still unknown. A size, an area or a real field is needed; the rest are task-defining counts."""
    if value(contract, 'field_mission') is not None:
        return time_gaps(contract)
    gaps = [key for key in KEY_FIELDS if key not in contract['fields']]
    if not any(key in contract['fields'] for key in SIZE_FIELDS):
        gaps.append('field_size_m')
    return gaps + time_gaps(contract)


def from_rule_parse(text: str) -> dict:
    """Rule baseline: the composer's lexicon parser, mapped onto the contract with its matched text spans. Time requests it
    cannot hold in the shared fields stay in ``unsupported``."""
    from ..compose.nl import parse
    parsed = parse(text)
    contract = empty(parsed['text'])
    values, evidence = {}, {}
    for key, raw in parsed['spec'].items():
        target = RULE_KEYS.get(key)
        if target is None:
            continue
        values[target] = raw
    for item in parsed['found']:
        prefix = item['key'].split('.')[0]
        target = 'obstacles' if prefix == 'obstacle' else RULE_KEYS.get(prefix)
        if target:
            evidence[target] = (evidence.get(target, '') + ' ' + item['text']).strip()
    # These are workflow constraints, so the map composer's lexicon does not
    # contain them. Keep explicit requests (including negation) in the same
    # source-backed contract rather than silently using execution defaults.
    trip_requests = re.compile(
        r'(?P<multi>(?:允许|可以|可|改成|改为)多趟(?:往返)?|(?:允许|可以|可)回库补给|不限趟次|'
        r'(?:不要|取消)(?:每车)?单趟(?:限制)?)|'
        r'(?P<single>(?:每(?:台)?车)?只(?:能)?(?:跑|走)?[一1]趟|每(?:台)?车单趟|'
        r'(?:改成|改为|只允许|仅允许)单趟|(?:禁止|不能|不允许)回库补给|(?:不允许|不能)多趟)')
    for match in trip_requests.finditer(text):
        values['trip_mode'] = 'multi' if match.lastgroup == 'multi' else 'single'
        evidence['trip_mode'] = match.group()
    simulation_requests = re.compile(
        r'(?P<skip>(?:不要|不用|不需要|无需|取消)[^，。；\n]{0,16}(?:仿真|Gazebo))|'
        r'(?P<run>(?:放到|送入|进入|启动|开始|执行)[^，。；\n]{0,12}(?:仿真|Gazebo)|'
        r'(?:跑|运行)(?:一下)?(?:Gazebo)?仿真)', re.IGNORECASE)
    for match in simulation_requests.finditer(text):
        values['simulate'] = match.lastgroup == 'run'
        evidence['simulate'] = match.group()
    contract, _, _ = apply(contract, values, 'user_text', evidence, '规则解析')
    return add_unsupported(contract, parsed['unsupported'], 'user_text')


def confirm(contract: dict) -> dict:
    gaps = missing(contract)
    if gaps:
        raise ValueError('仍缺少：' + '、'.join(FIELDS[g].label for g in gaps))
    mission = value(contract, 'field_mission')
    if mission:
        from .field_contract import check_shared_fields
        check_shared_fields(contract, mission)
    out = copy.deepcopy(contract)
    out['status'] = 'confirmed'
    out['confirmed_revision'] = out['revision']
    return out


def compose_overrides(contract: dict) -> dict:
    """Composer spec overrides; omitted fields fall back to the composer's own documented defaults."""
    spec = {}
    for key, item in contract['fields'].items():
        target = FIELDS[key].spec
        if target:
            spec[target] = list(item['value']) if key == 'field_size_m' else item['value']
    if 'size' in spec:
        spec.pop('area_m2', None)
    return spec


def execution(contract: dict) -> dict:
    return {key: value(contract, key, default) for key, default in EXECUTION_DEFAULTS.items()}


def cards(contract: dict, defaults: list[dict] | None = None) -> list[dict]:
    """Display rows: label, formatted value, provenance; composer defaults are listed as ``default``."""
    rows = []
    for key, spec in FIELDS.items():
        item = contract['fields'].get(key)
        if item is None:
            continue
        rows.append({'key': key, 'label': spec.label, 'value': item['value'], 'text': format_value(key, item['value']),
                     'source': item['source'], 'evidence': item.get('evidence', '')})
        if key == 'field_mission':
            from .field_contract import details
            rows[-1]['details'] = details(item['value'])
    for d in defaults or []:
        if value(contract, 'field_mission'):
            continue
        target = RULE_KEYS.get(d['key'])
        if target and target not in contract['fields']:
            rows.append({'key': target, 'label': FIELDS[target].label, 'value': d['value'],
                         'text': format_value(target, d['value']), 'source': 'default', 'evidence': ''})
    if defaults is not None:  # execution settings are part of what the user confirms
        for key, v in EXECUTION_DEFAULTS.items():
            if value(contract, 'field_mission') and key in ('trip_mode', 'travel_mode'):
                continue
            if key not in contract['fields']:
                rows.append({'key': key, 'label': FIELDS[key].label, 'value': v, 'text': format_value(key, v),
                             'source': 'default', 'evidence': ''})
    return rows


def format_value(key: str, v) -> str:
    spec = FIELDS[key]
    if spec.kind == 'mission':
        from .field_contract import summary
        return summary(v)
    if spec.kind in ('clock', 'time'):
        if not v.endswith('s'):
            return v
        seconds = float(v[:-1])
        return f'出发后 {seconds / 60:g} 分钟' if seconds >= 60 else f'出发后 {seconds:g} 秒'
    if spec.kind == 'enum':
        return spec.options.get(v, str(v))
    if spec.kind == 'bool':
        return '是' if v else '否'
    if spec.kind == 'size':
        return f'{v[0]:g} × {v[1]:g} m'
    if spec.kind == 'counts':
        return '、'.join(f'{n} 处{spec.options[k]}' for k, n in v.items()) or '无'
    if isinstance(v, float):
        return f'{v:g} {spec.unit or ""}'.strip()
    return f'{v} {spec.unit or ""}'.strip()


def tool_schema() -> dict:
    """JSON schema of the ``update_task_contract`` arguments (one optional property per field)."""
    props = {}
    for key, spec in FIELDS.items():
        if spec.kind == 'mission':
            from .field_contract import MISSION_SCHEMA
            p = copy.deepcopy(MISSION_SCHEMA)
        elif spec.kind == 'enum':
            p = {'type': 'string', 'enum': list(spec.options)}
        elif spec.kind == 'bool':
            p = {'type': 'boolean'}
        elif spec.kind in ('text', 'clock', 'time'):
            p = {'type': 'string'}
        elif spec.kind == 'counts':
            p = {'type': 'object', 'properties': {k: {'type': 'integer', 'minimum': 0, 'maximum': 30} for k in spec.options},
                 'additionalProperties': False}
        elif spec.kind == 'size':
            p = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'maxItems': 2}
        else:
            p = {'type': 'integer' if spec.kind == 'int' else 'number'}
            if spec.lo is not None:
                p['minimum'] = spec.lo
            if spec.hi is not None:
                p['maximum'] = spec.hi
        p['description'] = (spec.label + (f'（{spec.unit}）' if spec.unit else '') + ('：' + spec.describe if spec.describe else ''))
        props[key] = p
    return {
        'type': 'object',
        'properties': {
            'fields': {'type': 'object', 'properties': props, 'additionalProperties': False,
                       'description': '本轮从用户原话中读到或被用户改动的字段；未提及的字段不要填写'},
            'evidence': {'type': 'object', 'additionalProperties': {'type': 'string'},
                         'description': '字段 → 支撑该值的用户原话片段'},
            'unsupported': {'type': 'array', 'items': {'type': 'string'},
                            'description': '用户提出但上述字段无法表达的要求（如按任务分别给出的时间窗、跨日期的时间、异构车型、充电约束），原样列出'},
        },
        'required': ['fields'],
        'additionalProperties': False,
    }
