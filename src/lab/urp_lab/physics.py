"""Physics runs: a verified plan executed in MuJoCo, and the gap to its Gazebo session when one exists.

A run takes seconds and starts no ROS process, so it needs no approval and can screen every candidate plan. The plan
comes either from the solver record, through the same world builder Gazebo uses, or from a Gazebo session's frozen
``plan.json``, which makes the two records directly comparable.

A batch runs one plan many times under seeded perturbations, one process per run, on the CPU only. It asks how sensitive the
plan is inside this simulation; ``physics_stats`` turns the runs into a distribution.
"""
from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import field_coordination, fieldworld, paths, physics_stats, sim2sim, time_windows
from .events import utc_now
from .jobs import new_id
from .mujoco_field import FACTORS, MAX_INTENSITY, supports
from .scenarios import validate_instance
from .solvers.fleet import check as check_fleet

log = logging.getLogger('urp_lab.physics')

ACTIVE = ('queued', 'running')
WALL_LIMIT_S = 600
DISPLAY_STRIDE = 25
SUMMARY = ('state', 'engine', 'engine_version', 'sim_time', 'wall_s', 'real_time_factor', 'metrics', 'vehicles', 'contacts', 'errors', 'servo_kv', 'model_sha256')
TRAFFIC = ('serial', 'coordinated')
BATCH_RUNS = 16
BATCH_MAX_RUNS = 256
BATCH_WALL_LIMIT_S = 900
BATCH_MAX_WALL_S = 3600
SEED_STRIDE = 100_000      # the runs of two batch seeds never share a draw
BATCH_ROW = ('index', 'seed', 'state', 'valid', 'reason', 'makespan_s', 'min_gap_m', 'holds', 'held_s', 'gate_margin_m')
CPU_ROOT = Path('/sys/devices/system/cpu')


class PhysicsError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def available() -> dict:
    try:
        return {'available': True, 'reason': None, 'version': importlib.metadata.version('mujoco')}
    except importlib.metadata.PackageNotFoundError:
        return {'available': False, 'reason': '环境中没有安装 mujoco', 'version': None}


def _base_frequency(cpu: int) -> int | None:
    try:
        return int((CPU_ROOT / f'cpu{cpu}' / 'cpufreq' / 'base_frequency').read_text())
    except (OSError, ValueError):
        return None


def _cpu_list(text: str) -> list[int]:
    """``8-23,30`` as a list of cores."""
    cores: list[int] = []
    for part in text.split(','):
        low, _, high = part.strip().partition('-')
        cores += range(int(low), int(high or low) + 1)
    return cores


def batch_cpus() -> list[int]:
    """Where a batch runs: the cores ``URP_PHYSICS_CPUS`` names; otherwise, on a hybrid CPU, its efficiency cores, which leaves
    the fast ones to the interface; otherwise every core this process may use but one."""
    allowed = sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else list(range(os.cpu_count() or 1))
    named = os.environ.get('URP_PHYSICS_CPUS', '').strip()
    if named:
        try:
            chosen = [c for c in _cpu_list(named) if c in allowed]
        except ValueError:
            chosen = []
        if chosen:
            return chosen
        log.warning('URP_PHYSICS_CPUS=%s names no usable core; using the default', named)
    base = {c: _base_frequency(c) for c in allowed}
    if None not in base.values() and len(set(base.values())) > 1:
        return [c for c in allowed if base[c] == min(base.values())]
    return allowed[:-1] or allowed


