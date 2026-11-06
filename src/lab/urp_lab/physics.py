"""Physics runs: a verified plan executed in MuJoCo, and the gap to its Gazebo session when one exists.

A run takes seconds and starts no ROS process, so it needs no approval and can screen every candidate plan. The plan
comes either from the solver record, through the same world builder Gazebo uses, or from a Gazebo session's frozen
``plan.json``, which makes the two records directly comparable.
"""
from __future__ import annotations

import importlib.metadata
import json
import logging
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import field_coordination, fieldworld, paths, sim2sim
from .events import utc_now
from .jobs import new_id
from .mujoco_field import supports
from .scenarios import validate_instance
from .solvers.fleet import check as check_fleet

log = logging.getLogger('urp_lab.physics')

ACTIVE = ('queued', 'running')
WALL_LIMIT_S = 600
DISPLAY_STRIDE = 25
SUMMARY = ('state', 'engine', 'engine_version', 'sim_time', 'wall_s', 'real_time_factor', 'metrics', 'vehicles', 'contacts', 'errors', 'servo_kv', 'model_sha256')
TRAFFIC = ('serial', 'coordinated')


class PhysicsError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def available() -> dict:
    try:
        return {'available': True, 'reason': None, 'version': importlib.metadata.version('mujoco')}
    except importlib.metadata.PackageNotFoundError:
        return {'available': False, 'reason': '环境中没有安装 mujoco', 'version': None}


def plan_from_run(run: dict, out: Path) -> dict:
    """The plan Gazebo would receive for this run, with the same validation."""
    if run.get('problem_class') != 'fleet_routing' or run.get('state') != 'done' or not run.get('routes'):
        raise PhysicsError(422, 'not_fleet_plan', '只有已完成的多车任务方案可以进入物理执行')
    if run.get('status') not in ('feasible', 'optimal') or (run.get('check') or {}).get('valid') is not True:
        raise PhysicsError(422, 'unchecked_plan', '方案未通过独立检查，不能进入物理执行')
    try:
        inst = validate_instance('fleet_routing', run['instance'])
        checked = check_fleet(inst, run['routes'], run.get('params') or {})
        if not checked['valid']:
            raise ValueError('；'.join(checked['errors'][:5]))
        world = fieldworld.build(inst, run['routes'], out, 'physics', 4.0, None, None, inherit_ground=False)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise PhysicsError(422, 'invalid_plan', f'物理执行前重新验算失败：{exc}') from None
    for v, r in zip(world['vehicles'], run['routes']):
        v['planned_length_m'], v['planned_duration_s'] = r.get('cost'), r.get('duration_s')
    return {**world, 'run_id': run['run_id'], 'scenario_id': run['scenario_id'], 'algorithm': (run.get('algorithm') or {}).get('name'),
            'planned': {'makespan_s': max(r['duration_s'] for r in run['routes']), 'total_distance_m': checked['computed_cost']}}


def _traffic_summary(result: dict) -> dict:
    """How the vehicles shared the field in this run; the event list stays in the run directory."""
    t = result.get('traffic') or {}
    pairs = ((result.get('check') or {}).get('traffic') or {}).get('pairs') or []
    return {'policy': t.get('policy'), 'holds': sum(e.get('event') == 'hold' for e in t.get('events') or []),
            'held_s': round(sum((t.get('held_s') or {}).values()), 1) if t.get('held_s') else None,
            'predicted_makespan_s': t.get('predicted_makespan_s'), 'meetings': t.get('meetings'),
            'min_gap_m': round(min(p['min_footprint_gap_m'] for p in pairs), 3) if pairs else None}


