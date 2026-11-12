"""Data continuity across relocated runtimes; no environment or real model is involved."""
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from ceres_lab.runtime_transfer import TransferError, canonical, database_info, export_bundle, restore_bundle, verify_bundle
from ceres_lab.session_recovery import recover_exports
from ceres_lab.store import Store
from ceres_lab.agent.session import AgentStore, new_session
from ceres_lab.agent.runner import AgentRunner, RunnerError


def test_sqlite_wal_raw_failures_and_context_survive_relocation(tmp_path):
    runtime = tmp_path / 'source' / 'runtime'
    runtime.mkdir(parents=True)
    db = sqlite3.connect(runtime / 'lab.sqlite3')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE evidence (id TEXT, state TEXT, seed INTEGER, unit TEXT, raw BLOB)')
    db.execute('INSERT INTO evidence VALUES (?,?,?,?,?)', ('original', 'failed', 47, 'uncalibrated_xy', b'\x00\xff'))
    db.commit()  # keep the connection open, so the row still lives in the WAL
    agent = AgentStore(runtime / 'agent.sqlite3')
    session = new_session('rule', None)
    session['llm_messages'] = [{'role': 'user', 'content': 'original context'}]
    agent.save(session)
    agent.close()
    (runtime / 'runs').mkdir()
    (runtime / 'runs' / 'trajectory.raw').write_bytes(b'untouched\0trajectory')
    (runtime / 'native').mkdir()
    (runtime / 'native' / 'host-only.so').write_bytes(b'compiled')
    external = tmp_path / 'outside'
    external.write_bytes(b'never follow')
    try:
        (runtime / 'external-link').symlink_to(external)
    except OSError:
        pass  # Windows without developer-mode symlink permission
    before = (runtime / 'lab.sqlite3').read_bytes()
    archive = tmp_path / 'archive.sqlite3'
    with sqlite3.connect(archive) as a:
        a.execute('CREATE TABLE immutable (id TEXT)')
        a.execute('INSERT INTO immutable VALUES (?)', ('frozen',))
    bundle = tmp_path / 'backup.zip'
    receipt = export_bundle(runtime, bundle, archive)
    assert (runtime / 'lab.sqlite3').read_bytes() == before
    assert verify_bundle(bundle, receipt['sha256'])['verified']
    relocated = tmp_path / 'another-machine' / 'workbench'
    restore_bundle(bundle, relocated, receipt['sha256'])
    assert (relocated / 'runtime/runs/trajectory.raw').read_bytes() == b'untouched\0trajectory'
    assert not (relocated / 'runtime/native').exists()
    assert not (relocated / 'runtime/external-link').exists()
    with sqlite3.connect(relocated / 'runtime/lab.sqlite3') as check:
        assert check.execute('SELECT * FROM evidence').fetchone() == ('original', 'failed', 47, 'uncalibrated_xy', b'\x00\xff')
    copied_agent = AgentStore(relocated / 'runtime/agent.sqlite3')
    assert copied_agent.get(session['session_id'])['llm_messages'] == session['llm_messages']
    copied_agent.close()
    assert database_info(relocated / 'archive/research.sqlite3')['rows'] == {'immutable': 1}
    assert json.loads((relocated / 'dataset.json').read_text()) == receipt['dataset']
    with pytest.raises(TransferError, match='覆盖'):
        restore_bundle(bundle, relocated)
    with pytest.raises(TransferError, match='尚不存在'):
        export_bundle(runtime, bundle)
    db.close()


def test_live_owner_and_credentials_cannot_be_exported(tmp_path):
    runtime = tmp_path / 'runtime'
    store = Store(runtime / 'lab.sqlite3')
    with pytest.raises(TransferError, match='仍在使用'):
        export_bundle(runtime, tmp_path / 'busy.zip')
    store.close()
    (runtime / 'credentials.json').write_text('{"test":"private"}')
    with pytest.raises(TransferError, match='凭据'):
        export_bundle(runtime, tmp_path / 'private.zip')
    assert not (tmp_path / 'private.zip').exists()


