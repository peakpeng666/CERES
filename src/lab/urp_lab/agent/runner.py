"""Phase-gated agent runner: understand → confirm → execute → report, with pause, cancel, retry, approval and takeover.

The model proposes tool calls; the runner validates them, executes them through ``tools`` and records the real
results. Verification is mandatory: every run that reports a feasible plan is independently re-checked before the
report, whether or not the model asked for it. Without a usable model the same phases run on deterministic rules,
and the session says so.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import threading
import time
from pathlib import Path

from . import contract as tc
from . import diagnose, llm, prompts, providers, tools
from .session import (AgentStore, add_message, add_usage, claims_check, ensure_step, new_session, public,
                      reset_execution_plan, set_step, utc_now)

log = logging.getLogger('urp_lab.agent')
AFFIRM = re.compile(r'^\s*(确认|确定|开始|开始执行|执行|可以|好的?|同意|批准|没问题|就这样|ok|okay|go|yes)[。.!！\s]*$', re.I)
DENY = re.compile(r'^\s*(不|不要|拒绝|否|取消|不同意|no)[。.!！\s]*$', re.I)
QUESTIONS = {
    'task_count': ('需要服务多少个任务点？', ['20', '40', '60']),
    'vehicle_count': ('投入几台机器人？', ['1', '2', '3', '4']),
    'field_size_m': ('场地大约多大？可以给出长 × 宽（米）或面积（亩、公顷）。', ['200×120 米', '50 亩', '5 公顷']),
}
RUNNER_VERSION = 'agent-runner/5'
MAX_LLM_CALLS = 20
MAX_TOOL_CALLS = 30
MAX_UNDERSTAND_CALLS = 4
CONTEXT_MESSAGES = 40


class Paused(Exception):
    pass


class Cancelled(Exception):
    pass


class Budget(Exception):
    pass


class ToolFailure(Exception):
    """A tool failed for a reason the agent must not work around (environment or implementation error)."""

    def __init__(self, rec: dict):
        super().__init__(rec['error']['message'])
        self.rec = rec


FATAL_TOOL_ERRORS = {'source_changed', 'shutting_down', 'tool_exception'}


class RunnerError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class AgentRunner:
    def __init__(self, lab, path: Path | None = None, chat=None, providers=None):
        self.lab = lab
        self.store = AgentStore(path or Path(lab.runtime) / 'agent.sqlite3')
        self.tools = tools.registry()
        self.chat = chat or llm.chat
        self._providers = providers           # tests inject a provider list
        self._mutex = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._pause: dict[str, threading.Event] = {}
        self.recover()

    # ------------------------------------------------------------- public --
    def recover(self):
        for s in self.store.by_state(('running',)):
            s['state'] = 'interrupted'
            add_message(s, 'event', '网关重启时本会话正在执行，已中断；可点击“继续”从当前阶段恢复。', 'notice')
            self.store.save(s)

    def providers(self) -> dict:
        return {**providers.overview(), 'prompt_version': prompts.VERSION}

    def _find_provider(self, ref: str | None) -> llm.Provider | None:
        if self._providers is not None:          # tests inject their own list
            return next((p for p in self._providers if p.id == ref), None)
        return providers.resolve(ref)

    def _resolve_provider(self, provider_id: str | None) -> tuple[llm.Provider | None, str | None]:
        ref = provider_id or (self._providers[0].id if self._providers else None) or providers.selected()
        if ref is None:
            return None, '还没有连接模型服务商'
        p = self._find_provider(ref)
        return (p, None) if p else (None, f'模型 {ref} 不可用，或它的服务商尚未连接')

    def create(self, body: dict) -> dict:
        mode = body.get('mode') or 'auto'
        if mode not in ('auto', 'llm', 'hybrid', 'rule'):
            raise RunnerError(400, 'invalid_mode', 'mode 须为 auto / llm / hybrid / rule')
        provider, reason = (None, '用户选择规则流程') if mode == 'rule' else self._resolve_provider(body.get('provider_id'))
        if provider is None and mode in ('llm', 'hybrid'):
            raise RunnerError(409, 'llm_unavailable', f'无法使用大模型：{reason}')
        actual = 'rule' if provider is None else ('hybrid' if mode == 'auto' else mode)
        info = None if provider is None else {k: provider.public()[k] for k in ('id', 'kind', 'model', 'label')}
        s = new_session(actual, info, bool(body.get('auto_confirm')), str(body.get('title') or '')[:60])
        if provider is None:
            add_message(s, 'event', f'本会话使用规则流程（未调用大模型）：{reason}。理解与执行均由确定性规则完成，结果同样经独立检查。', 'notice')
        if body.get('scenario_id'):
            self._bind_map(s, str(body['scenario_id']))
        self.store.save(s)
        self._publish(s)
        text = str(body.get('text') or '').strip()
        return self.message(s['session_id'], text) if text else self._view(s)

    def get(self, session_id: str) -> dict:
        return self._view(self._load(session_id))

    def _bind_map(self, s: dict, scenario_id: str):
        """Start a task on an existing map: its facts enter the contract as map-sourced values, and the map step is done."""
        from ..scenarios import get_scenario
        try:
            sc = get_scenario(scenario_id)
        except KeyError:
            raise RunnerError(404, 'not_found', '没有该任务地图') from None
        if sc.get('problem_class') != 'fleet_routing':
            raise RunnerError(400, 'invalid_scenario', '智能体目前只接手多车任务地图')
        inst = sc['instance']
        veh = inst.get('vehicles') or {}
        xs, ys = [p[0] for p in inst['boundary']], [p[1] for p in inst['boundary']]
        values = {'field_size_m': [round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)], 'task_count': len(inst['tasks']),
                  'vehicle_count': veh.get('count') or len(veh.get('fleet') or []) or None, 'vehicle_capacity': veh.get('capacity'),
                  'vehicle_speed_mps': veh.get('speed_mps'), 'objective': inst.get('objective'), 'trip_mode': veh.get('trips'), 'travel_mode': veh.get('travel')}
        s['contract'], _, _ = tc.apply(s['contract'], {k: v for k, v in values.items() if v is not None}, 'user_edit', note=f"来自任务地图 {sc['title']}")
        self._takeover_locked(s, {'step': 'scenario', 'scenario_id': scenario_id, 'note': f"使用任务地图「{sc['title']}」"})

    def list(self) -> list[dict]:
        return self.store.list()

    def _view(self, s: dict) -> dict:
        return public(s) | self.store.meta(s['session_id'])

    def patch_session(self, session_id: str, body: dict) -> dict:
        """Name, pin or archive a session. Archiving only hides it from the list; every record stays."""
        self._load(session_id)
        fields: dict = {}
        if 'title' in body:
            fields['custom_title'] = (str(body['title'] or '').strip()[:60]) or None
        for key in ('pinned', 'archived'):
            if key in body:
                fields[key] = bool(body[key])
        if not fields:
            raise RunnerError(400, 'invalid_body', '没有可修改的字段')
        meta = self.store.set_meta(session_id, fields)
        bus = getattr(self.lab, 'bus', None)
        if bus is not None:
            bus.publish('agent_session', {'session_id': session_id, 'kind': 'meta'})
        return {'session_id': session_id} | meta

    def harness(self) -> dict:
        """What the agent may do: every tool with its stage gate, and the per-turn limits the runner enforces."""
        approval = {'start_simulation', 'propose_task_change'}
        items = [{'name': t.name, 'label': t.label, 'description': t.description, 'effect': t.effect, 'step': t.step,
                  'needs_scenario': t.needs_scenario, 'needs_confirmed': t.needs_confirmed, 'needs_approval': t.name in approval,
                  'phases': [phase for phase, names in tools.PHASE_TOOLS.items() if t.name in names]} for t in self.tools.values()]
        return {'tools': items, 'limits': {'model_calls_per_turn': MAX_LLM_CALLS, 'tool_calls_per_turn': MAX_TOOL_CALLS},
                'prompt_version': prompts.VERSION, 'runner_version': RUNNER_VERSION}

    def evals(self) -> dict:
        """Stored agent evaluations, newest first. Read-only: the evidence directories are never changed."""
        from ..paths import REPO
        out = []
        root = REPO / 'experiments' / 'agent-evals'
        for path in sorted((p for p in root.glob('*') if p.is_dir()), reverse=True):
            try:
                if (path / 'records.json').exists():
                    data = json.loads((path / 'records.json').read_text(encoding='utf8'))
                    meta = data.get('meta') or {}
                    out.append({'id': path.name, 'kind': 'execution', 'mode': meta.get('mode_requested'), 'provider': meta.get('provider_id'),
                                'prompt_version': meta.get('prompt_version'), 'runner_version': meta.get('runner_version'),
                                'finished_utc': meta.get('finished_utc'), 'passed': meta.get('passed'), 'total': meta.get('scenarios'),
                                'cases': [{'id': r.get('scenario'), 'passed': r.get('pass'), 'state': r.get('state'), 'note': r.get('error')} for r in data.get('records', [])]})
                elif (path / 'summary.json').exists():
                    data = json.loads((path / 'summary.json').read_text(encoding='utf8'))
                    meta = data.get('meta') or {}
                    allv = (data.get('summary') or {}).get('all') or {}
                    out.append({'id': path.name, 'kind': 'understanding', 'mode': meta.get('mode_requested'), 'provider': meta.get('provider_id'),
                                'split': meta.get('split'), 'prompt_version': meta.get('prompt_version'), 'finished_utc': meta.get('finished_utc'),
                                'metrics': {'f1': allv.get('field_f1'), 'exact_match': allv.get('exact_match'), 'turns': allv.get('turns')}})
            except (OSError, ValueError):
                continue
        return {'runs': out}

    def export(self, session_id: str) -> dict:
        """One reproducible bundle: session (contract, plan, tool and model logs), scenario and every run record."""
        import hashlib
        from ..scenarios import get_scenario
        s = self._load(session_id)
        scenarios = {}
        for sid in [x for x in [s.get('scenario_id'), *(s.get('previous_scenarios') or [])] if x]:
            try:
                scenarios[sid] = get_scenario(sid)
            except KeyError:
                scenarios[sid] = None
        runs = {rid: self.lab.store.get_run(rid) for rid in s.get('run_ids', [])}
        record = {'schema': 'urp.agent-export/1', 'session': public(s), 'scenarios': scenarios, 'runs': runs,
                  'prompt_version': prompts.VERSION, 'exported_utc': utc_now()}
        canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
        return {'record': record, 'integrity': {'algorithm': 'sha256', 'sha256': hashlib.sha256(canonical.encode()).hexdigest(),
                                                 'scope': 'record（规范化 JSON，键排序）；完整性校验，不是数字签名'}}

    def message(self, session_id: str, text: str) -> dict:
        text = (text or '').strip()
        if not text or len(text) > 2000:
            raise RunnerError(400, 'invalid_message', '消息须为 1–2000 字')
        with self._mutex:
            s = self._load(session_id)
            if s['state'] == 'running':
                raise RunnerError(409, 'busy', '智能体正在执行；可先暂停或取消')
            pending = s.get('pending')
            add_message(s, 'user', text)
            if s['title'] == '新任务':
                s['title'] = text[:24] + ('…' if len(text) > 24 else '')
            if s['state'] == 'awaiting_confirmation' and AFFIRM.match(text):
                return self._confirm_locked(s)
            if s['state'] == 'awaiting_approval' and pending:
                if AFFIRM.match(text):
                    return self._approve_locked(s, True)
                self._reject_pending(s)
                if DENY.match(text):
                    return self._start_locked(s)
            if s['state'] == 'awaiting_user' and pending and pending.get('type') == 'questions':
                self._apply_answers(s, pending, text)
            s['pending'] = None
            s['phase'] = 'understand'
            s['turn'] += 1
            if not s['contract'].get('request_text'):
                s['contract']['request_text'] = text[:600]
            s['llm_messages'].append({'role': 'user', 'content': text})
            return self._start_locked(s)

    def edit_contract(self, session_id: str, fields: dict) -> dict:
        with self._mutex:
            s = self._load(session_id)
            if s['state'] == 'running':
                raise RunnerError(409, 'busy', '执行中不能修改任务合同；请先暂停')
            new, accepted, rejected = tc.apply(s['contract'], fields or {}, 'user_edit', note='用户在表单中修改')
            if rejected:
                raise RunnerError(400, 'invalid_contract', '；'.join(r['reason'] for r in rejected))
            s['contract'] = new
            if accepted:
                s['llm_messages'].append({'role': 'user', 'content': '[用户在表单中修改了任务合同] ' + json.dumps(
                    {c['key']: c['new'] for c in accepted}, ensure_ascii=False)})
                add_message(s, 'event', '已修改：' + '；'.join(f"{tc.FIELDS[c['key']].label} → {tc.format_value(c['key'], c['new']) if c['new'] is not None else '清除'}"
                                                         for c in accepted), 'notice')
                self._await_confirmation(s)
            self._save(s)
            return self._view(s)

    def control(self, session_id: str, action: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        with self._mutex:
            s = self._load(session_id)
            if action == 'pause':
                if s['state'] != 'running':
                    raise RunnerError(409, 'not_running', '会话没有在执行')
                self._event(self._pause, session_id).set()
                add_message(s, 'event', '已请求暂停：当前步骤结束后停止。', 'notice')
                self._save(s)
                return self._view(s)
            if action == 'cancel':
                if s['state'] == 'running':
                    self._event(self._cancel, session_id).set()
                    add_message(s, 'event', '已请求取消：正在中止模型调用与求解进程。', 'notice')
                    self._save(s)
                    return self._view(s)
                s['pending'] = None
                s['state'] = 'cancelled'
                self._save(s)
                return self._view(s)
            if s['state'] == 'running':
                raise RunnerError(409, 'busy', '智能体正在执行；可先暂停或取消')
            if action in ('resume', 'retry'):
                if s['state'] not in ('paused', 'failed', 'cancelled', 'interrupted', 'done', 'idle'):
                    raise RunnerError(409, 'invalid_state', f"当前状态 {s['state']} 不能{'继续' if action == 'resume' else '重试'}")
                if action == 'retry' and s['phase'] == 'report':
                    s['phase'] = 'execute'
                decision = s.get('simulation_decision') or {}
                if action == 'retry' and self._simulation_decided(s) and decision.get('outcome') != 'ok':
                    s.setdefault('simulation_decision_history', []).append(copy.deepcopy(decision))
                    s.pop('simulation_decision', None)
                    set_step(s, 'simulate', 'pending', '用户请求重试；启动前仍需批准')
                    add_message(s, 'event', '已重新开放仿真批准流程；启动前仍需再次批准。', 'notice')
                s['error'] = None
                add_message(s, 'event', '继续执行。' if action == 'resume' else '重试当前阶段。', 'notice')
                return self._start_locked(s)
            if action == 'confirm':
                return self._confirm_locked(s)
            if action in ('approve', 'reject'):
                if s['state'] != 'awaiting_approval' or not s.get('pending'):
                    raise RunnerError(409, 'nothing_pending', '没有待批准的操作')
                if action == 'approve':
                    return self._approve_locked(s, True)
                self._reject_pending(s)
                return self._start_locked(s)
            if action == 'takeover':
                return self._takeover_locked(s, payload)
            if action == 'simulate':
                return self._simulate_locked(s, payload)
            raise RunnerError(400, 'invalid_action', f'未知操作 {action}')

    def shutdown(self):
        for ev in list(self._cancel.values()):
            ev.set()
        for t in list(self._threads.values()):
            t.join(timeout=5)
        self.store.close()

    # ----------------------------------------------------------- control --
    def _load(self, session_id: str) -> dict:
        s = self.store.get(session_id)
        if s is None:
            raise RunnerError(404, 'not_found', f'没有会话 {session_id}')
        return s

    def _event(self, table: dict, sid: str) -> threading.Event:
        if sid not in table:
            table[sid] = threading.Event()
        return table[sid]

    def _save(self, s: dict):
        self.store.save(s)
        self._publish(s)

    def _publish(self, s: dict, kind: str = 'state', data: dict | None = None):
        bus = getattr(self.lab, 'bus', None)
        if bus is not None:
            bus.publish('agent_session', {'session_id': s['session_id'], 'state': s['state'], 'phase': s['phase'],
                                          'updated_utc': s['updated_utc'], 'kind': kind, **(data or {})})

    def _start_locked(self, s: dict) -> dict:
        sid = s['session_id']
        self._event(self._cancel, sid).clear()
        self._event(self._pause, sid).clear()
        s['state'] = 'running'
        s['turn_started'] = {'llm_calls': len(s['llm_calls']), 'tool_calls': len(s['tool_calls'])}
        self._save(s)
        t = threading.Thread(target=self._turn, args=(sid,), name=f'agent-{sid}', daemon=True)
        self._threads[sid] = t
        t.start()
        return self._view(s)

    def _confirm_locked(self, s: dict) -> dict:
        try:
            s['contract'] = tc.confirm(s['contract'])
        except ValueError as exc:
            raise RunnerError(409, 'contract_incomplete', str(exc)) from None
        s['pending'] = None
        set_step(s, 'confirm', 'done', f"第 {s['contract']['revision']} 版")
        reset_execution_plan(s)
        if tc.execution(s['contract'])['simulate']:
            ensure_step(s, 'simulate')
        s['phase'] = 'execute'
        add_message(s, 'event', f"任务合同第 {s['contract']['revision']} 版已确认，开始执行。", 'notice')
        s['llm_messages'].append({'role': 'user', 'content': f"[用户已确认任务合同第 {s['contract']['revision']} 版，请开始执行]"})
        return self._start_locked(s)

    def _approve_locked(self, s: dict, approved: bool) -> dict:
        pending = s['pending']
        if pending.get('kind') == 'tool_call' and pending['call']['name'] == 'start_simulation':
            if pending.get('contract_revision', s['contract']['revision']) != s['contract']['revision']:
                raise RunnerError(409, 'stale_approval', '待批准的仿真不属于当前任务合同；请重新执行当前阶段')
            refusal = self._simulation_refusal(s, pending['call']['arguments'])
            if refusal:
                raise RunnerError(409, *refusal)
        s['pending'] = None
        if pending.get('kind') == 'contract_change':
            s['contract'], accepted, _ = tc.apply(s['contract'], pending['fields'], 'repair', note='用户批准的修复')
            s['contract'] = tc.confirm(s['contract'])
            add_message(s, 'event', '已批准修改任务：' + '；'.join(
                f"{tc.FIELDS[c['key']].label} {tc.format_value(c['key'], c['old']) if c['old'] is not None else '未设'} → {tc.format_value(c['key'], c['new'])}"
                for c in accepted), 'notice')
            s['llm_messages'].append({'role': 'user', 'content': '[用户批准了任务修改，合同已更新并确认：' + json.dumps(
                {c['key']: c['new'] for c in accepted}, ensure_ascii=False) + '。请重新建立任务地图并求解。]'})
            s.setdefault('rule_progress', {})['repair_applied'] = True
            set_step(s, 'repair', 'done', '修改已批准')
        elif pending.get('kind') == 'tool_call':
            call = pending['call']
            ctx = tools.ToolContext(self.lab, s, self._event(self._cancel, s['session_id']),
                                    lambda kind, data: self._publish(s, kind, data))
            rec = self._record(s, tools.execute(self.tools, ctx, call['name'], call['arguments']), 'user_approved')
            note = tools.digest({'status': rec['status'], 'result': rec.get('result'), 'error': rec.get('error')}, 3000)
            s['llm_messages'].append({'role': 'user', 'content': f"[用户已批准并执行 {call['name']}] {note}"})
            add_message(s, 'event', f"已批准：{rec.get('label', call['name'])}（{'成功' if rec['status'] == 'ok' else '失败：' + rec['error']['message']}）", 'notice')
            if call['name'] == 'start_simulation':
                s['simulation_decision'] = {'contract_revision': s['contract']['revision'],
                                            'run_id': call['arguments']['run_id'], 'status': 'attempted',
                                            'outcome': rec['status'], 'error': rec.get('error')}
                if rec['status'] == 'ok':
                    s['simulation_watch'] = {'run_id': call['arguments']['run_id'], 'session_id': (rec.get('result') or {}).get('session_id')}
        return self._start_locked(s)

    def _simulate_locked(self, s: dict, payload: dict) -> dict:
        """The user asks for a verified plan to run. The click is the approval and is recorded as one."""
        if s['state'] not in ('done', 'failed', 'cancelled', 'interrupted', 'paused'):
            raise RunnerError(409, 'invalid_state', '先完成当前待确认或待批准的事项')
        run_id = str(payload.get('run_id') or (self._best_verified(s) or {}).get('run_id') or '')
        if not run_id:
            raise RunnerError(409, 'no_verified_run', '还没有通过独立复核的方案')
        run = self.lab.store.get_run(run_id)
        if not run or not tools.run_matches_current(s, run):
            raise RunnerError(400, 'invalid_run', '只能仿真当前任务地图与合同的方案')
        ensure_step(s, 'simulate')
        ctx = tools.ToolContext(self.lab, s, self._event(self._cancel, s['session_id']), lambda kind, data: self._publish(s, kind, data))
        rec = self._record(s, tools.execute(self.tools, ctx, 'start_simulation', {'run_id': run_id}), 'user_approved')
        if rec['status'] != 'ok':
            set_step(s, 'simulate', 'failed', rec['error']['message'])
            add_message(s, 'event', f"仿真未启动：{rec['error']['message']}", 'error')
            self._save(s)
            return self._view(s)
        s['simulation_decision'] = {'contract_revision': s['contract']['revision'], 'run_id': run_id, 'status': 'attempted', 'outcome': 'ok', 'error': None}
        s['simulation_watch'] = {'run_id': run_id, 'session_id': (rec.get('result') or {}).get('session_id')}
        s['phase'] = 'report'
        add_message(s, 'event', '已按你的操作启动田间仿真。', 'notice')
        return self._start_locked(s)

    def _watch_simulation(self, s: dict, cancel, pause):
        """Stay with an approved simulation until it ends, so the report states what happened rather than that it began."""
        watch = s.get('simulation_watch')
        if not watch or watch.get('done'):
            return
        set_step(s, 'simulate', 'running', 'Gazebo 执行中')
        self._save(s)
        field, saved, state = self.lab.fieldsim, 0.0, {}
        while True:
            if cancel.is_set():
                try:
                    field.stop()
                except Exception:  # noqa: BLE001 - the session is reported cancelled either way; the service keeps its own log
                    log.exception('stopping the field simulation after cancel failed')
                raise Cancelled
            if pause.is_set():
                raise Paused        # the simulation keeps running; resuming comes back here
            state = field.state()
            if state.get('phase') in ('finished', 'failed', 'stopped', 'idle') or state.get('session_id') not in (None, watch.get('session_id')):
                break
            if time.monotonic() - saved > 5:
                done = sum(int(v.get('tasks_done') or 0) for v in state.get('vehicles') or [])
                total = sum(int(v.get('tasks_total') or 0) for v in state.get('vehicles') or [])
                set_step(s, 'simulate', 'running', f'仿真时钟 {float(state.get("sim_time") or 0):.0f} s · {done}/{total} 个任务点')
                self._save(s)
                saved = time.monotonic()
            cancel.wait(1.0)
        row = {}
        for _ in range(20):                       # the session writes its result just after it reports the end, failed ones too
            try:
                row = field.result(watch.get('session_id')) or {}
                break
            except Exception:  # noqa: BLE001 - a session that died writes no result; the report says so
                if cancel.wait(.5):
                    break
        self._release_simulation(s, field)
        metrics = row.get('metrics') or state.get('metrics') or {}
        keep = ('tasks_total', 'tasks_completed', 'total_distance_m', 'planned_total_distance_m', 'makespan_s', 'planned_makespan_s',
                'cross_track_mean_m', 'cross_track_max_m', 'energy_wh')
        result = {'run_id': watch['run_id'], 'session_id': watch.get('session_id'), 'phase': state.get('phase'), 'state': row.get('state'),
                  'check_valid': (row.get('check') or {}).get('valid'), 'metrics': {k: round(metrics[k], 3) if isinstance(metrics.get(k), float) else metrics.get(k) for k in keep},
                  'errors': (state.get('errors') or [])[:5]}
        watch['done'] = True
        ok = result['state'] == 'succeeded'
        if ok:
            result['comparison'] = self._sim2sim(s, watch['session_id'], cancel)
        s['simulation_result'] = result
        m = result['metrics']
        set_step(s, 'simulate', 'done' if ok else 'failed', f"完成 {m.get('tasks_completed')}/{m.get('tasks_total')}" if ok else (result['errors'][0] if result['errors'] else f"仿真结束：{state.get('phase')}"))
        add_message(s, 'assistant', '田间仿真已结束。' if ok else '田间仿真没有完成。', 'simulation', result)
        self._save(s)

    @staticmethod
    def _release_simulation(s: dict, field):
        """The result and trajectories are saved; an idle Gazebo would only hold the machine and block the next approval."""
        try:
            field.stop()
        except Exception as exc:  # noqa: BLE001 - the run is already recorded; the panel can still close the session
            log.exception('closing the finished field simulation failed')
            add_message(s, 'event', f'田间仿真已结束，但会话未能自动关闭：{exc}。可在仿真面板关闭。', 'notice')

    def _sim2sim(self, s: dict, session_id: str, cancel) -> dict | None:
        """The finished Gazebo session's plan, run again in MuJoCo: how far apart the two engines land."""
        from ..physics import PhysicsError
        try:
            rec = self.lab.physics.submit(session_id=session_id)
        except PhysicsError as exc:
            add_message(s, 'event', f'未做引擎对比：{exc.message}', 'notice')
            return None
        rec = self.lab.physics.wait(rec['physics_run_id'], cancel)
        return {'physics_run_id': rec.get('physics_run_id'), **(rec.get('comparison') or {})} if rec.get('comparison') else None

    def _reject_pending(self, s: dict):
        pending = s.get('pending') or {}
        s['pending'] = None
        what = pending.get('call', {}).get('name') if pending.get('kind') == 'tool_call' else '任务修改'
        s['llm_messages'].append({'role': 'user', 'content': f'[用户拒绝了待批准的操作：{what}。不要再次提出同一操作。]'})
        if what == 'start_simulation':
            s['simulation_decision'] = {'contract_revision': s['contract']['revision'],
                                        'run_id': pending['call']['arguments']['run_id'], 'status': 'rejected'}
            set_step(s, 'simulate', 'skipped', '用户拒绝仿真')
        else:
            s.setdefault('rule_progress', {})['rejected'] = True
            set_step(s, 'repair', 'skipped', '用户拒绝修改')
        add_message(s, 'event', f'已拒绝：{what}', 'notice')

    def _takeover_locked(self, s: dict, payload: dict) -> dict:
        from ..planning import fingerprint
        from ..scenarios import get_scenario
        step = payload.get('step')
        if step not in {x['kind'] for x in s['plan']}:
            raise RunnerError(400, 'invalid_step', '未知步骤')
        note = str(payload.get('note') or '')[:200]
        if step == 'scenario' and payload.get('scenario_id'):
            try:
                sc = get_scenario(str(payload['scenario_id']))
            except KeyError:
                raise RunnerError(404, 'not_found', '没有该任务地图') from None
            if sc.get('problem_class') != 'fleet_routing':
                raise RunnerError(400, 'invalid_scenario', '只能接管多车任务地图')
            s['scenario_id'] = sc['id']
            s['scenario_contract_fields'] = {k: tc.value(s['contract'], k) for k in tools.GEOMETRY_FIELDS}
            s['scenario_instance_sha256'] = fingerprint(sc['instance'])
            s['built_revision'] = s['contract']['revision']
            s['solved_since_build'] = False
            s['active_run_ids'], s['active_sim_runs'] = [], []
            s['verifications'], s['recommendation'], s['tried_algorithms'] = {}, None, []
            s['precheck'] = diagnose.precheck(sc['instance'])
            note = note or f"使用用户编辑的地图 {sc['id']}"
        if step == 'solve' and payload.get('run_ids'):
            ids = [str(r) for r in payload['run_ids']][:24]
            for rid in ids:
                run = self.lab.store.get_run(rid)
                if run is None or run.get('scenario_id') != s.get('scenario_id'):
                    raise RunnerError(400, 'invalid_run', f'{rid} 不是本任务地图的运行')
                if s.get('scenario_instance_sha256') and fingerprint(run.get('instance') or {}) != s['scenario_instance_sha256']:
                    raise RunnerError(400, 'stale_run', f'{rid} 的输入与当前任务地图不一致')
            s['run_ids'] = list(dict.fromkeys(s['run_ids'] + ids))
            s['active_run_ids'] = list(dict.fromkeys(tools.current_run_ids(s) + ids))
            s['solved_since_build'] = True
            note = note or f'接入用户的 {len(ids)} 个运行'
        set_step(s, step, 'taken_over', note or '由用户接管')
        s['tool_calls'].append({'id': f"c{len(s['tool_calls']) + 1}", 'name': 'takeover', 'label': '用户接管',
                                'arguments': payload, 'status': 'ok', 'actor': 'user', 'step': step,
                                'started_utc': utc_now(), 'duration_s': 0, 'result': {'note': note}})
        add_message(s, 'event', f"用户接管「{next(x['title'] for x in s['plan'] if x['kind'] == step)}」：{note or '手动完成'}", 'notice')
        self._save(s)
        return self._view(s)

    def _await_confirmation(self, s: dict):
        from ..compose.nl import fill_defaults
        _, defaults = fill_defaults(tc.compose_overrides(s['contract']))
        s['phase'] = 'confirm'
        s['state'] = 'awaiting_confirmation'
        set_step(s, 'confirm', 'waiting')
        data = {'cards': tc.cards(s['contract'], defaults), 'unsupported': s['contract']['unsupported'],
                'execution': tc.execution(s['contract']), 'revision': s['contract']['revision']}
        add_message(s, 'assistant', '请确认任务合同；浅色项为系统默认值，可直接修改后再确认。', 'confirmation', data)

    # -------------------------------------------------------------- turn --
    def _turn(self, sid: str):
        s = self.store.get(sid)
        cancel, pause = self._event(self._cancel, sid), self._event(self._pause, sid)
        try:
            if s['phase'] == 'understand':
                if not self._understand(s, cancel, pause):
                    return
                gaps = tc.missing(s['contract'])
                if gaps:
                    self._ask_missing(s, gaps)
                    return
                if s['contract']['status'] == 'confirmed':
                    s['phase'] = 'execute'
                elif s['auto_confirm']:
                    self._save(s)
                    with self._mutex:
                        s['contract'] = tc.confirm(s['contract'])
                        set_step(s, 'confirm', 'done', '自动确认')
                        reset_execution_plan(s)
                        s['phase'] = 'execute'
                else:
                    self._await_confirmation(s)
                    self._save(s)
                    return
            if s['phase'] == 'confirm':
                self._await_confirmation(s)
                self._save(s)
                return
            if s['phase'] == 'execute':
                if tc.execution(s['contract'])['simulate']:
                    ensure_step(s, 'simulate')
                if not self._execute(s, cancel, pause):
                    return
                s['phase'] = 'report'
            if s['phase'] == 'report':
                self._watch_simulation(s, cancel, pause)
                self._report(s, cancel)
                s['state'] = 'done'
                self._save(s)
        except Paused:
            s['state'] = 'paused'
            add_message(s, 'event', '已暂停。可修改任务合同或直接继续。', 'notice')
            self._save(s)
        except Cancelled:
            s['state'] = 'cancelled'
            for step in s['plan']:
                if step['status'] == 'running':
                    step['status'] = 'cancelled'
            add_message(s, 'event', '已取消。已有结果与记录保留，可重试或发送新的要求。', 'notice')
            self._save(s)
        except llm.LLMError as exc:
            s['state'] = 'failed'
            s['error'] = {'kind': exc.kind, 'message': str(exc)[:500], 'where': 'model'}
            for step in s['plan']:
                if step['status'] == 'running':
                    step['status'] = 'failed'
            add_message(s, 'event', f'模型调用失败（{exc.kind}）：{str(exc)[:300]}。可重试，或改用规则流程新建会话。', 'error')
            self._save(s)
        except ToolFailure as exc:
            s['state'] = 'failed'
            code = exc.rec['error']['code']
            s['error'] = {'kind': 'environment' if code in ('source_changed', 'shutting_down') else 'implementation_error',
                          'message': str(exc)[:500], 'where': exc.rec.get('label') or exc.rec['name']}
            hint = '请重启网关后点击“重试当前阶段”' if code == 'source_changed' else '这是软件或环境错误，智能体不会以修改任务的方式绕过；可在调用记录中查看详情后重试'
            add_message(s, 'event', f"「{exc.rec.get('label') or exc.rec['name']}」失败：{str(exc)[:300]}。{hint}。", 'error')
            self._save(s)
        except Budget as exc:
            s['state'] = 'failed'
            s['error'] = {'kind': 'budget', 'message': str(exc), 'where': 'runner'}
            add_message(s, 'event', str(exc), 'error')
            self._save(s)
        except Exception as exc:  # noqa: BLE001 - keep the session recoverable and the defect visible
            log.exception('agent turn failed')
            s['state'] = 'failed'
            s['error'] = {'kind': 'implementation_error', 'message': f'{type(exc).__name__}: {exc}'[:500], 'where': 'runner'}
            add_message(s, 'event', f'智能体内部错误：{type(exc).__name__}: {str(exc)[:300]}', 'error')
            self._save(s)

    def _checkpoint(self, s, cancel, pause):
        if cancel.is_set():
            raise Cancelled()
        if pause.is_set():
            raise Paused()

    def _llm(self, s: dict, phase: str, system: str, names: list[str] | None, cancel) -> llm.Response:
        started = s.get('turn_started') or {}
        if len(s['llm_calls']) - started.get('llm_calls', 0) >= MAX_LLM_CALLS:
            raise Budget(f'本轮模型调用已达上限 {MAX_LLM_CALLS} 次，已停止；可检查调用记录后重试')
        provider = self._find_provider((s.get('provider') or {}).get('id'))
        if provider is None:
            raise llm.LLMError('这个会话使用的模型已不可用，请在设置里重新连接服务商', 'not_configured')
        messages = [{'role': 'system', 'content': system}] + self._context(s)
        specs = [self.tools[n].spec() for n in names] if names else None
        try:
            resp = self.chat(provider, messages, specs, cancel=cancel)
        except llm.LLMCancelled:
            s['llm_calls'].append({'phase': phase, 'utc': utc_now(), 'provider': provider.id, 'model': provider.model,
                                   'error': 'cancelled', 'usage': {}})
            raise Cancelled() from None
        except llm.LLMError as exc:
            s['llm_calls'].append({'phase': phase, 'utc': utc_now(), 'provider': provider.id, 'model': provider.model,
                                   'error': f'{exc.kind}: {str(exc)[:200]}', 'usage': {}})
            raise
        for call in resp.tool_calls:
            try:
                json.dumps(call.get('arguments') or {}, allow_nan=False)
            except (TypeError, ValueError):
                call['parse_error'] = '工具参数须为有效 JSON，数值必须有限（不可含 NaN 或 Infinity）'
                call['arguments'] = {}
        rec = resp.record() | {'phase': phase, 'utc': utc_now(), 'prompt_version': prompts.VERSION, 'runner_version': RUNNER_VERSION}
        s['llm_calls'].append(rec)
        add_usage(s, rec)
        return resp

    def _context(self, s: dict) -> list[dict]:
        msgs = copy.deepcopy(s['llm_messages'][-CONTEXT_MESSAGES:])
        while msgs and msgs[0]['role'] == 'tool':
            msgs.pop(0)
        recent = len(msgs) - 10
        for i, m in enumerate(msgs):
            if m['role'] == 'tool' and i < recent and len(m['content']) > 500:
                m['content'] = m['content'][:500] + '…（较早的工具结果已压缩）'
        return msgs

    def _record(self, s: dict, rec: dict, actor: str) -> dict:
        tool = self.tools.get(rec['name'])
        rec['id'] = f"c{len(s['tool_calls']) + 1}"
        rec['actor'] = actor
        rec['step'] = tool.step if tool else None
        s['tool_calls'].append(rec)
        if tool and tool.step:
            if rec['status'] == 'ok' and rec['name'] == 'verify_run':
                checked = list((s.get('verifications') or {}).values())
                set_step(s, 'verify', 'done', f"{sum(1 for v in checked if v.get('valid'))}/{len(checked)} 个方案复核通过")
            elif rec['status'] == 'ok':
                set_step(s, tool.step, 'done', _detail(rec))
            elif rec['status'] in ('error', 'refused'):
                if rec['name'] == 'start_simulation' and rec['error']['code'] == 'simulation_already_decided':
                    decision = s.get('simulation_decision') or {}
                    status = 'skipped' if decision.get('status') == 'rejected' else ('done' if decision.get('outcome') == 'ok' else 'failed')
                    set_step(s, tool.step, status, '用户拒绝仿真' if status == 'skipped' else '仿真已尝试执行')
                else:
                    set_step(s, tool.step, 'failed', rec['error']['message'])
        self._save(s)
        return rec

    def _tool(self, s: dict, call: dict, actor: str, cancel) -> dict:
        tool = self.tools.get(call['name'])
        started = s.get('turn_started') or {}
        if len(s['tool_calls']) - started.get('tool_calls', 0) >= MAX_TOOL_CALLS:
            raise Budget(f'本轮工具调用已达上限 {MAX_TOOL_CALLS} 次，已停止')
        if tool and tool.step:
            if tool.step == 'study':
                ensure_step(s, 'study', before='verify')
            set_step(s, tool.step, 'running')
            self._save(s)
        if call.get('parse_error'):
            rec = {'name': call['name'], 'arguments': {}, 'status': 'refused', 'started_utc': utc_now(), 'duration_s': 0,
                   'error': {'code': 'invalid_arguments', 'message': call['parse_error']}, 'label': tool.label if tool else call['name']}
        else:
            ctx = tools.ToolContext(self.lab, s, cancel, lambda kind, data: self._publish(s, kind, data))
            rec = tools.execute(self.tools, ctx, call['name'], call.get('arguments') or {})
            if rec['status'] == 'error' and (rec.get('error') or {}).get('code') == 'cancelled':
                self._record(s, rec, actor)
                raise Cancelled()
        rec = self._record(s, rec, actor)
        if rec['status'] == 'error' and (rec.get('error') or {}).get('code') in FATAL_TOOL_ERRORS:
            raise ToolFailure(rec)
        return rec

    # -------------------------------------------------------- understand --
    def _understand(self, s, cancel, pause) -> bool:
        set_step(s, 'understand', 'running')
        self._save(s)
        text = next((m['text'] for m in reversed(s['messages']) if m['role'] == 'user'), '')
        if s['mode'] in ('rule', 'hybrid'):
            parsed = tc.from_rule_parse(text)
            values = {k: v['value'] for k, v in parsed['fields'].items()}
            evidence = {k: v['evidence'] for k, v in parsed['fields'].items()}
            s['contract'], accepted, rejected = tc.apply(s['contract'], values, 'user_text', evidence, '规则解析')
            self._record(s, {'name': 'rule_parse', 'label': '规则解析', 'arguments': {'text': text[:300]}, 'status': 'ok',
                             'started_utc': utc_now(), 'duration_s': 0,
                             'result': {'accepted': accepted, 'rejected': rejected}}, 'rule')
        if s['mode'] in ('llm', 'hybrid'):
            for _ in range(MAX_UNDERSTAND_CALLS):
                self._checkpoint(s, cancel, pause)
                resp = self._llm(s, 'understand', prompts.understand(s['contract']), tools.PHASE_TOOLS['understand'], cancel)
                if not resp.tool_calls:
                    if resp.content.strip():
                        s['llm_messages'].append({'role': 'assistant', 'content': resp.content})
                        add_message(s, 'assistant', _strip_think(resp.content))
                    break
                s['llm_messages'].append({'role': 'assistant', 'content': resp.content or '', 'reasoning': resp.reasoning,
                                          'tool_calls': [{'id': c['id'], 'name': c['name'], 'arguments': c['arguments']} for c in resp.tool_calls]})
                asked = False
                for call in resp.tool_calls:
                    if call['name'] not in tools.PHASE_TOOLS['understand']:
                        rec = {'name': call['name'], 'arguments': call.get('arguments') or {}, 'status': 'refused',
                               'started_utc': utc_now(), 'duration_s': 0, 'label': call['name'],
                               'error': {'code': 'wrong_phase', 'message': '理解阶段只能更新任务合同或提问；执行须等用户确认合同'}}
                        rec = self._record(s, rec, 'llm')
                    else:
                        rec = self._tool(s, call, 'llm', cancel)
                    s['llm_messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'name': call['name'],
                                              'content': tools.digest({'status': rec['status'], 'result': rec.get('result'),
                                                                       'error': rec.get('error')})})
                    asked = asked or (call['name'] == 'ask_user' and rec['status'] == 'ok')
                if asked:
                    pending = s['pending']
                    add_message(s, 'assistant', pending.get('reason') or '还需要确认几项信息：', 'questions', pending)
                    s['state'] = 'awaiting_user'
                    set_step(s, 'understand', 'waiting', '等待用户补充')
                    self._save(s)
                    return False
        set_step(s, 'understand', 'done', f"合同第 {s['contract']['revision']} 版")
        return True

    def _ask_missing(self, s: dict, gaps: list[str]):
        questions = [{'question': QUESTIONS[g][0], 'field': g, 'options': QUESTIONS[g][1]} for g in gaps if g in QUESTIONS]
        s['pending'] = {'type': 'questions', 'questions': questions, 'reason': '以下关键信息尚未给出：'}
        add_message(s, 'assistant', '以下关键信息尚未给出：', 'questions', s['pending'])
        s['state'] = 'awaiting_user'
        set_step(s, 'understand', 'waiting', '等待用户补充')
        self._save(s)

    def _apply_answers(self, s: dict, pending: dict, text: str):
        """A bare answer to a single question is bound to that question's field (e.g. "40" → task_count)."""
        fields = [q.get('field') for q in pending.get('questions', []) if q.get('field')]
        stripped = re.sub(r'\s+', '', text).rstrip('。.个台辆')
        if len(fields) == 1 and re.fullmatch(r'\d+(\.\d+)?', stripped) and fields[0] in ('task_count', 'vehicle_count'):
            s['contract'], _, _ = tc.apply(s['contract'], {fields[0]: int(float(stripped))}, 'user_text', {fields[0]: text}, '回答澄清问题')

    # ----------------------------------------------------------- execute --
    def _execute(self, s, cancel, pause) -> bool:
        if s['mode'] == 'rule':
            ok = self._execute_rule(s, cancel, pause)
        else:
            ok = self._execute_llm(s, cancel, pause)
        if not ok:
            return False
        self._checkpoint(s, cancel, pause)
        self._guard_verification(s, cancel)
        self._guard_physics(s, cancel, pause)
        return not self._guard_simulation(s)

    @staticmethod
    def _unmet(s: dict) -> list[str]:
        """Workflow steps the confirmed contract still requires before a report can be written."""
        gaps = []
        if not s.get('scenario_id') or s.get('built_revision') != s['contract']['revision']:
            gaps.append('build_scenario（按当前合同建立任务地图）')
        if not s.get('solved_since_build'):
            gaps.append('solve（求解对比）')
        return gaps

    def _execute_llm(self, s, cancel, pause) -> bool:
        nudges = 0
        execution_tools = [name for name in tools.PHASE_TOOLS['execute'] if name != 'ask_user']
        for _ in range(MAX_LLM_CALLS):
            self._checkpoint(s, cancel, pause)
            resp = self._llm(s, 'execute', prompts.execute(s['contract'], s), execution_tools, cancel)
            if not resp.tool_calls:
                s['llm_messages'].append({'role': 'assistant', 'content': resp.content or ''})
                gaps = self._unmet(s)
                if gaps and nudges < 2:
                    nudges += 1
                    s['llm_messages'].append({'role': 'user', 'content': '[系统检查] 尚未完成：' + '；'.join(gaps)
                                              + '。请直接调用相应工具，不要只用文字回复。'})
                    continue
                if gaps:
                    add_message(s, 'event', '模型连续未调用执行工具，剩余步骤改由规则流程完成（调用记录中标为“规则”）。', 'notice')
                    s.setdefault('rule_progress', {})
                    return self._execute_rule(s, cancel, pause)
                s['draft_report'] = _strip_think(resp.content or '')
                return True
            s['llm_messages'].append({'role': 'assistant', 'content': resp.content or '', 'reasoning': resp.reasoning,
                                      'tool_calls': [{'id': c['id'], 'name': c['name'], 'arguments': c['arguments']} for c in resp.tool_calls]})
            stop = None
            asked = False
            for call in resp.tool_calls:
                if stop:
                    s['llm_messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'name': call['name'],
                                              'content': json.dumps({'status': 'deferred', 'message': '等待用户处理前一项，未执行'}, ensure_ascii=False)})
                    continue
                self._checkpoint(s, cancel, pause)
                tool = self.tools.get(call['name'])
                if call['name'] not in execution_tools:
                    asked = asked or call['name'] == 'ask_user'
                    rec = self._record(s, {'name': call['name'], 'arguments': call.get('arguments') or {}, 'status': 'refused',
                                           'started_utc': utc_now(), 'duration_s': 0, 'label': tool.label if tool else call['name'],
                                           'error': {'code': 'wrong_phase', 'message': '执行阶段按已确认合同调用工具，不再提问；'
                                                     '任务修改用 propose_task_change，合同要求仿真时用 start_simulation 请求批准'}}, 'llm')
                    s['llm_messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'name': call['name'],
                                              'content': tools.digest({'status': rec['status'], 'error': rec['error']})})
                    continue
                if tool and tool.effect == 'physical' and not call.get('parse_error'):
                    errors = tools.validate(tool.parameters, call.get('arguments') or {})
                    if not errors:
                        self._guard_verification(s, cancel)
                        self._guard_physics(s, cancel, pause)
                        refusal = self._simulation_refusal(s, call['arguments'])
                        if refusal:
                            rec = self._record(s, {'name': call['name'], 'arguments': call['arguments'], 'status': 'refused',
                                                   'started_utc': utc_now(), 'duration_s': 0, 'label': tool.label,
                                                   'error': {'code': refusal[0], 'message': refusal[1]}}, 'llm')
                            s['llm_messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'name': call['name'],
                                                      'content': tools.digest({'status': rec['status'], 'error': rec['error']})})
                            continue
                        self._queue_simulation(s, call['arguments'])
                        s['llm_messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'name': call['name'],
                                                  'content': json.dumps({'status': 'awaiting_user_approval'}, ensure_ascii=False)})
                        stop = 'approval'
                        continue
                rec = self._tool(s, call, 'llm', cancel)
                s['llm_messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'name': call['name'],
                                          'content': tools.digest({'status': rec['status'], 'result': rec.get('result'),
                                                                   'error': rec.get('error')})})
                if rec['status'] == 'ok' and call['name'] == 'ask_user':
                    stop = 'questions'
                elif rec['status'] == 'ok' and call['name'] == 'propose_task_change':
                    stop = 'approval'
            if stop:
                self._pause_for(s, stop)
                return False
            if asked:
                self._guard_verification(s, cancel)
                if self._guard_simulation(s):
                    return False
        raise Budget(f'执行阶段模型调用已达上限 {MAX_LLM_CALLS} 次')

    def _pause_for(self, s: dict, stop: str):
        pending = s['pending']
        if stop == 'questions':
            add_message(s, 'assistant', pending.get('reason') or '需要你补充：', 'questions', pending)
            s['state'] = 'awaiting_user'
        else:
            if pending.get('kind') == 'contract_change':
                text = '建议修改任务：' + '；'.join(
                    f"{tc.FIELDS[c['key']].label} {tc.format_value(c['key'], c['old']) if c['old'] is not None else '未设'} → {tc.format_value(c['key'], c['new'])}"
                    for c in pending['changes']) + (f"。理由：{pending['reason']}" if pending.get('reason') else '')
            else:
                text = f"请求执行：{pending.get('label')}（{json.dumps(pending['call']['arguments'], ensure_ascii=False)}）。{pending.get('reason', '')}"
            add_message(s, 'assistant', text, 'approval', pending)
            s['state'] = 'awaiting_approval'
            set_step(s, 'repair' if pending.get('kind') == 'contract_change' else 'simulate', 'waiting', '等待批准')
        self._save(s)

    def _rule_call(self, s, name: str, args: dict, cancel) -> dict:
        return self._tool(s, {'id': f'rule_{len(s["tool_calls"])}', 'name': name, 'arguments': args}, 'rule', cancel)

    def _execute_rule(self, s, cancel, pause) -> bool:
        progress = s.setdefault('rule_progress', {})
        execution = tc.execution(s['contract'])
        built = progress.get('built_revision')
        if built != s['contract']['revision'] or not s.get('scenario_id'):
            self._checkpoint(s, cancel, pause)
            rec = self._rule_call(s, 'build_scenario', {}, cancel)
            if rec['status'] != 'ok':
                return True
            progress.update(built_revision=s['contract']['revision'], solved=False, repaired=False)
        if not progress.get('solved'):
            self._checkpoint(s, cancel, pause)
            rec = self._rule_call(s, 'recommend_algorithms', {}, cancel)
            chosen = (rec.get('result') or {}).get('compare') or ['pyvrp.fleet']
            self._checkpoint(s, cancel, pause)
            rec = self._rule_call(s, 'solve', {'algorithms': chosen[:execution['compare_count']]}, cancel)
            progress['solved'] = True
            progress['batch'] = [r['run_id'] for r in (rec.get('result') or {}).get('runs', [])]
        self._guard_verification(s, cancel)
        batch = progress.get('batch') or []
        valid = [rid for rid in batch if (s['verifications'].get(rid) or {}).get('valid')]
        if not valid and batch and not progress.get('repaired'):
            progress['repaired'] = True
            self._checkpoint(s, cancel, pause)
            rec = self._rule_call(s, 'diagnose_run', {'run_id': batch[0]}, cancel)
            actions = (rec.get('result') or {}).get('repairs') or []
            auto = next((a for a in actions if not a['needs_user']), None)
            manual = next((a for a in actions if a['needs_user']), None)
            if auto:
                args = {'algorithms': auto['algorithms']}
                if auto['action'] == 'raise_budget':
                    args['time_limit_s'] = auto['time_limit_s']
                rec = self._rule_call(s, 'solve', args, cancel)
                progress['batch'] = [r['run_id'] for r in (rec.get('result') or {}).get('runs', [])]
                self._guard_verification(s, cancel)
            elif manual and not progress.get('rejected'):
                change = _manual_change(manual, s)
                if change:
                    rec = self._rule_call(s, 'propose_task_change', {'fields': change, 'reason': manual['why']}, cancel)
                    if rec['status'] == 'ok':
                        progress['built_revision'] = None
                        self._pause_for(s, 'approval')
                        return False
        return True

    def _simulation_decided(self, s: dict) -> bool:
        decision = s.get('simulation_decision') or {}
        return decision.get('contract_revision') == s['contract']['revision'] and decision.get('status') in ('rejected', 'attempted')

    def _simulation_refusal(self, s: dict, args: dict) -> tuple[str, str] | None:
        """Validate a concrete approval request without starting any physical service."""
        if s['contract']['status'] != 'confirmed' or s.get('built_revision') != s['contract']['revision']:
            return 'stale_scenario', '仿真须使用按当前已确认合同建立的任务地图'
        if not tc.execution(s['contract'])['simulate']:
            return 'simulation_not_requested', '当前已确认合同未要求仿真；修改合同须先请求用户批准'
        gap = tools.gazebo_gap()
        if gap:
            return 'simulator_unavailable', gap
        if self._simulation_decided(s):
            return 'simulation_already_decided', '当前合同的仿真已被拒绝或已尝试执行，不得重复请求批准'
        run = self.lab.store.get_run(args['run_id'])
        if not run or not tools.run_matches_current(s, run):
            return 'invalid_run', '仿真只能使用本会话当前任务地图与合同的运行'
        verdict = (s.get('verifications') or {}).get(args['run_id']) or {}
        if run.get('state') != 'done' or run.get('status') != 'feasible' or not verdict.get('valid') or not verdict.get('agrees'):
            return 'unverified', '仿真批准前须通过独立复核，且复核须与存档判定一致'
        better = self._best_executable(s)
        if better and better['run_id'] != args['run_id'] and self._physics_failed(s, args['run_id']):
            return 'physics_failed', (f"该方案未通过物理预演：{self._physics_cause(s, args['run_id'])}；"
                                      f"通过预演的方案是 {better['algorithm']}（{better['run_id']}）")
        return None

    def _queue_simulation(self, s: dict, args: dict):
        reason = '该操作会启动 Gazebo 物理仿真会话，需要你的批准'
        planned = self._best_verified(s)
        if self._physics_failed(s, args['run_id']):
            reason = f"此方案未通过物理预演（{self._physics_cause(s, args['run_id'])}），没有通过预演的方案可选。{reason}"
        elif planned and planned['run_id'] != args['run_id'] and self._physics_failed(s, planned['run_id']):
            reason = f"规划最优的 {planned['algorithm']} 未通过物理预演（{self._physics_cause(s, planned['run_id'])}），这里改用通过预演的方案。{reason}"
        s['pending'] = {'type': 'approval', 'kind': 'tool_call', 'label': '启动田间仿真',
                        'call': {'name': 'start_simulation', 'arguments': copy.deepcopy(args)},
                        'contract_revision': s['contract']['revision'], 'reason': reason}

    def _guard_simulation(self, s: dict) -> bool:
        """A model omission cannot skip the confirmed contract's physical approval gate."""
        if not tc.execution(s['contract'])['simulate'] or self._simulation_decided(s):
            return False
        best = self._best_executable(s) or self._best_verified(s)
        if not best:
            return False
        args = {'run_id': best['run_id'], 'real_time_factor': 4.0}
        if self._simulation_refusal(s, args):
            return False
        self._queue_simulation(s, args)
        self._pause_for(s, 'approval')
        return True

    def _guard_verification(self, s: dict, cancel):
        """Mandatory independent re-check of every feasible run of this session that has not been verified."""
        for rid in tools.current_run_ids(s):
            if rid in s.get('verifications', {}):
                continue
            run = self.lab.store.get_run(rid)
            if run and tools.run_matches_current(s, run) and run.get('state') == 'done' and run.get('status') == 'feasible':
                self._rule_call(s, 'verify_run', {'run_id': rid}, cancel) if s['mode'] == 'rule' else \
                    self._tool(s, {'id': f'guard_{rid}', 'name': 'verify_run', 'arguments': {'run_id': rid}}, 'system', cancel)

    def _guard_physics(self, s: dict, cancel, pause):
        """Every verified plan of this contract meets the physics engine before the report; it takes seconds."""
        passed = [rid for rid in tools.current_run_ids(s) if (s.get('verifications', {}).get(rid) or {}).get('valid')
                  and (s.get('verifications', {}).get(rid) or {}).get('agrees')]
        if not passed:
            return
        ensure_step(s, 'physics', before='repair')
        for rid in [r for r in passed if r not in (s.get('physics') or {})]:
            self._checkpoint(s, cancel, pause)
            call = {'id': f'guard_physics_{rid}', 'name': 'physics_check', 'arguments': {'run_id': rid}}
            rec = self._tool(s, call, 'rule' if s['mode'] == 'rule' else 'system', cancel)
            if rec['status'] != 'ok':
                s.setdefault('physics', {})[rid] = {'run_id': rid, 'state': 'unavailable', 'errors': [rec['error']['message']], 'metrics': {}}
        rows = tools.physics_rows(s)
        shown = sorted(r['run_id'] for r in rows)
        if shown == s.get('physics_shown'):
            return
        s['physics_shown'] = shown
        done = [r for r in rows if r.get('state') == 'succeeded' and r.get('check_valid')]
        set_step(s, 'physics', 'done' if len(done) == len(rows) else 'failed', f'{len(done)}/{len(rows)} 个方案执行通过')
        add_message(s, 'assistant', f'{len(done)}/{len(rows)} 个方案通过物理预演。', 'physics', {'rows': rows})
        self._save(s)

    @staticmethod
    def _physics_failed(s: dict, rid: str) -> bool:
        """True only for a plan the engine actually ran and rejected; an unavailable engine says nothing about the plan."""
        row = (s.get('physics') or {}).get(rid) or {}
        return row.get('state') in ('succeeded', 'failed') and not (row.get('state') == 'succeeded' and row.get('check_valid') is True)

    @staticmethod
    def _physics_cause(s: dict, rid: str) -> str:
        errors = ((s.get('physics') or {}).get(rid) or {}).get('errors') or []
        return errors[0] if errors else '运行核验未通过'

    def _best_executable(self, s: dict) -> dict | None:
        """The best verified plan that also ran clean in the physics engine."""
        rows = s.get('physics') or {}
        return self._best_verified(s, lambda rid: rows.get(rid, {}).get('state') == 'succeeded' and rows[rid].get('check_valid') is True)

    def _best_verified(self, s: dict, accept=None) -> dict | None:
        best = None
        for rid in tools.current_run_ids(s):
            v = s.get('verifications', {}).get(rid)
            if not v or not v.get('valid') or not v.get('agrees') or (accept and not accept(rid)):
                continue
            run = self.lab.store.get_run(rid)
            if not run or not tools.run_matches_current(s, run):
                continue
            value = (run.get('objective') or {}).get('value')
            if isinstance(value, (int, float)) and (best is None or value < best['objective']):
                best = {'run_id': rid, 'objective': value, 'algorithm': run['algorithm'].get('name'),
                        'algorithm_id': run['algorithm']['id'], 'unit': (run.get('objective') or {}).get('unit'),
                        'total_distance_m': (run.get('metrics') or {}).get('total_distance_m'),
                        'makespan_s': (run.get('metrics') or {}).get('makespan_s')}
        return best

    # ------------------------------------------------------------ report --
    def _evidence(self, s: dict) -> dict:
        runs = []
        for rid in tools.current_run_ids(s):
            run = self.lab.store.get_run(rid)
            if not run or not tools.run_matches_current(s, run):
                continue
            v = s.get('verifications', {}).get(rid)
            row = tools.run_summary(run, s.get('precheck'), v)
            row['time_limit_s'] = (row.get('params') or {}).get('time_limit_s')
            row['seed'] = (row.get('params') or {}).get('seed')
            runs.append({k: row[k] for k in ('run_id', 'algorithm', 'state', 'status', 'independent_recheck',
                                              'total_distance_m', 'makespan_s', 'vehicles_used', 'trips', 'wall_s',
                                              'params', 'time_limit_s', 'seed', 'diagnosis_label')})
        for r in runs:
            for k in ('total_distance_m', 'makespan_s', 'wall_s'):
                if isinstance(r[k], float):
                    r[k] = round(r[k], 2)
        c = s['contract']
        return {'contract': {tc.FIELDS[k].label: tc.format_value(k, v['value']) for k, v in c['fields'].items()},
                'scenario_id': s.get('scenario_id'), 'runs': runs, 'best_verified': _rounded(self._best_verified(s)),
                'best_executable': _rounded(self._best_executable(s)),
                'unsupported': c['unsupported'], 'simulation_started': bool(s.get('active_sim_runs', s.get('sim_runs'))),
                'simulation_required': tc.execution(c)['simulate'], 'simulator_gap': tools.gazebo_gap(),
                'simulation_decision': s.get('simulation_decision') if self._simulation_decided(s) else None,
                'simulation_result': s.get('simulation_result') if (s.get('simulation_result') or {}).get('run_id') in tools.current_run_ids(s) else None,
                'physics': tools.physics_rows(s),
                'precheck': [f['message'] for f in (s.get('precheck') or {}).get('findings', [])]}

    def _report(self, s: dict, cancel):
        set_step(s, 'report', 'running')
        self._save(s)
        evidence = self._evidence(s)
        text, source = None, 'template'
        if s['mode'] != 'rule':
            try:
                resp = self._llm(s, 'report', prompts.report(evidence), None, cancel)
                text, source = _strip_think(resp.content).strip() or None, 'llm'
            except llm.LLMError as exc:
                add_message(s, 'event', f'报告生成调用失败（{exc.kind}），改用模板报告。', 'notice')
        if not text:
            text, source = _template_report(evidence), 'template'
        flagged = claims_check(text, evidence) if source == 'llm' else []
        s['report'] = {'evidence': evidence, 'narrative': text, 'source': source, 'unverified_numbers': flagged,
                       'utc': utc_now()}
        add_message(s, 'assistant', text, 'report', s['report'])
        if not any(st['kind'] == 'repair' and st['status'] not in ('pending',) for st in s['plan']):
            set_step(s, 'repair', 'skipped', '无需修复')
        for st in s['plan']:
            if st['status'] == 'pending' and st['kind'] not in ('report',):
                st['status'] = 'skipped'
        set_step(s, 'report', 'done', '依据已核对' if not flagged else f'{len(flagged)} 个数值未在依据中找到')


