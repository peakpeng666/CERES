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
from . import diagnose, llm, prompts, tools
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

    def provider_list(self) -> list[llm.Provider]:
        return self._providers if self._providers is not None else llm.load_providers()

    def providers(self) -> dict:
        items = []
        providers = self.provider_list()
        for p in providers:
            info = p.public()
            info['status'] = llm.probe(p) if p.enabled else {'reachable': None, 'model_present': None, 'detail': '未启用'}
            items.append(info)
        return {'providers': items, 'default': llm.default_provider_id(providers), 'config': str(llm.CONFIG_PATH),
                'prompt_version': prompts.VERSION}

    def _resolve_provider(self, provider_id: str | None) -> tuple[llm.Provider | None, str | None]:
        providers = self.provider_list()
        pid = provider_id or llm.default_provider_id(providers)
        p = next((x for x in providers if x.id == pid), None)
        if p is None:
            return None, '没有已启用的模型提供方'
        if not p.enabled:
            return None, f'{p.label or p.id} 未启用' + (f'（{p.terms_note}）' if p.terms_note else '')
        status = llm.probe(p) if self._providers is None else {'reachable': True, 'model_present': True}
        if p.kind == 'ollama' and not (status.get('reachable') and status.get('model_present')):
            return None, status.get('detail') or '本地模型不可用'
        if p.kind != 'ollama' and status.get('detail') == '缺少凭据':
            return None, f'{p.label or p.id} 缺少凭据'
        return p, None

    def create(self, body: dict) -> dict:
        mode = body.get('mode') or 'auto'
        if mode not in ('auto', 'llm', 'hybrid', 'rule'):
            raise RunnerError(400, 'invalid_mode', 'mode 须为 auto / llm / hybrid / rule')
        provider, reason = (None, '用户选择规则流程') if mode == 'rule' else self._resolve_provider(body.get('provider_id'))
        if provider is None and mode in ('llm', 'hybrid'):
            raise RunnerError(409, 'llm_unavailable', f'无法使用大模型：{reason}')
        actual = 'rule' if provider is None else ('hybrid' if mode == 'auto' else mode)
        info = None if provider is None else {k: provider.public()[k] for k in ('id', 'kind', 'model', 'label', 'local')}
        s = new_session(actual, info, bool(body.get('auto_confirm')), str(body.get('title') or '')[:60])
        if provider is None:
            add_message(s, 'event', f'本会话使用规则流程（未调用大模型）：{reason}。理解与执行均由确定性规则完成，结果同样经独立检查。', 'notice')
        self.store.save(s)
        self._publish(s)
        text = str(body.get('text') or '').strip()
        return self.message(s['session_id'], text) if text else public(s)

    def get(self, session_id: str) -> dict:
        return public(self._load(session_id))

    def list(self) -> list[dict]:
        return self.store.list()

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
                    s['state'] = 'done' if s['phase'] == 'report' else 'idle'
                    self._save(s)
                    return public(s)
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
            return public(s)

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
                return public(s)
            if action == 'cancel':
                if s['state'] == 'running':
                    self._event(self._cancel, session_id).set()
                    add_message(s, 'event', '已请求取消：正在中止模型调用与求解进程。', 'notice')
                    self._save(s)
                    return public(s)
                s['pending'] = None
                s['state'] = 'cancelled'
                self._save(s)
                return public(s)
            if s['state'] == 'running':
                raise RunnerError(409, 'busy', '智能体正在执行；可先暂停或取消')
            if action in ('resume', 'retry'):
                if s['state'] not in ('paused', 'failed', 'cancelled', 'interrupted', 'done', 'idle'):
                    raise RunnerError(409, 'invalid_state', f"当前状态 {s['state']} 不能{'继续' if action == 'resume' else '重试'}")
                if action == 'retry' and s['phase'] == 'report':
                    s['phase'] = 'execute'
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
        return public(s)

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
        elif pending.get('kind') == 'tool_call':
            call = pending['call']
            ctx = tools.ToolContext(self.lab, s, self._event(self._cancel, s['session_id']),
                                    lambda kind, data: self._publish(s, kind, data))
            rec = self._record(s, tools.execute(self.tools, ctx, call['name'], call['arguments']), 'user_approved')
            note = tools.digest({'status': rec['status'], 'result': rec.get('result'), 'error': rec.get('error')}, 3000)
            s['llm_messages'].append({'role': 'user', 'content': f"[用户已批准并执行 {call['name']}] {note}"})
            add_message(s, 'event', f"已批准：{rec.get('label', call['name'])}（{'成功' if rec['status'] == 'ok' else '失败：' + rec['error']['message']}）", 'notice')
            if call['name'] == 'start_simulation':
                s.setdefault('rule_progress', {})['simulated'] = True
        return self._start_locked(s)

    def _reject_pending(self, s: dict):
        pending = s.get('pending') or {}
        s['pending'] = None
        what = pending.get('call', {}).get('name') if pending.get('kind') == 'tool_call' else '任务修改'
        s['llm_messages'].append({'role': 'user', 'content': f'[用户拒绝了待批准的操作：{what}。不要再次提出同一操作。]'})
        s.setdefault('rule_progress', {})['rejected'] = True
        add_message(s, 'event', f'已拒绝：{what}', 'notice')

    def _takeover_locked(self, s: dict, payload: dict) -> dict:
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
            s['precheck'] = diagnose.precheck(sc['instance'])
            note = note or f"使用用户编辑的地图 {sc['id']}"
        if step == 'solve' and payload.get('run_ids'):
            ids = [str(r) for r in payload['run_ids']][:24]
            for rid in ids:
                run = self.lab.store.get_run(rid)
                if run is None or run.get('scenario_id') != s.get('scenario_id'):
                    raise RunnerError(400, 'invalid_run', f'{rid} 不是本任务地图的运行')
            s['run_ids'] = list(dict.fromkeys(s['run_ids'] + ids))
            note = note or f'接入用户的 {len(ids)} 个运行'
        set_step(s, step, 'taken_over', note or '由用户接管')
        s['tool_calls'].append({'id': f"c{len(s['tool_calls']) + 1}", 'name': 'takeover', 'label': '用户接管',
                                'arguments': payload, 'status': 'ok', 'actor': 'user', 'step': step,
                                'started_utc': utc_now(), 'duration_s': 0, 'result': {'note': note}})
        add_message(s, 'event', f"用户接管「{next(x['title'] for x in s['plan'] if x['kind'] == step)}」：{note or '手动完成'}", 'notice')
        self._save(s)
        return public(s)

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
        provider = next((p for p in self.provider_list() if p.id == (s.get('provider') or {}).get('id')), None)
        if provider is None:
            raise llm.LLMError('会话绑定的模型提供方已不存在或未启用', 'not_configured')
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
        rec = resp.record() | {'phase': phase, 'utc': utc_now(), 'prompt_version': prompts.VERSION}
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
                s['llm_messages'].append({'role': 'assistant', 'content': resp.content or '',
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
        return True

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
        for _ in range(MAX_LLM_CALLS):
            self._checkpoint(s, cancel, pause)
            resp = self._llm(s, 'execute', prompts.execute(s['contract'], s), tools.PHASE_TOOLS['execute'], cancel)
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
            s['llm_messages'].append({'role': 'assistant', 'content': resp.content or '',
                                      'tool_calls': [{'id': c['id'], 'name': c['name'], 'arguments': c['arguments']} for c in resp.tool_calls]})
            stop = None
            for call in resp.tool_calls:
                if stop:
                    s['llm_messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'name': call['name'],
                                              'content': json.dumps({'status': 'deferred', 'message': '等待用户处理前一项，未执行'}, ensure_ascii=False)})
                    continue
                self._checkpoint(s, cancel, pause)
                tool = self.tools.get(call['name'])
                if tool and tool.effect == 'physical' and not call.get('parse_error'):
                    errors = tools.validate(tool.parameters, call.get('arguments') or {})
                    if not errors:
                        s['pending'] = {'type': 'approval', 'kind': 'tool_call', 'label': tool.label,
                                        'call': {'name': call['name'], 'arguments': call['arguments']},
                                        'reason': '该操作会启动 Gazebo 物理仿真会话，需要你的批准'}
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
        if execution['simulate'] and not progress.get('simulated') and not progress.get('rejected'):
            best = self._best_verified(s)
            if best:
                s['pending'] = {'type': 'approval', 'kind': 'tool_call', 'label': '启动田间仿真',
                                'call': {'name': 'start_simulation', 'arguments': {'run_id': best['run_id'], 'real_time_factor': 4.0}},
                                'reason': '该操作会启动 Gazebo 物理仿真会话，需要你的批准'}
                self._pause_for(s, 'approval')
                return False
        return True

    def _guard_verification(self, s: dict, cancel):
        """Mandatory independent re-check of every feasible run of this session that has not been verified."""
        for rid in list(s.get('run_ids', [])):
            if rid in s.get('verifications', {}):
                continue
            run = self.lab.store.get_run(rid)
            if run and run.get('state') == 'done' and run.get('status') == 'feasible':
                self._rule_call(s, 'verify_run', {'run_id': rid}, cancel) if s['mode'] == 'rule' else \
                    self._tool(s, {'id': f'guard_{rid}', 'name': 'verify_run', 'arguments': {'run_id': rid}}, 'system', cancel)

    def _best_verified(self, s: dict) -> dict | None:
        best = None
        for rid in s.get('run_ids', []):
            v = s.get('verifications', {}).get(rid)
            if not v or not v.get('valid'):
                continue
            run = self.lab.store.get_run(rid)
            if not run or run.get('scenario_id') != s.get('scenario_id'):
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
        for rid in s.get('run_ids', []):
            run = self.lab.store.get_run(rid)
            if not run or run.get('scenario_id') != s.get('scenario_id'):
                continue
            row = tools.run_summary(run, s.get('precheck'))
            v = s.get('verifications', {}).get(rid)
            row['independent_recheck'] = None if v is None else bool(v.get('valid'))
            runs.append({k: row[k] for k in ('run_id', 'algorithm', 'state', 'status', 'independent_recheck',
                                              'total_distance_m', 'makespan_s', 'vehicles_used', 'trips', 'wall_s', 'diagnosis_label')})
        for r in runs:
            for k in ('total_distance_m', 'makespan_s', 'wall_s'):
                if isinstance(r[k], float):
                    r[k] = round(r[k], 2)
        c = s['contract']
        return {'contract': {tc.FIELDS[k].label: tc.format_value(k, v['value']) for k, v in c['fields'].items()},
                'scenario_id': s.get('scenario_id'), 'runs': runs, 'best_verified': _rounded(self._best_verified(s)),
                'unsupported': c['unsupported'], 'simulation_started': bool(s.get('sim_runs')),
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
        return '复核通过' if (r.get('recheck') or {}).get('valid') else '复核未通过'
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
        parts.append(f'在同一输入、预算与随机种子下比较了 {len(runs)} 个方案（{names}），其中 {checked} 个通过独立检查器复核。')
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
    return ''.join(p for p in parts if p)