@pytest.mark.parametrize('change', ['corrupt', 'traversal', 'duplicate', 'case', 'symlink', 'missing', 'extra', 'schema'])
def test_invalid_bundle_never_creates_restore_destination(tmp_path, change):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'record.json').write_text('{"state":"failed"}')
    good = tmp_path / 'good.zip'
    export_bundle(runtime, good)
    bad = tmp_path / 'bad.zip'
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, 'w') as dst:
        for item in src.infolist():
            raw = src.read(item)
            if change == 'missing' and item.filename == 'runtime/record.json':
                continue
            if change == 'corrupt' and item.filename == 'runtime/record.json':
                raw = b'X' * len(raw)
            if change == 'schema' and item.filename == 'manifest.json':
                value = json.loads(raw); value['schema'] = 'ceres.runtime-bundle/future'; raw = canonical(value)
            dst.writestr(item, raw)
        if change == 'traversal':
            dst.writestr('runtime/../../escape', b'bad')
        elif change == 'duplicate':
            with pytest.warns(UserWarning):
                dst.writestr('runtime/record.json', b'bad')
        elif change == 'case':
            dst.writestr('runtime/RECORD.json', b'bad')
        elif change == 'symlink':
            item = zipfile.ZipInfo('runtime/link'); item.external_attr = 0o120777 << 16
            dst.writestr(item, b'/etc/passwd')
        elif change == 'extra':
            dst.writestr('runtime/extra', b'bad')
    dest = tmp_path / 'restored'
    with pytest.raises((TransferError, KeyError)):
        restore_bundle(bad, dest)
    assert not dest.exists()
    assert not (tmp_path / 'escape').exists()


def test_public_export_recovery_is_verifiable_read_only_and_non_overwriting(tmp_path):
    runtime = tmp_path / 'new-data' / 'runtime'
    session = new_session('rule', None)
    session['state'] = 'failed'
    session['error'] = 'real failure'
    session.pop('llm_messages')  # a public export is not a complete live session
    session['run_ids'] = ['run_preserved', 'run_missing']
    run = {'run_id': 'run_preserved', 'algorithm': {'id': 'test'}, 'state': 'failed', 'status': 'infeasible', 'seed': 123}
    record = {'schema': 'urp.agent-export/1', 'session': session, 'runs': {'run_preserved': run}, 'scenarios': {}}
    envelope = {'record': record, 'integrity': {'algorithm': 'sha256', 'sha256': hashlib.sha256(canonical(record)).hexdigest()}}
    source = tmp_path / 'session.json'
    source.write_bytes(canonical(envelope))
    receipt = recover_exports(runtime, [source])
    assert receipt['sessions'] == 1 and receipt['runs_added'] == 1
    assert not receipt['full_raw_runtime_recovered']
    assert (runtime / receipt['records'][0]['preserved_at']).read_bytes() == source.read_bytes()
    store = Store(runtime / 'lab.sqlite3')
    assert store.get_run('run_preserved') == run
    runner = AgentRunner(SimpleNamespace(runtime=runtime, store=store), providers=[])
    recovered = runner.get(session['session_id'])
    assert recovered['state'] == 'failed' and recovered['error'] == 'real failure'
    assert recovered['restoration']['missing_run_ids'] == ['run_missing']
    assert not recovered['restoration']['llm_context_available']
    for fn, args in ((runner.message, ('hello',)), (runner.control, ('retry',)), (runner.edit_contract, ({},))):
        with pytest.raises(RunnerError, match='仅供查阅'):
            fn(session['session_id'], *args)
    runner.shutdown()
    store.close()
    with pytest.raises(TransferError, match='不会被覆盖'):
        recover_exports(runtime, [source])
    corrupted = tmp_path / 'corrupted.json'
    envelope['record']['session']['error'] = 'forged success'
    corrupted.write_bytes(canonical(envelope))
    with pytest.raises(TransferError, match='校验不通过'):
        recover_exports(runtime, [corrupted])