def _strip_think(text: str) -> str:
    return re.sub(r'<think>.*?</think>', '', text or '', flags=re.S).strip()


def _rounded(d: dict | None) -> dict | None:
    if not d:
        return d
    return {k: round(v, 2) if isinstance(v, float) else v for k, v in d.items()}


def _detail(rec: dict) -> str:
    r = rec.get('result') or {}
    name = rec['name']
    if name == 'build_scenario':
        return f"{r.get('tasks')} 个任务点 · {r.get('vehicles')} 台车"
    if name == 'recommend_algorithms':
        return f"推荐 {r.get('recommended')}"
    if name == 'solve':
        runs = r.get('runs') or []
        ok = sum(1 for x in runs if x['status'] == 'feasible' and x['check_valid'])
        return f'{ok}/{len(runs)} 个方案通过检查'
    if name == 'verify_run':
        verdict = r.get('recheck') or {}
        return '复核通过' if verdict.get('valid') and verdict.get('agrees') else '复核未通过'
    if name == 'diagnose_run':
        return r.get('diagnosis_label') or ''
    if name == 'start_simulation':
        return f"会话 {r.get('phase')}"
    return ''


def _manual_change(action: dict, s: dict) -> dict | None:
    pre = s.get('precheck') or {}
    if action['action'] == 'allow_multi_trip':
        return {'trip_mode': 'multi'}
    if action['action'] == 'add_vehicles':
        cap = tc.value(s['contract'], 'vehicle_capacity')
        total = pre.get('total_demand')
        if cap and total:
            return {'vehicle_count': min(30, max(int(tc.value(s['contract'], 'vehicle_count') or 1) + 1, math.ceil(total / cap)))}
    if action['action'] == 'raise_capacity':
        return None
    return None