class PhysicsManager:
    def __init__(self, store, runtime: Path, bus=None, fieldsim=None):
        self.store, self.runtime, self.bus, self.fieldsim = store, Path(runtime), bus, fieldsim
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._canceled: set[str] = set()
        for rec in store.list_physics_runs(limit=200):
            if rec.get('state') in ACTIVE:
                self._finish(rec, 'failed', ['网关重启时运行中断'])

    def _dir(self, rid: str) -> Path:
        return self.runtime / 'physics' / rid

    def _emit(self, rec: dict):
        self.store.put_physics_run(rec)
        if self.bus:
            self.bus.publish('physics_run', {k: v for k, v in rec.items() if k != 'trails'})

    def _finish(self, rec: dict, state: str, errors: list[str]):
        rec.update(state=state, errors=errors, finished_utc=utc_now())
        self._emit(rec)

    def _gazebo(self, session_id: str) -> tuple[dict, dict]:
        base = self.runtime / 'fieldsim' / session_id
        try:
            plan = json.loads((base / 'plan.json').read_text(encoding='utf8'))
            result = json.loads((base / 'result.json').read_text(encoding='utf8'))
        except (OSError, ValueError):
            raise PhysicsError(404, 'unknown_session', f'没有已结束的田间仿真记录 {session_id}') from None
        return plan, result

    def submit(self, *, run_id: str | None = None, session_id: str | None = None, traffic: str = 'serial') -> dict:
        """Start a run from a plan or from a finished Gazebo session; returns at once with the record.

        ``traffic`` is how the vehicles share the field: one mission at a time, or all at once through the temporal plan
        graph. A Gazebo record is replayed the way it was executed, so a comparison stays about the engines.
        """
        if not available()['available']:
            raise PhysicsError(503, 'engine_unavailable', available()['reason'])
        if traffic not in TRAFFIC:
            raise PhysicsError(400, 'invalid_request', f'traffic 只能是 {" 或 ".join(TRAFFIC)}')
        if session_id and traffic != 'serial':
            raise PhysicsError(422, 'frozen_traffic', '对比 Gazebo 记录时按原记录的交通方式重放')
        rid = new_id('phys')
        out = self._dir(rid)
        if session_id:
            plan, reference = self._gazebo(session_id)
            run_id = plan.get('run_id')
            # Frozen replay keeps the source policy as well as its label.
            traffic = 'coordinated' if (plan.get('traffic') or {}).get('policy') == field_coordination.POLICY else 'serial'
        else:
            run = self.store.get_run(str(run_id or ''))
            if not run:
                raise PhysicsError(404, 'unknown_run', '没有这个运行记录')
            with tempfile.TemporaryDirectory(prefix='physics-world-') as world:   # meshes and SDF are Gazebo's; only the plan is kept
                plan, reference = plan_from_run(run, Path(world)), None
            if traffic == 'coordinated':
                plan = field_coordination.apply(plan)
                if plan['traffic'].get('coordination_refused'):
                    raise PhysicsError(422, 'no_safe_order', plan['traffic']['coordination_refused'])
        reason = supports(plan)
        if reason:
            raise PhysicsError(422, 'unsupported_plan', reason)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf8')
        rec = {'physics_run_id': rid, 'run_id': run_id, 'session_id': session_id, 'scenario_id': plan.get('scenario_id'), 'algorithm': plan.get('algorithm'),
               'engine': 'mujoco', 'state': 'running', 'created_utc': utc_now(), 'finished_utc': None, 'progress': None, 'errors': [], 'traffic_mode': traffic}
        self._emit(rec)
        threading.Thread(target=self._run, args=(rec, reference), name=f'physics-{rid}', daemon=True).start()
        return rec

    def _run(self, rec: dict, reference: dict | None):
        rid = rec['physics_run_id']
        out = self._dir(rid)
        proc = subprocess.Popen([sys.executable, '-m', 'urp_lab.physics_worker', str(out / 'plan.json'), str(out / 'result.json')],
                                cwd=paths.LAB, stdout=subprocess.PIPE, stderr=(out / 'stderr.log').open('wb'), text=True)
        with self._lock:
            self._procs[rid] = proc
        killer = threading.Timer(WALL_LIMIT_S, proc.kill)
        killer.start()
        try:
            for line in proc.stdout:
                try:
                    note = json.loads(line)
                except ValueError:
                    continue
                if 'unsupported' in note:
                    rec['errors'] = [note['unsupported']]
                else:
                    rec['progress'] = note
                    self._emit(rec)
            code = proc.wait()
        finally:
            killer.cancel()
            with self._lock:
                self._procs.pop(rid, None)
        if rid in self._canceled:
            self._canceled.discard(rid)
            self._finish(rec, 'canceled', ['已取消'])
            return
        if code != 0 or not (out / 'result.json').exists():
            tail = (out / 'stderr.log').read_text(encoding='utf8', errors='replace').strip().splitlines()[-1:]
            self._finish(rec, 'failed', rec.get('errors') or tail or [f'物理执行进程退出码 {code}'])
            return
        try:
            result = json.loads((out / 'result.json').read_text(encoding='utf8'))
            rec.update({k: result.get(k) for k in SUMMARY})
            rec['check'] = {k: (result.get('check') or {}).get(k) for k in ('valid', 'errors', 'scope')}
            rec['traffic'] = _traffic_summary(result)
            if reference:
                rec['comparison'] = sim2sim.compare(reference, result)
            rec['finished_utc'] = utc_now()
            self._emit(rec)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.exception('reading physics result %s failed', rid)
            self._finish(rec, 'failed', [f'读取物理执行结果失败：{exc}'])

    def cancel(self, rid: str) -> dict | None:
        rec = self.store.get_physics_run(rid)
        if not rec:
            return None
        with self._lock:
            proc = self._procs.get(rid)
        if proc:
            self._canceled.add(rid)
            proc.kill()
        return rec

    def get(self, rid: str) -> dict | None:
        """The record with its trails at 2 Hz for display; the 50 Hz truth stays in the run directory."""
        rec = self.store.get_physics_run(rid)
        if not rec:
            return None
        path = self._dir(rid) / 'result.json'
        if path.exists():
            result = json.loads(path.read_text(encoding='utf8'))
            rec['trails'] = {name: rows[::DISPLAY_STRIDE] + rows[-1:] for name, rows in (result.get('trails') or {}).items()}
            rec['tasks'] = result.get('tasks')
        return rec

    def active(self) -> list[dict]:
        return [r for r in self.store.list_physics_runs(limit=50) if r.get('state') in ACTIVE]

    def wait(self, rid: str, cancel: threading.Event | None = None, timeout_s: float = WALL_LIMIT_S + 30) -> dict:
        stop = cancel or threading.Event()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rec = self.store.get_physics_run(rid)
            if rec is None or rec.get('state') not in ACTIVE:
                return rec or {}
            if stop.wait(.1):
                self.cancel(rid)
                stop = threading.Event()
        return self.store.get_physics_run(rid) or {}

    def shutdown(self):
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            proc.kill()
