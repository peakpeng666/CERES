"""Agent sessions (CONTRACT §9): the model plans, workbench tools act, independent checkers decide.

A session is one conversation. Each user message starts a turn that runs in a worker thread:
model call → tool calls → model call … until the model answers without tools, a turn budget is
spent, an execution-tier tool needs approval, or the user cancels. Only the worker writes a running
session; approval, cancel and new messages act on sessions that are not running. Every step is
persisted to SQLite and announced on the SSE bus as an ``agent`` event, so the panel can follow
along and a reviewer can replay the full transcript later.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..events import utc_now
from ..services import ServiceError
from . import grounding
from .artifacts import ArtifactStore
from .research_llm import ModelError, configured_model
from .prompt import PROMPT_ID, PROMPT_SHA256, SYSTEM_PROMPT
from .research_tools import BY_NAME, TOOLS, ToolContext, ToolError

log = logging.getLogger('urp_lab.agent')

POLICY = {'id': 'urp.agent/1', 'max_model_calls_per_turn': 16, 'max_tool_calls_per_turn': 30,
          'max_turn_s': 900, 'tool_result_chars': 6000, 'step_result_chars': 20000, 'max_sessions_running': 2}
EVIDENCE_KEYS = {'scenario_id': 'scenarios', 'run_id': 'runs', 'job_id': 'jobs', 'study_id': 'studies',
                 'session_id': 'field_sessions'}
TURN_KEYS = ('turn_started', 'turn_model_calls', 'turn_tool_calls')
CANCELED = {'canceled': True, 'message': '用户取消了这一轮'}
INTERRUPTED = {'interrupted': True, 'message': '这一轮被中断，调用没有完成或结果未记录；可能已部分执行（例如已提交运行），继续前先用 list_runs 核对'}


def new_session_id() -> str:
    return f"agent_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(3)}"


def collect_ids(value, out: dict, depth: int = 0):
    """Ids a tool result names explicitly (scenario_id, run_id, run_ids, …) become session evidence."""
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            bucket = EVIDENCE_KEYS.get(key)
            if bucket and isinstance(item, str) and item and item not in out[bucket]:
                out[bucket].append(item)
            elif key == 'run_ids' and isinstance(item, list):
                out['runs'].extend(i for i in item if isinstance(i, str) and i not in out['runs'])
            else:
                collect_ids(item, out, depth + 1)
    elif isinstance(value, list):
        for item in value:
            collect_ids(item, out, depth + 1)


def _clip(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + f'…（已截断，原长 {len(text)} 字符）'


class AgentManager:
    def __init__(self, lab, model=None):
        self.lab = lab
        self._provided_model = model is not None
        self._model, self._model_reason = (model, None) if self._provided_model else configured_model()
        self._lock = threading.RLock()
        self._threads = ThreadPoolExecutor(max_workers=POLICY['max_sessions_running'], thread_name_prefix='agent')
        self._running: dict[str, threading.Event] = {}   # session id → cancel flag of its live turn
        self._turn_models: dict[str, object] = {}
        self._closed = False
        for stale in self.lab.store.list_agent_sessions(limit=200, states=('running',)):
            self._end(stale, 'error', '网关重启时这一轮被中断；会话记录保留，可以继续提问', announce=False)

    # ---------------------------------------------------------------- views --
    def status(self) -> dict:
        if not self._provided_model:
            self._model, self._model_reason = configured_model()
        return {'configured': self._model is not None, 'reason': self._model_reason,
                'model': self._model.describe() if self._model is not None else None,
                'prompt': {'id': PROMPT_ID, 'sha256': PROMPT_SHA256}, 'policy': POLICY,
                'tools': [t.public() for t in TOOLS]}

    def list(self, limit: int = 50) -> list[dict]:
        keys = ('session_id', 'title', 'state', 'created_utc', 'updated_utc', 'usage', 'error')
        return [{k: s.get(k) for k in keys} for s in self.lab.store.list_agent_sessions(limit=limit)]

    def get(self, session_id: str, messages: bool = False) -> dict:
        session = self._load(session_id)
        return session if messages else self._public(session)

    @staticmethod
    def _public(session: dict) -> dict:
        return {k: v for k, v in session.items() if k not in ('messages', 'queue') and k not in TURN_KEYS}

    # -------------------------------------------------------------- actions --
    def start(self, message: str) -> dict:
        self._require_model()
        now = utc_now()
        session = {'session_id': new_session_id(), 'title': message.strip()[:40], 'created_utc': now, 'updated_utc': now,
                   'state': 'idle', 'model': self._model.describe(), 'prompt': {'id': PROMPT_ID, 'sha256': PROMPT_SHA256},
                   'policy': POLICY, 'messages': [{'role': 'system', 'content': SYSTEM_PROMPT}], 'steps': [],
                   'queue': [], 'pending': None, 'grounding': None, 'error': None,
                   'usage': {'model_calls': 0, 'tool_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                   'evidence': {v: [] for v in EVIDENCE_KEYS.values()}}
        with self._lock:
            return self._begin_turn(session, message)

    def send(self, session_id: str, message: str) -> dict:
        self._require_model()
        with self._lock:
            session = self._load(session_id)
            if session['state'] in ('running', 'awaiting_approval'):
                raise ServiceError(409, 'agent_busy', '这一轮还没有结束（运行中或等待批准）')
            return self._begin_turn(session, message)

    def decide(self, session_id: str, call_id: str, approve: bool, note: str | None = None) -> dict:
        self._require_model()
        note = (note or '').strip()[:500] or None
        with self._lock:
            session = self._load(session_id)
            pending = session.get('pending')
            if session['state'] != 'awaiting_approval' or not pending or pending.get('call_id') != call_id:
                raise ServiceError(409, 'no_pending_approval', '没有等待批准的这项操作')
            self._saturation_check()
            self._bind_model(session)
            for step in session['steps']:
                if step.get('kind') == 'approval' and step.get('call_id') == call_id and step.get('decision') == 'pending':
                    step.update(decision='approved' if approve else 'denied', note=note, decided_utc=utc_now())
            session['pending'] = None
            call = session['queue'][0]
            if approve:
                call['approved'] = True
            else:
                session['queue'].pop(0)
                self._reply(session, call, {'denied': True, 'message': '用户拒绝了这项执行操作' + (f'：{note}' if note else '')})
                self._append(session, {'kind': 'tool', 'call_id': call['id'], 'name': call['name'], 'tier': 'actuate',
                                       'label': BY_NAME[call['name']].label, 'arguments': call.get('args'), 'ok': False,
                                       'error': '用户拒绝执行', 'duration_s': 0.0})
            session['state'] = 'running'
            session['turn_started'] = time.time()   # waiting for a person does not consume the turn's time budget
            self._save(session)
            self._spawn(session['session_id'])
            return self._public(session)

    def cancel(self, session_id: str) -> dict:
        with self._lock:
            session = self._load(session_id)
            flag = self._running.get(session_id)
            if flag is not None:
                flag.set()                      # the worker stops at its next step boundary
            elif session['state'] == 'awaiting_approval':
                for step in session['steps']:
                    if step.get('kind') == 'approval' and step.get('decision') == 'pending':
                        step.update(decision='canceled', decided_utc=utc_now())
                for call in session['queue']:
                    self._reply(session, call, CANCELED)
                session['queue'], session['pending'] = [], None
                self._append(session, {'kind': 'notice', 'message': '已取消这一轮'})
                self._end(session, 'canceled')
            return self._public(session)

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for flag in self._running.values():
                flag.set()
            for session in self.lab.store.list_agent_sessions(limit=200, states=('running', 'awaiting_approval')):
                for step in session['steps']:
                    if step.get('kind') == 'approval' and step.get('decision') == 'pending':
                        step.update(decision='canceled', decided_utc=utc_now())
                self._end(session, 'error', '网关关闭时这一轮已中断；重新提问后继续')
        self._threads.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ internals --
    def _require_model(self):
        if not self._provided_model:
            self._model, self._model_reason = configured_model()
        if self._model is None:
            raise ServiceError(503, 'agent_unconfigured', self._model_reason or '未配置大模型')
        if self._closed:
            raise ServiceError(503, 'shutting_down', '网关正在退出')

    def _load(self, session_id: str) -> dict:
        session = self.lab.store.get_agent_session(session_id)
        if session is None:
            raise ServiceError(404, 'not_found', f'没有智能体会话 {session_id}')
        return session

    def _saturation_check(self):
        if len(self._running) >= POLICY['max_sessions_running']:
            raise ServiceError(429, 'agent_saturated', f"已有 {len(self._running)} 个会话在运行，请稍后再试")

    def _begin_turn(self, session: dict, message: str) -> dict:
        self._saturation_check()
        text = message.strip()[:4000]
        if not text:
            raise ServiceError(400, 'invalid_request', 'message 不能为空')
        self._bind_model(session)
        session['messages'].append({'role': 'user', 'content': text})
        self._append(session, {'kind': 'user', 'text': text})
        session.update(state='running', error=None, grounding=None, turn_started=time.time(),
                       turn_model_calls=0, turn_tool_calls=0)
        self._save(session)
        self._spawn(session['session_id'])
        return self._public(session)

    def _bind_model(self, session: dict):
        model = self._model.describe()
        if session['model'] != model:
            self._append(session, {'kind': 'notice', 'message': f"本轮模型：{model['model']}",
                                   'previous_model': session['model'], 'model': model})
            session['model'] = model

    def _spawn(self, session_id: str):
        self._running[session_id] = threading.Event()
        self._turn_models[session_id] = self._model
        self._threads.submit(self._drive, session_id)

    def _append(self, session: dict, step: dict):
        session['steps'].append({'i': len(session['steps']), 'utc': utc_now(), **step})

    def _save(self, session: dict, announce: bool = True):
        session['updated_utc'] = utc_now()
        self.lab.store.put_agent_session(session)
        if announce:
            self.lab.bus.publish('agent', {'session_id': session['session_id'], 'state': session['state'],
                                           'steps': len(session['steps']), 'updated_utc': session['updated_utc']})

    def _reply(self, session: dict, call: dict, result: dict):
        """Every tool call in an assistant message gets exactly one tool message before the next model call."""
        text = ArtifactStore(self.lab.runtime, session['session_id']).payload(result)
        session['messages'].append({'role': 'tool', 'tool_call_id': call['id'],
                                    'content': text})

    def _end(self, session: dict, state: str, error: str | None = None, announce: bool = True):
        # Calls still queued (a restart or an internal error mid-turn) are answered now: providers reject a history
        # with unanswered tool calls, and a stale queue, an approved actuation included, must not run next turn.
        for call in session.get('queue') or []:
            self._reply(session, call, INTERRUPTED)
        session['queue'], session['pending'] = [], None
        session['state'], session['error'] = state, error
        if error:
            self._append(session, {'kind': 'error', 'message': error})
        for key in TURN_KEYS:
            session.pop(key, None)
        self._running.pop(session['session_id'], None)
        self._turn_models.pop(session['session_id'], None)
        self._save(session, announce)

    def _drive(self, session_id: str):
        try:
            self._turn(session_id)
        except Exception as exc:  # a bug must not leave a session "running"
            log.exception('agent session %s failed', session_id)
            if self._closed:
                return
            with self._lock:
                session = self.lab.store.get_agent_session(session_id)
                if session is not None and session['state'] == 'running':
                    self._end(session, 'error', f'智能体内部错误：{type(exc).__name__}: {exc}')

    def _stop_reason(self, session: dict, flag: threading.Event) -> tuple[str, str | None] | None:
        if self._closed:
            return 'error', '网关正在关闭，这一轮已停止；重启后可以继续提问'
        if flag.is_set():
            return 'canceled', None
        if time.time() - session.get('turn_started', time.time()) > POLICY['max_turn_s']:
            return 'error', f"这一轮超过 {POLICY['max_turn_s']} 秒，已停止；可以继续提问"
        if not session['queue'] and session.get('turn_model_calls', 0) >= POLICY['max_model_calls_per_turn']:
            return 'error', f"这一轮已调用模型 {POLICY['max_model_calls_per_turn']} 次，按预算停止；可以继续提问"
        if session['queue'] and session.get('turn_tool_calls', 0) >= POLICY['max_tool_calls_per_turn']:
            return 'error', f"这一轮已调用工具 {POLICY['max_tool_calls_per_turn']} 次，按预算停止；可以继续提问"
        return None

    def _turn(self, session_id: str):
        flag = self._running.get(session_id) or threading.Event()
        ctx = ToolContext(self.lab, session_id, flag.is_set)
        while True:
            with self._lock:
                if self._closed:
                    return
                session = self.lab.store.get_agent_session(session_id)
                if session is None or session['state'] != 'running':
                    return
                stop = self._stop_reason(session, flag)
                if stop is not None:
                    for call in session['queue']:
                        self._reply(session, call, CANCELED if stop[0] == 'canceled' else {'error': stop[1]})
                    session['queue'] = []
                    if stop[0] == 'canceled':
                        self._append(session, {'kind': 'notice', 'message': '已取消这一轮'})
                    self._end(session, *stop)
                    return
            if session['queue']:
                self._run_next_call(session, ctx)
            else:
                self._ask_model(session)

    def _ask_model(self, session: dict):
        with self._lock:
            if self._closed:
                return
            if self._running[session['session_id']].is_set():
                self._append(session, {'kind': 'notice', 'message': '已取消这一轮'})
                self._end(session, 'canceled')
                return
            session['usage']['model_calls'] += 1
            session['turn_model_calls'] = session.get('turn_model_calls', 0) + 1
            self._save(session)
            model = self._turn_models[session['session_id']]
        started = time.monotonic()
        try:
            reply = model.complete(session['messages'], [t.schema() for t in TOOLS])
        except ModelError as exc:
            with self._lock:
                if not self._closed:
                    if self._running[session['session_id']].is_set():
                        self._append(session, {'kind': 'notice', 'message': '已取消这一轮'})
                        self._end(session, 'canceled')
                    else:
                        self._end(session, 'error', str(exc))
            return
        elapsed = round(time.monotonic() - started, 3)
        with self._lock:
            if self._closed:
                return
            # Providers may omit or reuse call ids; approvals and replies need ids unique within the session.
            seen = {s.get('call_id') for s in session['steps'] if s.get('call_id')} | {
                c['id'] for s in session['steps'] if s.get('kind') == 'assistant' for c in s.get('tool_calls') or []}
            for i, call in enumerate(reply.tool_calls):
                if not call['id'] or call['id'] in seen:
                    call['id'] = f"call_{session['usage']['model_calls']}_{i}"
                seen.add(call['id'])
            usage = reply.usage or {}
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                if isinstance(usage.get(key), int):
                    session['usage'][key] += usage[key]
            if not reply.tool_calls and self._running[session['session_id']].is_set():
                self._append(session, {'kind': 'notice', 'message': '已取消这一轮'})
                self._end(session, 'canceled')
                return
            assistant = {'role': 'assistant', 'content': reply.content or ''}
            if reply.tool_calls:
                assistant['tool_calls'] = [{'id': c['id'], 'type': 'function',
                                            'function': {'name': c['name'], 'arguments': c['arguments']}} for c in reply.tool_calls]
            session['messages'].append(assistant)
            reasoning = _clip(reply.reasoning, 4000)
            if reply.tool_calls:
                self._append(session, {'kind': 'assistant', 'text': reply.content, 'reasoning': reasoning, 'model_s': elapsed,
                                       'tool_calls': [{'id': c['id'], 'name': c['name']} for c in reply.tool_calls]})
                session['queue'] = [{'id': c['id'], 'name': c['name'], 'raw': c['arguments']} for c in reply.tool_calls]
                self._save(session)
                return
            if not (reply.content or '').strip():
                self._end(session, 'error', f'模型没有给出答复（finish_reason={reply.finish_reason}）')
                return
            known = {i for ids in session['evidence'].values() for i in ids}
            results = [s.get('result') for s in session['steps'] if s.get('kind') == 'tool' and s.get('ok')]
            users = [m['content'] for m in session['messages'] if m.get('role') == 'user']
            report = grounding.check(reply.content, results, users, known)
            session['grounding'] = report
            self._append(session, {'kind': 'final', 'text': reply.content, 'reasoning': reasoning, 'model_s': elapsed,
                                   'grounding': report})
            self._end(session, 'idle')

    def _run_next_call(self, session: dict, ctx: ToolContext):
        with self._lock:
            if self._closed or self._running[session['session_id']].is_set():
                return
        call = session['queue'][0]
        tool = BY_NAME.get(call['name'])
        if 'args' not in call:
            try:
                parsed = json.loads(call['raw'])
            except ValueError:
                parsed = None
            call['args'] = parsed if isinstance(parsed, dict) else {}
            call['bad_args'] = not isinstance(parsed, dict)
        if tool is not None and tool.tier == 'actuate' and not call.get('approved') and not call['bad_args']:
            with self._lock:
                session['pending'] = {'call_id': call['id'], 'tool': tool.name, 'label': tool.label,
                                      'arguments': call['args'], 'requested_utc': utc_now()}
                session['state'] = 'awaiting_approval'
                self._append(session, {'kind': 'approval', 'call_id': call['id'], 'name': tool.name, 'label': tool.label,
                                       'arguments': call['args'], 'decision': 'pending'})
                self._running.pop(session['session_id'], None)
                self._save(session)
            return
        started = time.monotonic()
        ok, error, result = True, None, None
        if tool is None:
            ok, error = False, f"没有工具 {call['name']}；可用：{', '.join(BY_NAME)}"
        elif call['bad_args']:
            ok, error = False, '参数不是合法的 JSON 对象'
        else:
            try:
                result = tool.handler(ctx, call['args'])
            except ToolError as exc:
                ok, error = False, str(exc)
            except Exception as exc:
                log.exception('agent tool %s failed', call['name'])
                ok, error = False, f'{type(exc).__name__}: {exc}'
        duration = round(time.monotonic() - started, 3)
        with self._lock:
            if self._closed:
                return
            payload = result if ok else {'error': error}
            self._reply(session, call, payload)
            if ok:
                collect_ids(result, session['evidence'])
            self._append(session, {'kind': 'tool', 'call_id': call['id'], 'name': call['name'],
                                   'tier': tool.tier if tool else None, 'label': tool.label if tool else None,
                                   'arguments': call['args'], 'ok': ok, 'error': error, 'result': payload, 'duration_s': duration})
            session['usage']['tool_calls'] += 1
            session['turn_tool_calls'] = session.get('turn_tool_calls', 0) + 1
            session['queue'].pop(0)
            self._save(session)
