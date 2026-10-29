"""Agent session documents and their SQLite store (``runtime/agent.sqlite3``, separate from ``lab.sqlite3``)."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import contract as tc

SCHEMA = 'urp.agent-session/1'
STATES = ('idle', 'running', 'awaiting_user', 'awaiting_confirmation', 'awaiting_approval', 'paused', 'cancelled',
          'failed', 'done', 'interrupted')
ACTIVE = ('running',)
STEP_TITLES = {'understand': '理解需求', 'confirm': '确认任务合同', 'scenario': '建立任务地图', 'recommend': '推荐算法',
               'solve': '求解对比', 'study': '重复实验', 'verify': '独立复核', 'repair': '诊断与修复', 'simulate': '田间仿真',
               'report': '结果报告'}
NUMBER = re.compile(r'(?<![\w.])-?\d+(?:\.\d+)?')


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def new_id() -> str:
    return f"agt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(3)}"


def new_session(mode: str, provider: dict | None, auto_confirm: bool = False, title: str = '') -> dict:
    now = utc_now()
    return {'schema': SCHEMA, 'session_id': new_id(), 'title': title or '新任务', 'created_utc': now, 'updated_utc': now,
            'state': 'idle', 'phase': 'understand', 'mode': mode, 'provider': provider, 'auto_confirm': bool(auto_confirm),
            'messages': [], 'llm_messages': [], 'contract': tc.empty(), 'plan': initial_plan(), 'tool_calls': [],
            'llm_calls': [], 'usage': {'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0, 'currency': None, 'calls': 0,
                                       'latency_s': 0.0}, 'scenario_id': None, 'run_ids': [], 'tried_algorithms': [],
            'precheck': None, 'verifications': {}, 'recommendation': None, 'pending': None, 'error': None, 'turn': 0,
            'report': None}


def initial_plan() -> list[dict]:
    return [{'id': key, 'kind': key, 'title': STEP_TITLES[key], 'status': 'pending', 'detail': ''}
            for key in ('understand', 'confirm', 'scenario', 'recommend', 'solve', 'verify', 'repair', 'report')]


def ensure_step(session: dict, kind: str, before: str = 'report'):
    if any(s['kind'] == kind for s in session['plan']):
        return
    index = next((i for i, s in enumerate(session['plan']) if s['kind'] == before), len(session['plan']))
    session['plan'].insert(index, {'id': kind, 'kind': kind, 'title': STEP_TITLES[kind], 'status': 'pending', 'detail': ''})


def set_step(session: dict, kind: str | None, status: str, detail: str | None = None):
    for step in session['plan']:
        if step['kind'] == kind:
            if step['status'] == 'taken_over' and status != 'pending':
                return
            step['status'] = status
            if detail is not None:
                step['detail'] = detail[:200]
            step['updated_utc'] = utc_now()
            return


def reset_execution_plan(session: dict):
    for step in session['plan']:
        if step['kind'] not in ('understand', 'confirm'):
            step.update(status='pending', detail='')


def add_message(session: dict, role: str, text: str, kind: str = 'text', data: dict | None = None) -> dict:
    msg = {'id': f"m{len(session['messages']) + 1}", 'role': role, 'kind': kind, 'text': text, 'utc': utc_now()}
    if data is not None:
        msg['data'] = data
    session['messages'].append(msg)
    return msg


def add_usage(session: dict, record: dict):
    u = session['usage']
    usage = record.get('usage') or {}
    u['input_tokens'] += int(usage.get('input_tokens') or 0)
    u['output_tokens'] += int(usage.get('output_tokens') or 0)
    u['calls'] += 1
    u['latency_s'] = round(u['latency_s'] + float(record.get('latency_s') or 0), 3)
    if record.get('cost') is not None:
        u['cost'] = round((u['cost'] or 0) + record['cost'], 6)
        u['currency'] = record.get('currency')
    else:
        u['unpriced_calls'] = u.get('unpriced_calls', 0) + 1  # subscription quota or no price configured


def claims_check(text: str, evidence) -> list[str]:
    """Numbers in a narrative that do not occur in the evidence (to 0–2 decimals); flags fabricated figures."""
    known: set[str] = set()

    def collect(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            for nd in (0, 1, 2, 3):
                known.add(f'{round(float(v), nd):.{nd}f}')
            known.add(str(v))
        elif isinstance(v, dict):
            for x in v.values():
                collect(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                collect(x)
        elif isinstance(v, str):
            for m in NUMBER.findall(v):
                known.add(m)
    collect(evidence)
    flagged = []
    for m in NUMBER.findall(text or ''):
        if len(m.replace('-', '').replace('.', '')) < 2:
            continue  # single digits are ordinal words, not measured values
        candidates = {m}
        if '.' in m:
            whole, frac = m.split('.')
            candidates |= {f'{float(m):.{nd}f}' for nd in range(0, len(frac) + 1)}
        if not candidates & known and m not in flagged:
            flagged.append(m)
    return flagged


def public(session: dict) -> dict:
    """Session as served to the browser: internal model messages are summarized, never secrets (none are stored)."""
    out = {k: v for k, v in session.items() if k != 'llm_messages'}
    out['llm_context'] = {'messages': len(session.get('llm_messages') or [])}
    out['contract_cards'] = tc.cards(session['contract'])
    out['missing'] = [{'key': k, 'label': tc.FIELDS[k].label} for k in tc.missing(session['contract'])]
    return out


class AgentStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, created_utc TEXT, '
                         'updated_utc TEXT, state TEXT, title TEXT, json TEXT NOT NULL)')

    def save(self, session: dict):
        session['updated_utc'] = utc_now()
        data = json.dumps(session, ensure_ascii=False, allow_nan=False, default=str)
        with self._lock:
            self._db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET '
                             'updated_utc=excluded.updated_utc, state=excluded.state, title=excluded.title, json=excluded.json',
                             (session['session_id'], session['created_utc'], session['updated_utc'], session['state'],
                              session['title'], data))

    def get(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute('SELECT json FROM sessions WHERE session_id=?', (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit: int = 60) -> list[dict]:
        with self._lock:
            rows = self._db.execute('SELECT json FROM sessions ORDER BY updated_utc DESC LIMIT ?', (limit,)).fetchall()
        out = []
        for (raw,) in rows:
            s = json.loads(raw)
            out.append({k: s.get(k) for k in ('session_id', 'title', 'state', 'phase', 'mode', 'created_utc',
                                              'updated_utc', 'scenario_id')} | {'runs': len(s.get('run_ids') or []),
                                                                                'provider': (s.get('provider') or {}).get('model')})
        return out

    def by_state(self, states: tuple[str, ...]) -> list[dict]:
        with self._lock:
            rows = self._db.execute(f"SELECT json FROM sessions WHERE state IN ({','.join('?' * len(states))})", states).fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self):
        with self._lock:
            self._db.close()
