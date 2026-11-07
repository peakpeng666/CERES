"""Gateway side of the multi-vehicle field simulation: Gazebo world from a fleet plan, the ROS 2
session process (src/lab/ros/field_sim_session.py), its live state and the saved results."""
from __future__ import annotations

import json
import copy
import logging
import math
import hashlib
import os
import shlex
import signal
import threading
from pathlib import Path

import shapely
from shapely.geometry import Polygon

from . import field_coordination, fieldworld, lanes, paths, time_windows
from .field_records import contact_metrics
from .field_traffic import POLICY as SERIAL_POLICY
from .jobs import new_id
from .services import ROS_SETUP, ManagedProcess, ServiceError, http_json
from .scenarios import validate_instance
from .solvers.fleet import check as check_fleet

log = logging.getLogger('urp_lab.fieldsim')

FIELD_PORT = 4192
SCRIPT_REL = 'src/lab/ros/field_sim_session.py'
OBSTACLE_HEX = {'pond': '#4a8cdc', 'ditch': '#5a8dbd', 'building': '#a9a9ad', 'greenhouse': '#cfe3ea',
                'rock': '#8a857d', 'tree': '#2f7d3a'}


def _traffic_mode(record: dict) -> str | None:
    return record.get('traffic_mode') or {SERIAL_POLICY: 'serial', field_coordination.POLICY: 'coordinated'}.get(
        (record.get('traffic') or {}).get('policy'))


def _hex(rgba) -> str:
    return '#' + ''.join(f'{round(c * 255):02x}' for c in rgba[:3])


def _triangles(poly: Polygon) -> list[list[float]]:
    out = []
    for part in fieldworld._polys(poly.buffer(0)):
        for t in shapely.constrained_delaunay_triangles(part).geoms:
            out.extend([round(x, 3), round(y, 3)] for x, y in list(t.exterior.coords)[:3])
    return out


def marker_layers(inst: dict) -> tuple[list[dict], list[dict]]:
    """Filled field layers and outlines for the ROS view (visualization_msgs TRIANGLE_LIST / LINE_STRIP)."""
    boundary = Polygon(inst['boundary'])
    layers = [{'ns': 'field', 'color': _hex(fieldworld.SOIL), 'alpha': 1.0, 'z': 0.0, 'triangles': _triangles(boundary)}]
    outlines = [{'ns': 'boundary', 'color': '#5c4a36', 'width': 0.3, 'z': 0.04, 'points': [list(p) for p in boundary.exterior.coords]}]
    net = lanes.Network(inst)
    for plot, rows in zip(inst.get('plots', []), net.plots):
        poly = Polygon(plot['polygon'])
        name = plot.get('name') or plot.get('id')
        layers.append({'ns': f'plot {name}', 'color': _hex(tuple(c * 0.82 for c in fieldworld.SOIL[:3])), 'alpha': 0.9, 'z': 0.02,
                       'triangles': _triangles(poly)})
        if rows.rows:
            layers.append({'ns': f'rows {name}', 'color': _hex(rows.rgba), 'alpha': 1.0, 'z': 0.03,
                           'triangles': [xy for row in rows.rows for xy in _triangles(row)]})
        outlines.append({'ns': 'plots', 'color': '#3f6b2a', 'width': 0.12, 'z': 0.05, 'points': [list(p) for p in poly.exterior.coords]})
    for o in inst.get('obstacles', []):
        layers.append({'ns': f"obstacle {o.get('kind', '')}", 'color': OBSTACLE_HEX.get(o.get('kind', ''), '#a9a9ad'),
                       'alpha': 0.9, 'z': 0.06, 'triangles': _triangles(Polygon(o['polygon']))})
    return layers, outlines


