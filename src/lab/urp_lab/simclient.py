"""Gateway side of the ROS simulation session (CONTRACT §2.7, §4): state polling, execution, SimRun persistence."""
from __future__ import annotations

import json
import logging
import threading
import time

from .execution_timing import budget_metadata, freeze_plan_budget
from .events import EventBus, utc_now
from .jobs import new_id
from .services import ServiceError, ServiceManager, bridge_known_issues, http_json
from .store import Store

log = logging.getLogger('urp_lab.sim')

POLL_S = 0.25
IDLE_PROBE_S = 5.0
READY_TIMEOUT_S = 1200.0
SIM_FINAL = ('succeeded', 'failed', 'canceled')


def stopped_state(errors=None, warnings=None) -> dict:
    return {'session_id': None, 'phase': 'stopped', 'world': None, 'spawn': None, 'components': {}, 'robot': None,
            'execution': None, 'errors': errors or [], 'warnings': warnings or [], 'stages': [],
            'known_issues': bridge_known_issues()}


def new_sim_run(sim_run_id: str, solution: dict, session_id: str | None, world: str, mode: str, plan: dict) -> dict:
    tasks = [{'task': t.get('task'), 'label': str(t.get('label', t.get('task'))), 'xy': t.get('xy'),
              'state': 'pending', 't_sim': None, 'distance_m': None} for t in plan.get('tasks') or []]
    return {
        'sim_run_id': sim_run_id, 'run_id': solution['run_id'], 'session_id': session_id, 'world': world, 'mode': mode,
        'state': 'queued', 'sources': plan.get('sources') or {}, **budget_metadata(plan),
        'planned': {'path': plan.get('path') or [], 'length_m': plan.get('planned_length_m')},
        'tasks': tasks, 'actual': {'ground_truth': [], 'amcl': []}, 'nav2': [],
        'metrics': {'duration_sim_s': None, 'duration_wall_s': None, 'actual_length_m': None,
                    'cross_track_mean_m': None, 'cross_track_max_m': None, 'cross_track_p95_m': None,
                    'final_error_m': None, 'tasks_reached': None, 'tasks_total': len(tasks)},
        'error': None, 'report': None, 'created_utc': utc_now(), 'finished_utc': None,
    }


