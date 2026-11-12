"""Scenario registry (CONTRACT §2.1, §3): built-in benchmark/map scenarios plus user imports in runtime/scenarios/."""
from __future__ import annotations

import copy
import json
import re
import threading
from functools import lru_cache

from .. import paths
from . import benchmark, field, maps, motion
from .validate import PROBLEM_CLASSES, validate_instance

__all__ = ['list_scenarios', 'get_scenario', 'validate_instance', 'world_map', 'map_png', 'import_scenario',
           'summary', 'task_count', 'node_count']

ID_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{1,63}$')
_WRITE_LOCK = threading.RLock()


class ScenarioConflict(ValueError):
    """The caller edited an older revision; its draft must not overwrite newer work."""


@lru_cache(maxsize=None)
def _builtin() -> dict:
    items = field.map_scenarios() + motion.motion_scenarios() + benchmark.turn_scenarios() + benchmark.cvrp_scenarios()
    out = {}
    for s in items:
        s['instance'] = validate_instance(s['problem_class'], s['instance'])
        s['origin'] = 'builtin'
        out[s['id']] = s
    return out


def _user() -> dict:
    out = {}
    if paths.USER_SCENARIOS.is_dir():
        for f in sorted(paths.USER_SCENARIOS.glob('*.json')):
            try:
                s = json.loads(f.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(s, dict) and s.get('id') and s['id'] not in _builtin():
                out[s['id']] = s
    return out


def task_count(problem_class: str, inst: dict) -> int:
    if problem_class == 'turn_routing':
        return len(inst.get('customers') or [])
    if problem_class == 'euclidean_cvrp':
        return max(0, len(inst.get('coordinates') or []) - 1)
    if problem_class == 'fleet_routing':
        return len(inst.get('tasks') or [])
    return 0


def node_count(problem_class: str, inst: dict) -> int | None:
    if problem_class == 'turn_routing':
        return len({u for u, _, _ in inst['arcs']} | {v for _, v, _ in inst['arcs']})
    if problem_class == 'euclidean_cvrp':
        return len(inst.get('coordinates') or [])
    return None


def summary(s: dict) -> dict:
    out = {k: copy.deepcopy(s.get(k)) for k in ('id', 'title', 'summary', 'problem_class', 'frame', 'cost_unit', 'sim', 'library')}
    out['task_count'] = task_count(s['problem_class'], s['instance'])
    out['node_count'] = node_count(s['problem_class'], s['instance'])
    out['origin'] = s.get('origin', 'builtin')
    ref = s.get('reference') or {}
    out['bks'] = ref.get('bks')
    out['bks_optimal'] = ref.get('optimal')
    return out


OPENED = paths.RUNTIME / 'library' / 'opened.json'


def _opened() -> list[str]:
    try:
        return [x for x in json.loads(OPENED.read_text()) if isinstance(x, str)]
    except (OSError, ValueError):
        return []


def _remember(sid: str):
    ids = [sid] + [x for x in _opened() if x != sid]
    OPENED.parent.mkdir(parents=True, exist_ok=True)
    OPENED.write_text(json.dumps(ids[:60]))


def _library(scenario_id: str) -> dict | None:
    from ..library import cvrplib, fields
    name = cvrplib.name_from_scenario(scenario_id)
    if name:
        s = cvrplib.scenario(name)
    else:
        fid = fields.field_from_scenario(scenario_id)
        if not fid:
            return None
        s = fields.scenario(fid)
    s['instance'] = validate_instance(s['problem_class'], s['instance'])
    return s


def list_scenarios() -> list[dict]:
    out = [summary(s) for s in list(_builtin().values()) + list(_user().values())]
    for sid in _opened():
        try:
            s = _library(sid)
        except (KeyError, ValueError, OSError):
            continue
        if s:
            out.append(summary(s))
    return out


def get_scenario(scenario_id: str) -> dict:
    s = _builtin().get(scenario_id) or _user().get(scenario_id)
    if s is None and scenario_id.startswith(('cvrplib-', 'field-')):
        s = _library(scenario_id)
        if s is not None:
            _remember(scenario_id)
    if s is None:
        raise KeyError(scenario_id)
    return copy.deepcopy(s)


def world_map(world: str) -> dict:
    return maps.world_map(world)


def map_png(world: str) -> bytes:
    return maps.map_png(world)


def prepare_import(obj: dict) -> dict:
    """Normalize a scenario import without writing it."""
    if not isinstance(obj, dict):
        raise ValueError('场景应为 JSON 对象')
    sid = str(obj.get('id') or '').strip().lower()
    if not ID_RE.match(sid):
        raise ValueError('场景 id 须为 2–64 位小写字母、数字、-、_、.（以字母或数字开头）')
    if sid in field.MAP_SCENARIO_IDS or sid in motion.MAP_SCENARIO_IDS or sid in _builtin():
        raise ValueError(f'场景 id {sid} 与内置场景重名')
    pc = obj.get('problem_class')
    if pc not in PROBLEM_CLASSES:
        raise ValueError(f'problem_class 须为 {" / ".join(PROBLEM_CLASSES)}')
    inst = validate_instance(pc, obj.get('instance'))
    frame_in = obj.get('frame') or {'kind': 'benchmark'}
    if not isinstance(frame_in, dict):
        raise ValueError('frame 须为 JSON 对象')
    if frame_in.get('kind') == 'map':
        world = frame_in.get('world')
        if world not in maps.known_worlds():
            raise ValueError(f'地图坐标场景须指定已知世界：{", ".join(maps.known_worlds())}')
        frame = field._frame(world)
        spawn = maps.default_spawn(world)
        sim = {'supported': True, 'world': world, 'spawn': spawn, 'reason': None,
               'start_pose_after_undock': maps.start_pose_after_undock(spawn)}
        note = '用户导入的地图坐标（米）场景：仿真使用该世界默认生成位姿；地图间隙由每次求解的 map_check 报告。'
    elif frame_in.get('kind', 'benchmark') == 'benchmark':
        frame = dict(benchmark.BENCH_FRAME)
        sim = dict(benchmark.NO_SIM)
        note = '用户导入的基准坐标（无单位）场景。'
    elif frame_in.get('kind') == 'field':
        frame = {'kind': 'field', 'units': 'm', 'crs': str(frame_in.get('crs') or 'local')}
        sim = {'supported': False, 'world': None, 'spawn': None, 'reason': '实地田块坐标，尺度与仿真世界不同'}
        note = '用户导入的实地坐标（米）场景。'
    else:
        raise ValueError('frame.kind 须为 benchmark / map / field')
    from ..planning import validate_frame
    validate_frame(pc, frame)
    default_unit = {'turn_routing': 'cm_equivalent' if frame['kind'] == 'map' else 'synthetic_integer_cost',
                    'point_to_point': 'm', 'coverage': 'm' if frame['kind'] != 'benchmark' else 'unitless'}.get(pc, 'euclidean')
    source = obj.get('provenance') if isinstance(obj.get('provenance'), dict) else {}
    source_params = source.get('params') if isinstance(source.get('params'), dict) else {}
    # Keep generator inputs for reproducibility; importing never grants execution capability.
    try:
        source_params = json.loads(json.dumps(source_params, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError('生成参数须为有限数值组成的 JSON 对象') from exc
    source_seed = source.get('seed')
    if source_seed is not None and (isinstance(source_seed, bool) or not isinstance(source_seed, int)):
        raise ValueError('生成种子须为整数')
    scenario = {
        'id': sid, 'title': str(obj.get('title') or sid)[:80], 'summary': str(obj.get('summary') or '用户导入场景')[:400],
        'problem_class': pc, 'frame': frame, 'cost_unit': str(obj.get('cost_unit') or default_unit),
        'provenance': {'generator': 'user_import', 'params': source_params, 'seed': source_seed,
                       'source_generator': str(source.get('source_generator') or source.get('generator') or '')[:120],
                       'note': note + (' 原始说明：' + str(obj['provenance'].get('note'))[:300]
                                       if isinstance(obj.get('provenance'), dict) and obj['provenance'].get('note') else '')},
        'sim': sim, 'instance': inst,
        'editable': {'tasks': True, 'depot': frame['kind'] != 'map', 'capacity': pc != 'coverage', 'demands': pc != 'coverage'},
        'layers': obj.get('layers') if isinstance(obj.get('layers'), dict) else {},
        'origin': 'user',
    }
    return scenario


def import_scenario(obj: dict) -> dict:
    """Validate and store a new user scenario; returns its summary."""
    scenario = prepare_import(obj)
    sid = scenario['id']
    with _WRITE_LOCK:
        if (paths.USER_SCENARIOS / f'{sid}.json').exists():
            raise ScenarioConflict(f'场景 {sid} 已存在；请使用新 ID 导入，或在规划页编辑原场景')
        return summary(save_user_scenario(scenario, overwrite=False))


def save_user_scenario(scenario: dict, overwrite: bool = True, expected_revision: int | None = None) -> dict:
    from ..planning import without_stale_reference
    with _WRITE_LOCK:
        sid = str(scenario.get('id') or '').strip().lower() if isinstance(scenario, dict) else ''
        target = paths.USER_SCENARIOS / f'{sid}.json'
        old = json.loads(target.read_text()) if ID_RE.match(sid) and target.is_file() else None
        current = (old or {}).get('revision', 0)
        if expected_revision is not None and expected_revision != current:
            raise ScenarioConflict(f'场景已更新：当前版本 {current}，草稿基于 {expected_revision}；草稿已保留，请先另存副本或重新载入')
        if not isinstance(scenario, dict):
            raise ValueError('场景应为 JSON 对象')
        stored = copy.deepcopy(scenario)
        if old and old.get('reference'):
            checked = without_stale_reference(old, stored.get('instance') or {})
            if 'reference' not in checked:
                stored.pop('reference', None)
                stored['provenance'] = {**stored.get('provenance', {}),
                                         'reference_invalidated': checked['provenance']['reference_invalidated']}
        stored['revision'] = current + 1
        return _save_user_scenario(stored, overwrite)


def _save_user_scenario(scenario: dict, overwrite: bool = True) -> dict:
    """Validate and store a complete user scenario (composer output or an edited copy); returns the stored scenario."""
    if not isinstance(scenario, dict):
        raise ValueError('场景应为 JSON 对象')
    sid = str(scenario.get('id') or '').strip().lower()
    if not ID_RE.match(sid):
        raise ValueError('场景 id 须为 2–64 位小写字母、数字、-、_、.')
    if sid in field.MAP_SCENARIO_IDS or sid in motion.MAP_SCENARIO_IDS or sid in _builtin():
        raise ValueError('内置场景不能覆盖；请另存为新场景')
    target = paths.USER_SCENARIOS / f'{sid}.json'
    if target.exists() and not overwrite:
        raise ValueError(f'场景 {sid} 已存在')
    pc = scenario.get('problem_class')
    if pc not in PROBLEM_CLASSES:
        raise ValueError(f'未知问题类型 {pc}')
    stored = copy.deepcopy(scenario)
    stored.update(id=sid, origin='user', instance=validate_instance(pc, scenario.get('instance')))
    stored.setdefault('title', sid)
    stored.setdefault('summary', '')
    stored.setdefault('frame', {'kind': 'benchmark', 'units': 'unitless'})
    from ..planning import validate_frame
    validate_frame(pc, stored['frame'])
    stored.setdefault('cost_unit', 'm')
    stored.setdefault('sim', {'supported': False, 'world': None, 'spawn': None, 'reason': '非仿真地图'})
    stored.setdefault('editable', {'tasks': True, 'depot': True, 'capacity': True, 'demands': True})
    stored.setdefault('layers', {})
    stored.setdefault('provenance', {'generator': 'user', 'params': {}, 'seed': None, 'note': ''})
    paths.USER_SCENARIOS.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(target)
    return copy.deepcopy(stored)