class FieldSimManager:
    def __init__(self, store, services, runtime: Path | None = None, *, port: int = FIELD_PORT):
        self.store, self.services = store, services
        self.runtime = Path(runtime or paths.RUNTIME)
        self.port = port
        self.url = f'http://127.0.0.1:{port}'
        self.proc: ManagedProcess | None = None
        self.meta: dict | None = None
        self.last: dict | None = None
        self._result_cache = {}
        self._result_lock = threading.Lock()
        # Share the TB4 lifecycle lock: two different HTTP endpoints must not start ROS worlds together.
        self._lock = services._lock
        services.field_sim = self

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.alive

    # -------------------------------------------------------------- start --
    def start(self, body: dict) -> dict:
        traffic = body.get('traffic', 'serial')
        if traffic not in ('serial', 'coordinated'):
            raise ServiceError(400, 'invalid_traffic', 'traffic 只能是 serial 或 coordinated')
        run = self.store.get_run(str(body.get('run_id') or ''))
        if not run:
            raise ServiceError(404, 'unknown_run', '没有这个运行记录')
        if run.get('problem_class') == 'field_operation':
            from .agri.execution import NotExecutable, fleet_view
            try:
                run = fleet_view(run)
            except NotExecutable as exc:
                raise ServiceError(422, 'not_executable', str(exc)) from None
        if run.get('problem_class') != 'fleet_routing' or run.get('state') != 'done' or not run.get('routes'):
            raise ServiceError(422, 'not_fleet_plan', '只有已完成的多车任务方案可以在 Gazebo 中执行')
        try:
            value = body.get('real_time_factor', 4.0)
            if isinstance(value, bool):
                raise ValueError
            rtf = float(value)
        except (TypeError, ValueError):
            raise ServiceError(400, 'invalid_rtf', '仿真倍速需为 0.5–20 之间的有限数字') from None
        if not math.isfinite(rtf) or not 0.5 <= rtf <= 20:
            raise ServiceError(400, 'invalid_rtf', '仿真倍速需在 0.5–20 之间')
        if run.get('status') not in ('feasible', 'optimal') or (run.get('check') or {}).get('valid') is not True:
            raise ServiceError(422, 'unchecked_plan', '方案未通过独立检查，不能进入仿真')
        if (run.get('instance') or {}).get('unload_points') and (run.get('raw_result') or {}).get('model_contract', {}).get('schema') != 'urp.fleet-model/2':
            raise ServiceError(422, 'obsolete_resupply_model', '补给方案来自旧模型或缺少版本证据，请重新求解并验算；原记录保留')
        try:
            inst = validate_instance('fleet_routing', run['instance'])
            checked = check_fleet(inst, run['routes'], run.get('params') or {})
            if not checked['valid']:
                raise ValueError('；'.join(checked['errors'][:5]))
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise ServiceError(422, 'invalid_plan', f'仿真前重新验算失败：{exc}') from None
        from .assets.bindings import field_vehicle
        vehicle_asset = field_vehicle(run, body.get('vehicle_asset'))
        try:
            from .ground_execution import resolve
            execution = resolve(body.get('execution'), inst, run['routes'])
        except ValueError as exc:
            raise ServiceError(422, 'invalid_execution', f'非理想执行配置无效：{exc}') from None
        with self._lock:
            if self.alive:
                raise ServiceError(409, 'fieldsim_running', '已有田间仿真在运行，请先停止')
            if self.services.sim_alive():
                raise ServiceError(409, 'sim_running', '机器人仿真会话正在运行，请先在仿真工作区停止它')
            status, _ = http_json('GET', f'{self.url}/state', timeout=0.5)
            if status is not None:
                raise ServiceError(409, 'port_busy', f'端口 {self.port} 已被占用（可能是未关闭的田间仿真）')
            sid = new_id('field')
            out = self.runtime / 'fieldsim' / sid
            try:
                world = fieldworld.build(inst, run['routes'], out, 'field_' + sid.rsplit('_', 1)[-1], rtf,
                                         body.get('physics'), execution, inherit_ground=False)
            except ValueError as exc:
                raise ServiceError(422, 'world_failed', str(exc)) from None
            windows = time_windows.by_task(inst)
            for v, r in zip(world['vehicles'], run['routes']):
                v['planned_length_m'] = r.get('cost')
                v['planned_duration_s'] = r.get('duration_s')
                for task in v['tasks']:
                    if task['id'] in windows:
                        task['earliest_s'], task['latest_s'] = windows[task['id']]
            layers, outlines = marker_layers(inst)
            plan = {**world, 'session_id': sid, 'run_id': run['run_id'], 'scenario_id': run['scenario_id'],
                    'algorithm': (run.get('algorithm') or {}).get('name'), 'field_layers': layers, 'outlines': outlines,
                    'planning_check': checked, 'planning_solution': run,
                    'vehicle_asset': vehicle_asset,
                    'planned': {'makespan_s': max(r['duration_s'] for r in run['routes']),
                                'total_distance_m': checked['computed_cost']}}
            if traffic == 'coordinated':
                plan = field_coordination.apply(plan)
                if plan['traffic'].get('coordination_refused'):
                    raise ServiceError(422, 'no_safe_order', plan['traffic']['coordination_refused'])
            sources = [paths.REPO / SCRIPT_REL, paths.LAB / 'urp_lab/field_tracking.py',
                       paths.LAB / 'urp_lab/path_geometry.py', paths.LAB / 'urp_lab/fieldcheck.py',
                       paths.LAB / 'urp_lab/field_traffic.py', paths.LAB / 'urp_lab/field_coordination.py',
                       paths.LAB / 'urp_lab/time_windows.py', paths.LAB / 'urp_lab/sim2sim.py', paths.LAB / 'urp_lab/assets/bindings.py',
                       paths.LAB / 'urp_lab/field_records.py',
                       paths.LAB / 'urp_lab/field_physics.py',
                       paths.LAB / 'urp_lab/field_actuation.py',
                       paths.LAB / 'urp_lab/ground_risk.py',
                       paths.LAB / 'urp_lab/ground_map.py',
                       paths.LAB / 'urp_lab/ground_execution.py',
                       paths.LAB / 'urp_lab/field_dynamics.py',
                       paths.LAB / 'urp_lab/field_nonideal.py', paths.LAB / 'urp_lab/field_localization.py', paths.LAB / 'urp_lab/field_observation_check.py',
                       paths.LAB / 'urp_lab/field_energy.py',
                       paths.LAB / 'urp_lab/fieldworld.py', paths.LAB / 'urp_lab/lanes.py',
                       paths.LAB / 'ros/processes.py', paths.LAB / 'ros/physics_runtime.py', paths.LAB / 'ros/bridge_runtime.py',
                       paths.LAB / 'ros/native/bridge/manifest.json', paths.LAB / 'ros/joint_state_runtime.py',
                       *sorted((paths.LAB / 'ros/native/joint_state').glob('*'))]
            localization_note = ('Gazebo ground truth (ideal localization)' if not execution or execution['localization']['gnss'] is None else
                                 'wheel odometry fused with synthetic absolute fixes under a declared error model; '
                                 'truth recorded separately for independent evaluation only')
            plan['execution_provenance'] = {
                'source_sha256': {paths.rel(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                'world_sha256': hashlib.sha256(Path(world['sdf']).read_bytes()).hexdigest(),
                'coordinates': 'task map = Gazebo world = ROS map, metres',
                'localization': localization_note,
                'physics_model_sha256': world['physics']['sha256'],
                'mission_success_semantics': 'geometric path/access-stop/dwell verification; no agricultural work quality or tool reach validation',
                'controller': 'URP field path follower; not Nav2 replanning',
                'bridge_shutdown_fix_requested': os.environ.get('URP_BRIDGE_FIX', '1') != '0',
                'bridge_exit_guard': False,
                'comparison_scope': 'Actual distance/time include both parking transfers, turns and traffic queueing; planned metrics exclude them.'}
            (out / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1))
            args = ['--runtime', str(out), '--port', str(self.port)]
            args += ['--no-web'] if body.get('web') is False else []
            args += ['--gui'] if body.get('gui') else []
            args += ['--rviz'] if body.get('rviz') else []
            inner = f'source {ROS_SETUP} && exec bash setup/profile.sh simulation /usr/bin/python3 {SCRIPT_REL} ' + shlex.join(args)
            self.proc = ManagedProcess(f'fieldsim-{sid}', ['bash', '-c', inner], paths.REPO,
                                       self.runtime / 'logs' / f'fieldsim-{sid}.log', env=dict(os.environ))
            self.proc.start()
            self.meta = {'session_id': sid, 'run_id': run['run_id'], 'scenario_id': run['scenario_id'], 'world': world['world'],
                         'vehicles': len(world['vehicles']), 'real_time_factor': rtf, 'dir': str(out), 'traffic_mode': traffic,
                         'execution': ({'mode': execution['mode'],
                                        'ground': execution['ground']['preset'],
                                        'localization': execution['localization']['preset'],
                                        'seed': execution['seed']} if execution else {'mode': 'ideal'})}
            self.last = None
            log.info('field sim %s started for run %s (%d vehicles)', sid, run['run_id'], len(world['vehicles']))
        return self.state()

    # -------------------------------------------------------------- state --
    def state(self) -> dict:
        meta = self.meta or {}
        if self.alive:
            status, st = http_json('GET', f'{self.url}/state', timeout=0.8)
            if status == 200 and isinstance(st, dict) and st.get('session_id') == meta.get('session_id'):
                self.last = st
                return {**st, 'running': True}
            if self.last:
                st = copy.deepcopy(self.last)
                st.update(phase='failed', running=True, web={})
                st.setdefault('errors', []).append('会话控制接口失联；进程仍在运行，可尝试停止并查阅日志')
                return st
            return {**meta, 'phase': 'starting', 'running': True, 'vehicles': [], 'components': {}, 'web': {},
                    'errors': [], 'warnings': []}
        if meta:
            st = copy.deepcopy(self.last or {**meta, 'vehicles': [], 'components': {}, 'web': {}, 'errors': [], 'warnings': []})
            try:
                saved = json.loads((Path(meta['dir']) / 'state.json').read_text())
                if saved.get('session_id') == meta.get('session_id'):
                    st = saved
            except (OSError, ValueError, KeyError):
                pass
            st['running'] = False
            st['web'] = {}
            if st.get('phase') not in ('finished', 'failed'):
                st['phase'] = 'stopped'
            rc = self.proc.returncode if self.proc is not None else None
            st['returncode'] = rc
            if rc not in (0, None):
                st['phase'] = 'failed'
                st.setdefault('errors', []).append(f'田间仿真进程退出，返回码 {rc}；见日志 fieldsim-{meta["session_id"]}')
            return st
        return {'phase': 'idle', 'running': False, 'vehicles': [], 'components': {}, 'web': {}, 'errors': [], 'warnings': []}

    def window(self, kind: str, on: bool) -> dict:
        if kind not in ('gui', 'rviz'):
            raise ServiceError(400, 'invalid_window', '窗口只能是 gui 或 rviz')
        if not self.alive:
            raise ServiceError(409, 'not_running', '田间仿真没有运行')
        status, reply = http_json('POST', f'{self.url}/{kind}', timeout=3, json={'on': bool(on)})
        if status not in (200, 202):
            raise ServiceError(503, 'session_unreachable', f'仿真窗口控制失败：{reply}')
        return self.state()

    # -------------------------------------------------------------- stop --
    def stop(self, *, session_id: str | None = None) -> dict:
        with self._lock:
            # Check ownership under the same lock as start: a paused watcher must
            # never stop a replacement session between its state read and stop.
            if session_id is not None and (self.meta or {}).get('session_id') != session_id:
                return self.state()
            return self._stop()

    def _stop(self) -> dict:
        if not self.alive:
            return self.state()
        assert self.proc is not None
        self.proc.stop_requested = True
        self.state()  # keep the last live state for the stopped view
        http_json('POST', f'{self.url}/shutdown', timeout=3, json={})
        if not self.proc.wait(45):
            for sig, grace in ((signal.SIGINT, 10), (signal.SIGTERM, 6), (signal.SIGKILL, 4)):
                self.proc.signal_group(sig)
                if self.proc.wait(grace):
                    break
        log.info('field sim %s stopped rc=%s', (self.meta or {}).get('session_id'), self.proc.returncode)
        return self.state()

    def shutdown(self):
        if self.alive:
            log.info('gateway exit: stopping field simulation')
            self.stop()

    # ------------------------------------------------------------ results --
    def results(self, run_id: str | None = None) -> list[dict]:
        # The UI polls this list; parsing every historical trajectory at every poll scales
        # with all recorded samples. Cache compact summaries, invalidating on atomic writes.
        with self._result_lock:
            return self._results(run_id)

    def _results(self, run_id: str | None = None) -> list[dict]:
        rows = []
        files = sorted((self.runtime / 'fieldsim').glob('*/result.json'), reverse=True)
        for gone in self._result_cache.keys() - set(files):
            del self._result_cache[gone]
        for path in files:
            try:
                stat = path.stat()
                key = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                cached = self._result_cache.get(path)
                if cached and cached[0] == key:
                    r = cached[1]
                    if not run_id or r.get('run_id') == run_id:
                        rows.append(copy.deepcopy(r))
                    continue
                r = json.loads(path.read_text())
                r.update(contact_metrics(r))
            except (OSError, ValueError):
                continue
            row = ({k: r.get(k) for k in ('session_id', 'run_id', 'scenario_id', 'algorithm', 'world', 'finished_utc',
                                               'state', 'metrics', 'real_time_factor', 'sim_time', 'travel', 'row_collisions',
                                               'row_collision_shapes', 'robot_contact_events', 'contact_observation', 'execution', 'traffic_mode')}
                        | {'traffic_mode': _traffic_mode(r),
                           'check': {k: (r.get('check') or {}).get(k) for k in ('valid', 'errors')},
                           'time_windows': time_windows.compact(r.get('time_windows'))}
                        | {'vehicles': [{k: v.get(k) for k in ('name', 'label', 'color', 'state', 'distance_m', 'duration_s',
                                                                'planned_length_m', 'planned_duration_s', 'tasks_done',
                                                                'tasks_total', 'cross_track_mean_m', 'cross_track_max_m')}
                                        for v in r.get('vehicles', [])]})
            self._result_cache[path] = (key, row)
            if not run_id or row.get('run_id') == run_id:
                rows.append(copy.deepcopy(row))
        return rows

    def result(self, session_id: str) -> dict:
        path = self.runtime / 'fieldsim' / session_id / 'result.json'
        if '/' in session_id or not path.is_file():
            raise ServiceError(404, 'unknown_result', '没有这次田间仿真的结果')
        result = json.loads(path.read_text())
        result.setdefault('traffic_mode', _traffic_mode(result))
        try:
            plan = json.loads(path.with_name('plan.json').read_text())
        except (OSError, ValueError):
            result['provenance_warning'] = '缺少会话计划文件，无法补齐规划输入和执行配置'
            result.update(contact_metrics(result))
            return result
        result.update(contact_metrics(result, plan))
        result['planning_solution'] = plan.get('planning_solution')
        result['execution_provenance'] = plan.get('execution_provenance')
        result['execution_plan'] = {k: plan.get(k) for k in ('vehicles', 'instance', 'robot_radius_m', 'travel', 'world', 'bounds', 'traffic', 'vehicle_asset')}
        report_path = path.with_name('session-report.json')
        result['session_report'] = json.loads(report_path.read_text()) if report_path.is_file() else None
        return result