def sample_seed(seed: int, index: int) -> int:
    return seed * SEED_STRIDE + index


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
    windows = time_windows.by_task(inst)
    for v, r in zip(world['vehicles'], run['routes']):
        v['planned_length_m'], v['planned_duration_s'] = r.get('cost'), r.get('duration_s')
        for task in v['tasks']:
            if task['id'] in windows:
                task['earliest_s'], task['latest_s'] = windows[task['id']]
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
        self._batches: dict[str, tuple[threading.Event, threading.Thread]] = {}
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
            reason = supports(plan)
            if reason:
                raise PhysicsError(422, 'unsupported_plan', reason)
        else:
            plan, reference = self._frozen_plan(run_id, traffic), None
        out.mkdir(parents=True, exist_ok=True)
        (out / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf8')
        rec = {'physics_run_id': rid, 'run_id': run_id, 'session_id': session_id, 'scenario_id': plan.get('scenario_id'), 'algorithm': plan.get('algorithm'),
               'engine': 'mujoco', 'state': 'running', 'created_utc': utc_now(), 'finished_utc': None, 'progress': None, 'errors': [], 'traffic_mode': traffic}
        self._emit(rec)
        threading.Thread(target=self._run, args=(rec, reference), name=f'physics-{rid}', daemon=True).start()
        return rec

    def _frozen_plan(self, run_id: str | None, traffic: str) -> dict:
        """The plan of a solver record, ready for the engine, under this way of sharing the field."""
        run = self.store.get_run(str(run_id or ''))
        if not run:
            raise PhysicsError(404, 'unknown_run', '没有这个运行记录')
        with tempfile.TemporaryDirectory(prefix='physics-world-') as world:   # meshes and SDF are Gazebo's; only the plan is kept
            plan = plan_from_run(run, Path(world))
        if traffic == 'coordinated':
            plan = field_coordination.apply(plan)
            if plan['traffic'].get('coordination_refused'):
                raise PhysicsError(422, 'no_safe_order', plan['traffic']['coordination_refused'])
        reason = supports(plan)
        if reason:
            raise PhysicsError(422, 'unsupported_plan', reason)
        return plan

    def submit_batch(self, *, run_id: str | None, traffic: str = 'serial', runs: int = BATCH_RUNS, seed: int = 0, intensity: float = 1., factors: list[str] | None = None,
                     wall_limit_s: float = BATCH_WALL_LIMIT_S) -> dict:
        """Run one plan ``runs`` times under draws that follow from ``seed``; returns at once with the record.

        The draws depend on the seed and the run's index, not on which process finishes first, so a batch repeats exactly.
        Two plans given the same seed meet the same draws. ``wall_limit_s`` bounds the whole batch: runs still going then
        are stopped and counted as cut off, never as failures of the plan.
        """
        if not available()['available']:
            raise PhysicsError(503, 'engine_unavailable', available()['reason'])
        if traffic not in TRAFFIC:
            raise PhysicsError(400, 'invalid_request', f'traffic 只能是 {" 或 ".join(TRAFFIC)}')
        if isinstance(runs, bool) or not isinstance(runs, int) or not 1 <= runs <= BATCH_MAX_RUNS:
            raise PhysicsError(400, 'invalid_request', f'runs 必须是 1–{BATCH_MAX_RUNS} 的整数')
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 31:
            raise PhysicsError(400, 'invalid_request', 'seed 必须是非负整数')
        if isinstance(intensity, bool) or not isinstance(intensity, (int, float)) or not 0 <= intensity <= MAX_INTENSITY:
            raise PhysicsError(400, 'invalid_request', f'intensity 必须在 0–{MAX_INTENSITY:g} 之间')
        if isinstance(wall_limit_s, bool) or not isinstance(wall_limit_s, (int, float)) or not 1 <= wall_limit_s <= BATCH_MAX_WALL_S:
            raise PhysicsError(400, 'invalid_request', f'wall_limit_s 必须在 1–{BATCH_MAX_WALL_S} 秒之间')
        if factors is not None and (not isinstance(factors, list) or any(
                not isinstance(factor, str) or factor not in FACTORS for factor in factors)):
            raise PhysicsError(400, 'invalid_request', f'factors 只能取 {"、".join(FACTORS)}')
        chosen = list(FACTORS if factors is None else factors)
        plan = self._frozen_plan(run_id, traffic)
        rid = new_id('phys')
        out = self._dir(rid)
        (out / 'samples').mkdir(parents=True, exist_ok=True)
        (out / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf8')
        cpus = batch_cpus()
        traffic_of = plan.get('traffic') or {}
        rec = {'physics_run_id': rid, 'kind': 'batch', 'run_id': run_id, 'session_id': None, 'scenario_id': plan.get('scenario_id'), 'algorithm': plan.get('algorithm'),
               'engine': 'mujoco', 'engine_version': available()['version'], 'state': 'running', 'created_utc': utc_now(), 'finished_utc': None, 'errors': [], 'traffic_mode': traffic,
               'progress': {'runs_done': 0, 'runs_total': runs},
               'batch': {'runs': runs, 'seed': seed, 'intensity': float(intensity), 'factors': chosen, 'wall_limit_s': float(wall_limit_s), 'workers': min(runs, len(cpus)), 'cpus': cpus},
               'planned_makespan_s': (plan.get('planned') or {}).get('makespan_s'), 'predicted_makespan_s': traffic_of.get('predicted_makespan_s'), 'meetings': traffic_of.get('meetings')}
        stop = threading.Event()
        worker = threading.Thread(target=self._run_batch, args=(rec, stop), name=f'physics-{rid}', daemon=True)
        with self._lock:
            self._batches[rid] = (stop, worker)
        self._emit(rec)
        worker.start()
        return rec

    def _sample_process(self, rec: dict, index: int) -> subprocess.Popen:
        rid, spec = rec['physics_run_id'], rec['batch']
        out = self._dir(rid)
        draw = {'seed': sample_seed(spec['seed'], index), 'intensity': spec['intensity'], 'factors': spec['factors']}
        # A run is one thread of MuJoCo. Left alone, the numerical libraries would each start a pool the size of the machine.
        env = {**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
        with (out / 'samples' / f'{index:03d}.log').open('wb') as err:
            proc = subprocess.Popen([sys.executable, '-m', 'urp_lab.physics_worker', str(out / 'plan.json'), str(out / 'samples' / f'{index:03d}.json'), json.dumps(draw)],
                                    cwd=paths.LAB, stdout=subprocess.DEVNULL, stderr=err, env=env)
        if hasattr(os, 'sched_setaffinity'):
            try:
                os.sched_setaffinity(proc.pid, spec['cpus'])
            except OSError:
                pass          # already gone: its exit code tells the rest
        with self._lock:
            self._procs[f'{rid}/{index}'] = proc
        return proc

    def _sample_row(self, rec: dict, index: int, code: int) -> dict:
        """A finished run. An exit without a record is a result too: it says the run ended abnormally, not that the plan failed."""
        base = self._dir(rec['physics_run_id']) / 'samples' / f'{index:03d}'
        try:
            return {'index': index, **json.loads(base.with_suffix('.json').read_text(encoding='utf8'))}
        except (OSError, ValueError):
            tail = base.with_suffix('.log').read_text(encoding='utf8', errors='replace').strip().splitlines()[-1:] if base.with_suffix('.log').exists() else []
            return {'index': index, 'seed': sample_seed(rec['batch']['seed'], index), 'state': 'crashed', 'valid': False, 'reason': 'worker_crash',
                    'errors': [f'物理执行进程异常退出（退出码 {code}）', *tail]}

    def _run_batch(self, rec: dict, stop: threading.Event):
        rid, spec = rec['physics_run_id'], rec['batch']
        waiting, live, rows = list(range(spec['runs'])), {}, {}
        started = time.monotonic()
        deadline = started + spec['wall_limit_s']
        try:
            while (waiting or live) and not stop.is_set() and time.monotonic() < deadline:
                while waiting and len(live) < spec['workers']:
                    index = waiting.pop(0)
                    live[index] = self._sample_process(rec, index)
                for index, proc in list(live.items()):
                    if proc.poll() is None:
                        continue
                    del live[index]
                    with self._lock:
                        self._procs.pop(f'{rid}/{index}', None)
                    rows[index] = self._sample_row(rec, index, proc.returncode)
                    rec['progress'] = {'runs_done': len(rows), 'runs_total': spec['runs']}
                    self._emit(rec)
                stop.wait(.05)
        except Exception as exc:  # noqa: BLE001 - the record must end whatever happened here
            log.exception('physics batch %s failed', rid)
            self._close_batch(rec, rows, live, stop, f'批量运行中断：{exc}')
            return
        self._close_batch(rec, rows, live, stop, None, time.monotonic() - started)

    def _close_batch(self, rec: dict, rows: dict, live: dict, stop: threading.Event, failure: str | None, wall_s: float | None = None):
        rid, spec = rec['physics_run_id'], rec['batch']
        for index, proc in live.items():
            proc.kill()
            proc.wait()
            with self._lock:
                self._procs.pop(f'{rid}/{index}', None)
        unfinished = [{'index': i, 'seed': sample_seed(spec['seed'], i), 'state': 'cut_off' if i in live else 'not_run'} for i in range(spec['runs']) if i not in rows]
        ordered = sorted([*rows.values(), *unfinished], key=lambda r: r['index'])
        (self._dir(rid) / 'samples.json').write_text(json.dumps(ordered, ensure_ascii=False, allow_nan=False), encoding='utf8')
        rec['samples'] = [{**{k: r.get(k) for k in BATCH_ROW}, 'ground_friction': (r.get('perturbation') or {}).get('ground_friction')} for r in ordered]
        rec['summary'] = physics_stats.summarize(ordered, spec['runs'])
        rec['wall_s'] = round(wall_s, 2) if wall_s is not None else None
        with self._lock:
            self._batches.pop(rid, None)
        if failure:
            self._finish(rec, 'failed', [failure])
        elif stop.is_set():
            self._finish(rec, 'canceled', ['已取消'])
        elif unfinished:
            self._finish(rec, 'truncated', [f"到达总时长上限 {spec['wall_limit_s']:g} s，完成 {rec['summary']['finished']}/{spec['runs']} 次"])
        else:
            self._finish(rec, 'succeeded', [])

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
            rec['time_windows'] = time_windows.compact(result.get('time_windows'))
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
            proc, batch = self._procs.get(rid), self._batches.get(rid)
        if batch:
            batch[0].set()
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
        samples = self._dir(rid) / 'samples.json'
        if samples.exists():       # with each run's drawn values, which the record leaves out
            rec['samples'] = json.loads(samples.read_text(encoding='utf8'))
        if path.exists():
            result = json.loads(path.read_text(encoding='utf8'))
            rec['trails'] = {name: rows[::DISPLAY_STRIDE] + rows[-1:] for name, rows in (result.get('trails') or {}).items()}
            rec['tasks'] = result.get('tasks')
            rec['time_windows'] = result.get('time_windows')
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
            batches = list(self._batches.values())
        for stop, _ in batches:
            stop.set()
        for proc in procs:
            proc.kill()
        for _, worker in batches:
            worker.join(5)        # a batch closes its own record, and the store is about to close