class SimClient:
    def __init__(self, store: Store, bus: EventBus | None, services: ServiceManager):
        self.store, self.bus, self.services = store, bus, services
        services.sim_client = self
        self.url = services.sim_url
        self.state = stopped_state()
        self._last_json = None
        self._persisted: dict[str, str] = {}
        self._waiting: dict[str, threading.Event] = {}  # sim_run_id → cancel flag while waiting for "ready"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fails = 0
        self._recover()

    def _recover(self):
        # Session files are atomically written after final metrics. Reconcile
        # them before declaring in-flight DB rows interrupted after a restart.
        for path in (self.services.runtime / 'sim').glob('*/runs/*.json'):
            try:
                full = json.loads(path.read_text())
                base = self.store.get_sim_run(full.get('sim_run_id', ''))
                if base and full.get('finished_utc') and full.get('state') in SIM_FINAL and base.get('run_id') == full.get('run_id'):
                    self.store.put_sim_run({**base, **full})
            except (OSError, ValueError):
                log.exception('cannot recover completed session record %s', path)
        status, st = http_json('GET', f'{self.url}/state', timeout=0.5)
        live = (st.get('execution') or {}) if status == 200 and isinstance(st, dict) else {}
        for run in self.store.active_sim_runs():
            if run['sim_run_id'] != live.get('sim_run_id'):
                self._finish_local(run, 'failed', '网关重启时该仿真执行已不在进行，结果未知（未伪造轨迹或指标）')

    # ------------------------------------------------------------- polling --
    def start(self):
        self._thread = threading.Thread(target=self._loop, name='urp-sim-poll', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        for flag in list(self._waiting.values()):
            flag.set()

    def _loop(self):
        next_probe = 0.0
        while not self._stop.is_set():
            try:
                if self.services.sim_alive():
                    self.poll_once()
                    self._stop.wait(POLL_S)
                    continue
                # A supervisor can fail before its HTTP socket is ever ready.
                # Surface that real exit even when our previous state is stopped.
                if self.services.sim_meta and self.services.sim_exit is None:
                    self.services.note_sim_exit()
                    if self.services.sim_exit:self._session_gone()
                if self.state.get('session_id') or self.state.get('phase') != 'stopped':
                    self._session_gone()
                if time.monotonic() >= next_probe:
                    next_probe = time.monotonic() + IDLE_PROBE_S
                    status, st = http_json('GET', f'{self.url}/state', timeout=0.5)
                    if status == 200 and isinstance(st, dict) and st.get('session_id'):
                        self.services.adopt_sim(st)
                        continue
            except Exception:
                log.exception('sim poll failed')
            self._stop.wait(1.0)

    def poll_once(self):
        status, st = http_json('GET', f'{self.url}/state', timeout=1.0)
        if status == 200 and isinstance(st, dict):
            self._fails = 0
            st.setdefault('known_issues', [])
            self._handle_execution(st.get('execution'))
            with self.services._lock:
                meta = self.services.sim_meta or self.services.sim_adopted or {}
                if meta.get('session_id') and st.get('session_id') != meta['session_id']:
                    return  # A response from the previous session arrived after restart.
                self._set_state(st)
            return
        self._fails += 1
        meta = self.services.sim_meta or self.services.sim_adopted or {}
        if self.state.get('session_id') != meta.get('session_id'):  # control API not up yet
            self._set_state({**stopped_state(), 'session_id': meta.get('session_id'), 'phase': 'starting',
                             'world': meta.get('world'), 'spawn': meta.get('spawn')})
        elif self._fails >= 3:
            self._set_state({**self.state, 'errors': [f'会话控制接口暂无响应：{st}']})

    def _set_state(self, st: dict):
        text = json.dumps(st, ensure_ascii=False, sort_keys=True)
        changed = text != self._last_json
        prev_phase = self.state.get('phase')
        self.state, self._last_json = st, text
        if changed and self.bus:
            self.bus.publish('sim_state', st)
        if prev_phase != st.get('phase'):
            self.services.changed()

    def _session_gone(self):
        with self.services._lock:
            if self.services.sim_alive():
                return  # A new owner can start between the poll loop's checks.
            self.services.note_sim_exit()
            exit_text = self.services.sim_exit
            for run in self.store.active_sim_runs():
                if run['sim_run_id'] in self._waiting:
                    continue
                self._finish_local(run, 'failed', f'仿真会话已结束，执行中断{"：" + exit_text if exit_text else ""}')
            steps = [s['text'] for s in self.services.sim_stop_report]
            state=stopped_state(errors=[exit_text] if exit_text else [], warnings=steps[-3:])
            if exit_text:state['phase']='failed'
            self._set_state(state)

    # ----------------------------------------------------------- execution --
    def _handle_execution(self, ex: dict | None):
        if not isinstance(ex, dict) or not ex.get('sim_run_id'):
            return
        rid, state = ex['sim_run_id'], ex.get('state')
        if self._persisted.get(rid) == state:
            return
        base = self.store.get_sim_run(rid)
        if base is None:
            return  # not started through this gateway
        record = {**base, **{k: v for k, v in ex.items() if v is not None or k in ('error',)}}
        if state in SIM_FINAL:
            status, full = http_json('GET', f'{self.url}/runs/{rid}', timeout=10)
            if status == 200 and isinstance(full, dict) and full.get('finished_utc'):
                record = {**base, **full}
            else:
                # Retry until metrics and stop observation are fully written.
                return
            record['finished_utc'] = record.get('finished_utc') or utc_now()
        self._persisted[rid] = state
        self.store.put_sim_run(record)
        if self.bus:
            self.bus.publish('sim_run', record)

    def _finish_local(self, run: dict, state: str, error: str):
        run.update(state=state, error=error, finished_utc=utc_now())
        self._persisted[run['sim_run_id']] = state
        self.store.put_sim_run(run)
        if self.bus:
            self.bus.publish('sim_run', run)

    def submit(self, solution: dict, scenario: dict, mode: str, *, autostart: bool = False,
               gui: bool | None = None, rviz: bool | None = None, budget_s=None,
               time_basis=None, wall_watchdog_s=None) -> dict:
        from .simplan import NotSimulatable, to_sim_plan

        if mode not in ('follow_path', 'nav2_waypoints'):
            raise ServiceError(400, 'invalid_mode', 'mode 须为 follow_path 或 nav2_waypoints')
        sim = scenario.get('sim') or {}
        frame = solution.get('frame') or {}
        if frame.get('kind') != 'map':
            raise ServiceError(422, 'not_simulatable', '基准算例坐标（无物理单位）不能直接送入仿真；请选择地图坐标场景')
        if not sim.get('supported'):
            raise ServiceError(422, 'not_simulatable', sim.get('reason') or '该场景不支持仿真')
        if solution.get('state') != 'done' or not solution.get('routes'):
            raise ServiceError(422, 'not_simulatable',
                               f"该运行没有可执行路线（状态 {solution.get('state')} / {solution.get('status')}）")
        try:
            plan = to_sim_plan(solution, scenario, mode)
        except NotSimulatable as exc:
            raise ServiceError(422, 'not_simulatable', str(exc)) from None
        try:
            plan = freeze_plan_budget(plan, budget_s=budget_s, time_basis=time_basis, wall_watchdog_s=wall_watchdog_s)
        except ValueError as exc:
            raise ServiceError(422, 'invalid_execution_budget', str(exc)) from None
        world = sim.get('world') or frame.get('world')
        with self._lock:
            active = [r for r in self.store.active_sim_runs()]
            if active:
                raise ServiceError(409, 'sim_busy', f"仿真正在执行 {active[0]['sim_run_id']}，请等待完成或取消")
            alive = self.services.sim_alive()
            if alive:
                session_world = (self.services.sim_meta or self.services.sim_adopted or {}).get('world') \
                    or self.state.get('world')
                if session_world and session_world != world:
                    raise ServiceError(409, 'world_mismatch',
                                       f'当前仿真会话的世界是 {session_world}，该方案属于 {world}；请以 {world} 重启仿真会话后再执行')
                if self.state.get('phase') in ('failed', 'stopping') or self.services._busy.get('sim'):
                    raise ServiceError(409, 'sim_not_ready', f"仿真会话状态为 {self.state.get('phase')}，不能执行")
            elif not autostart:
                raise ServiceError(409, 'sim_not_running', f'仿真会话未运行；请先以 {world} 启动会话，或使用 autostart')
            else:
                body = {'world': world, 'spawn': sim.get('spawn'), 'gui': bool(gui), 'rviz': bool(rviz)}
                self.services.start_sim(body)
            meta = self.services.sim_meta or self.services.sim_adopted or {}
            rid = new_id('sim_run')
            plan = {**plan, 'sim_run_id': rid, 'run_id': solution['run_id'], 'mode': mode}
            run = new_sim_run(rid, solution, meta.get('session_id'), world, mode, plan)
            self.store.put_sim_run(run)
            self._persisted[rid] = 'queued'
            flag = threading.Event()
            self._waiting[rid] = flag
        if self.bus:
            self.bus.publish('sim_run', run)
        threading.Thread(target=self._dispatch, args=(run, plan, flag), name=f'urp-sim-{rid}', daemon=True).start()
        return run

    def _dispatch(self, run: dict, plan: dict, canceled: threading.Event):
        rid = run['sim_run_id']
        try:
            t0 = time.monotonic()
            end = t0 + READY_TIMEOUT_S
            while not canceled.is_set():
                phase = self.state.get('phase')
                if self.services.sim_alive() and phase == 'ready' and self.state.get('session_id'):
                    break
                if not self.services.sim_alive() and not self.services._busy.get('sim') and time.monotonic() > t0 + 5:
                    self._finish_local(run, 'failed', f'仿真会话未能就绪：{self.services.sim_exit or "会话已退出"}')
                    return
                if phase == 'failed':
                    errors = '；'.join(self.state.get('errors') or []) or '会话报告失败'
                    self._finish_local(run, 'failed', f'仿真会话启动失败：{errors}')
                    return
                if time.monotonic() > end:
                    self._finish_local(run, 'failed', f'等待仿真会话就绪超过 {READY_TIMEOUT_S:.0f} s')
                    return
                time.sleep(0.25)
            if canceled.is_set():
                current = self.store.get_sim_run(rid) or run
                if current.get('state') not in SIM_FINAL:
                    self._finish_local(current, 'canceled', '在派发到仿真会话前取消')
                return
            run['session_id'] = self.state.get('session_id')
            self.store.put_sim_run(run)
            status, data = http_json('POST', f'{self.url}/execute', timeout=15, json=plan)
            if status != 202 and status != 200:
                msg = data.get('message') if isinstance(data, dict) else data
                self._finish_local(run, 'failed', f'仿真会话拒绝执行（{status}）：{msg}')
        finally:
            with self._lock:
                self._waiting.pop(rid, None)

    def cancel(self, sim_run_id: str) -> dict:
        run = self.store.get_sim_run(sim_run_id)
        if run is None:
            raise ServiceError(404, 'not_found', f'没有仿真运行 {sim_run_id}')
        if run.get('state') in SIM_FINAL:
            return run
        flag = self._waiting.get(sim_run_id)
        if flag is not None:
            flag.set()
            self._finish_local(run, 'canceled', '在派发到仿真会话前取消')
            return run
        status, data = http_json('POST', f'{self.url}/cancel', timeout=10, json={'sim_run_id': sim_run_id})
        if status is None:
            raise ServiceError(502, 'sim_session_error', f'仿真会话无响应，无法取消：{data}')
        if status >= 400:
            msg = data.get('message') if isinstance(data, dict) else status
            raise ServiceError(status, 'sim_session_error', f'会话拒绝取消：{msg}')
        return self.store.get_sim_run(sim_run_id) or run

    def get_run(self, sim_run_id: str) -> dict | None:
        run = self.store.get_sim_run(sim_run_id)
        ex = self.state.get('execution') or {}
        if run is not None and ex.get('sim_run_id') == sim_run_id and run.get('state') not in SIM_FINAL:
            run = {**run, **{k: v for k, v in ex.items() if v is not None}}
        return run

    def current_state(self) -> dict:
        with self.services._lock:
            st = dict(self.state)
            alive = self.services.sim_alive()
            meta = self.services.sim_meta or self.services.sim_adopted or {}
            if alive and st.get('session_id') != meta.get('session_id'):
                st = {**stopped_state(), 'session_id': meta.get('session_id'), 'phase': 'starting',
                      'world': meta.get('world'), 'spawn': meta.get('spawn')}
            if self.services._busy.get('sim') == 'stopping' and st.get('session_id'):
                st['phase'] = 'stopping'
            elif not alive and st.get('phase') not in ('stopped','failed'):
                st = stopped_state(errors=st.get('errors'))
                if st['errors']:st['phase']='failed'
            return st

    def set_window(self, key: str, on: bool) -> dict:
        self.services.sim_window(key, on)
        if self.services.sim_alive():
            self.poll_once()
        return self.current_state()
