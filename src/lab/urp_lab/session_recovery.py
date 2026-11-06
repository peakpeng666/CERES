"""Recover public session exports as read-only evidence, never as invented runnable model context."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from .runtime_transfer import TransferError, _json, canonical, digest, utc_now

ID = re.compile(r'^[A-Za-z0-9_-]{1,160}$')


def recover_exports(runtime, sources):
    from .agent.session import AgentStore, SCHEMA
    from .store import Store
    runtime = Path(runtime).expanduser().resolve()
    records, sessions, runs, scenarios = [], {}, {}, {}
    for source in sources:
        source = Path(source).expanduser().resolve()
        raw = source.read_bytes()
        data = _json(raw)
        envelope = 'record' in data
        if envelope:
            rec, integrity = data['record'], data.get('integrity') or {}
            if rec.get('schema') != 'urp.agent-export/1' or integrity.get('algorithm') != 'sha256' or hashlib.sha256(canonical(rec)).hexdigest() != integrity.get('sha256'):
                raise TransferError(f'会话导出校验不通过：{source}')
            session = rec.get('session')
        else:
            rec, session = {}, data
        if not isinstance(session, dict) or session.get('schema') != SCHEMA or not ID.fullmatch(session.get('session_id') or ''):
            raise TransferError(f'不支持的会话记录：{source}')
        sid = session['session_id']
        if sid in sessions:
            raise TransferError(f'重复会话，请选择一个明确版本：{sid}')
        for key in ('created_utc', 'updated_utc', 'state', 'title', 'contract', 'messages', 'plan'):
            if key not in session:
                raise TransferError(f'会话缺少 {key}：{source}')
        sessions[sid] = dict(session)
        for rid, run in (rec.get('runs') or {}).items():
            if run is None:
                continue
            if not ID.fullmatch(rid) or run.get('run_id') != rid:
                raise TransferError('无效运行 ID')
            if rid in runs and canonical(runs[rid]) != canonical(run):
                raise TransferError(f'导出间的运行记录冲突：{rid}')
            runs[rid] = run
        for scid, scenario in (rec.get('scenarios') or {}).items():
            if scenario is None:
                continue
            if not ID.fullmatch(scid) or scenario.get('id') != scid:
                raise TransferError('无效场景 ID')
            if scid in scenarios and canonical(scenarios[scid]) != canonical(scenario):
                raise TransferError(f'导出间的场景版本冲突：{scid}')
            scenarios[scid] = scenario
        records.append({'source': str(source), 'sha256': hashlib.sha256(raw).hexdigest(), 'session_id': sid,
                        'envelope_verified': envelope, 'raw': raw})
    if not records:
        raise TransferError('没有提供会话导出')
    store = Store(runtime / 'lab.sqlite3')  # claims the same execution lock as the gateway
    agent = AgentStore(runtime / 'agent.sqlite3')
    try:
        for sid in sessions:
            if agent.get(sid) is not None:
                raise TransferError(f'已有会话不会被覆盖：{sid}')
        for rid, run in runs.items():
            current = store.get_run(rid)
            if current is not None and canonical(current) != canonical(run):
                raise TransferError(f'已有运行不会被覆盖：{rid}')
        for sid, sc in scenarios.items():
            target = runtime / 'scenarios' / f'{sid}.json'
            if target.exists() and canonical(_json(target.read_bytes())) != canonical(sc):
                raise TransferError(f'已有场景不会被覆盖：{sid}')
        import_id = 'recovery-' + uuid.uuid4().hex
        location = runtime / 'imports' / import_id
        location.mkdir(parents=True, mode=0o700)
        for record in records:
            target = location / f"{record['session_id']}.json"
            target.write_bytes(record.pop('raw'))
            record['preserved_at'] = target.relative_to(runtime).as_posix()
        inserted_runs, created_scenarios = [], []
        for rid in runs:
            if store.get_run(rid) is None:
                inserted_runs.append(rid)
        for record in records:
            session = sessions[record['session_id']]
            session['restoration'] = {
                'read_only': True, 'recovered_utc': utc_now(), 'source_sha256': record['sha256'],
                'source_record': record['preserved_at'], 'integrity_verified': record['envelope_verified'],
                'llm_context_available': 'llm_messages' in session,
                'raw_runtime_available': False,
                'missing_run_ids': [rid for rid in session.get('run_ids', []) if rid not in runs and store.get_run(rid) is None],
                'note': '从保存的会话记录恢复。完整模型上下文、原始轨迹及部分附件未随导出保存；仅供查阅，重新执行请新建任务。'}
        # One SQLite transaction for both indices; original JSON remains separately preserved byte for byte.
        db = sqlite3.connect(store.path)
        try:
            db.execute('ATTACH DATABASE ? AS recovered_agent', (str(agent.path),))
            db.execute('BEGIN IMMEDIATE')
            for rid in inserted_runs:
                s = runs[rid]
                db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)', (rid, s.get('job_id'), s.get('scenario_id'),
                           (s.get('algorithm') or {}).get('id'), s.get('state'), s.get('status'), s.get('created_utc'),
                           s.get('finished_utc'), json.dumps(s, ensure_ascii=False, allow_nan=False)))
            for sid, s in sessions.items():
                db.execute('INSERT INTO recovered_agent.sessions VALUES (?,?,?,?,?,?)', (sid, s['created_utc'], s['updated_utc'],
                           s['state'], s['title'], json.dumps(s, ensure_ascii=False, allow_nan=False)))
            for sid, sc in scenarios.items():
                target = runtime / 'scenarios' / f'{sid}.json'
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as f:
                        f.write(canonical(sc))
                    created_scenarios.append(target)
            db.commit()
        except BaseException:
            db.rollback()
            for p in created_scenarios:
                p.unlink()
            raise
        finally:
            db.close()
        receipt = {'schema': 'urp.session-recovery/1', 'recovered_utc': utc_now(), 'runtime': str(runtime),
                   'sessions': len(sessions), 'runs_added': len(inserted_runs), 'scenarios_added': len(created_scenarios),
                   'records': records, 'read_only': True, 'full_raw_runtime_recovered': False}
        (location / 'receipt.json').write_bytes(canonical(receipt))
        return receipt
    finally:
        agent.close()
        store.close()