def _template_report(e: dict) -> str:
    """Deterministic report in the same register as the model-written one; every figure comes from the evidence."""
    c = e['contract']
    scale = '，'.join(x for x in (
        f"场地 {c['场地尺寸']}" if '场地尺寸' in c else '',
        f"{c['任务点数']}{c.get('作业类型', '')}任务点" if '任务点数' in c else '',
        f"{c['车辆数']}车" if '车辆数' in c else '',
        f"单车容量 {c['单车容量']}" if '单车容量' in c else '',
        f"障碍为 {c['障碍']}" if c.get('障碍') not in (None, '无') else '') if x)
    parts = [f'任务规模：{scale}。' if scale else '']
    runs = e['runs']
    if runs:
        names = '、'.join(dict.fromkeys(r['algorithm'] for r in runs if r['algorithm']))
        checked = sum(1 for r in runs if r['independent_recheck'] is True)
        parts.append(f'在当前任务输入上比较了 {len(runs)} 个方案（{names}），其中 {checked} 个通过独立检查器复核；各次预算与随机种子见运行记录。')
    best = e.get('best_verified')
    if best:
        parts.append(f"经复核的最优方案为 {best['algorithm']}（{best['run_id']}），总里程 {best['total_distance_m']} m，"
                     f"按恒速模型计算的最晚完成时间为 {best['makespan_s']} s。")
    else:
        parts.append('目前没有通过独立复核的方案。')
    failed = [r for r in runs if r['independent_recheck'] is not True]
    if failed:
        parts.append('未通过的方案及原因：' + '；'.join(f"{r['algorithm']}：{r['diagnosis_label']}" for r in failed[:4]) + '。')
    if e['precheck']:
        parts.append('预检查结论：' + '；'.join(e['precheck'][:3]) + '。')
    if e['unsupported']:
        parts.append('以下要求超出当前任务模型的表达范围，未纳入求解：' + '；'.join(e['unsupported']) + '。')
    decision = e.get('simulation_decision') or {}
    physics = [r for r in e.get('physics', []) if r.get('metrics')]
    if physics:
        head = '；'.join(f"{r['algorithm']} 实际里程 {r['metrics'].get('total_distance_m')} m、工期 {r['metrics'].get('makespan_s')} s" if r.get('check_valid')
                        else f"{r['algorithm']} 未通过" for r in physics)
        parts.append(f"物理预演（MuJoCo {physics[0].get('engine_version')}）：{head}。")
        planned, executable = e.get('best_verified'), e.get('best_executable')
        if planned and executable and planned['run_id'] != executable['run_id']:
            cause = next(((r.get('errors') or ['运行核验未通过'])[0] for r in physics if r['run_id'] == planned['run_id']), '运行核验未通过')
            parts.append(f"规划最优的 {planned['algorithm']} 未通过物理预演（{cause}），可执行的最好方案为 {executable['algorithm']}（{executable['run_id']}），规划里程 {executable['total_distance_m']} m。")
        elif planned and not executable:
            parts.append('通过复核的方案都没有通过物理预演，暂无可执行方案。')
    sim = e.get('simulation_result')
    if sim:
        m = sim['metrics']
        if sim.get('state') == 'succeeded':
            parts.append(f"田间仿真完成 {m.get('tasks_completed')}/{m.get('tasks_total')} 个任务点，实际里程 {m.get('total_distance_m')} m，"
                         f"规划里程 {m.get('planned_total_distance_m')} m，实际工期 {m.get('makespan_s')} s，最大横向偏差 {m.get('cross_track_max_m')} m。")
            gap = sim.get('comparison')
            if gap and gap.get('path_mean_m') is not None:
                parts.append(f"同一方案在 Gazebo 与 MuJoCo 中的轨迹差：平均 {gap['path_mean_m']:.3f} m，最大 {gap['path_max_m']:.3f} m。")
        else:
            parts.append('田间仿真没有完成：' + ('；'.join(sim.get('errors') or []) or str(sim.get('phase'))) + '。')
    elif e.get('simulation_started'):
        parts.append('已启动田间仿真；启动记录不代表实际轨迹验收通过。')
    elif decision.get('status') == 'rejected':
        parts.append('用户拒绝启动仿真，本轮未执行田间仿真。')
    elif decision.get('status') == 'attempted':
        parts.append('仿真启动失败：' + str((decision.get('error') or {}).get('message') or '见工具调用记录') + '。')
    elif e.get('simulation_required') and e.get('simulator_gap'):
        parts.append(f"合同要求仿真，但未能启动 Gazebo：{e['simulator_gap']}。")
    elif e.get('simulation_required'):
        parts.append('当前没有可供仿真的已复核方案，未启动田间仿真。')
    return ''.join(p for p in parts if p)
