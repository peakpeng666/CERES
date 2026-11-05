"""Failure injection for the Lab execution owner and accepted code identity.

No ROS, GPU, solver run, or production evidence is needed.
"""
import subprocess
import sys

import pytest

from urp_lab.gateway import Config, create_app
from urp_lab import jobs
from urp_lab.scenarios.benchmark import cvrp_scenarios
from urp_lab.solvers import catalog
from urp_lab.store import Store


def test_second_gateway_cannot_recover_live_run_and_shutdown_releases_lock(tmp_path):
    first = create_app(Config(runtime=tmp_path, background=False))
    db = first.state.lab.store
    active = {'run_id': 'active-run', 'job_id': 'active-job', 'scenario_id': 'test',
              'state': 'running', 'created_utc': '2026-10-19T00:00:00Z', 'error': None}
    db.put_run(active)
    with pytest.raises(RuntimeError, match='已有任务执行服务'):
        create_app(Config(runtime=tmp_path, port=4201, background=False))
    assert db.get_run('active-run') == active  # no premature recovery/overwrite

    # The same owner rule must apply across processes, not only in one interpreter.
    probe = subprocess.run(
        [sys.executable, '-c', 'from urp_lab.store import Store; import sys; '
         'Store(sys.argv[1]); print("unexpected owner")', str(tmp_path / 'lab.sqlite3')],
        capture_output=True, text=True, check=False)
    assert probe.returncode != 0 and '已有任务执行服务' in probe.stderr
    assert db.get_run('active-run') == active

    first.state.lab.shutdown()
    reopened = create_app(Config(runtime=tmp_path, port=4201, background=False))
    assert reopened.state.lab.store.get_run('active-run')['state'] == 'error'
    reopened.state.lab.shutdown()
    # A normal close must release the owner lock for a third process too.
    next_owner = Store(tmp_path / 'lab.sqlite3')
    next_owner.close()


def test_symlink_alias_cannot_bypass_database_owner(tmp_path):
    primary = tmp_path / 'primary.sqlite3'
    alias = tmp_path / 'alias.sqlite3'
    owner = Store(primary)
    alias.symlink_to(primary)
    try:
        with pytest.raises(RuntimeError, match='已有任务执行服务'):
            Store(alias)
    finally:
        owner.close()
    Store(alias).close()


def test_queue_rejects_dependency_changed_after_submission(tmp_path, monkeypatch):
    extra = tmp_path / 'executed-dependency.py'
    extra.write_text('VERSION = 1\n')
    original_files = jobs._execution_files
    monkeypatch.setattr(jobs, '_execution_files', lambda: original_files() + [extra])
    monkeypatch.setattr(jobs, '_GATEWAY_IMPORT_MANIFEST', jobs.execution_manifest())
    db = Store(tmp_path / 'lab.sqlite3')
    manager = jobs.JobManager(db, command=[str(tmp_path / 'must-not-start')], concurrency=1)
    scenario = cvrp_scenarios()[0]
    algorithm = next(a for a in catalog() if a['id'] == 'pyvrp.ils')
    params = jobs.resolve_params(algorithm, {'time_limit_s': .25, 'seed': 11})
    try:
        _, ids = manager.submit(scenario, scenario['instance'], [(algorithm, params)])
        queued = db.get_run(ids[0])
        assert queued['state'] == 'queued'
        assert queued['reproducibility']['source_sha256'][str(extra)]
        assert 'src/lab/models/cvrp-knn-v1.json' in queued['reproducibility']['source_sha256']
        assert 'src/frontend/core/routing/cvrp-solver.js' in queued['reproducibility']['source_sha256']
        assert 'src/research/src/urp_research/cvrp_selection/experiment.py' in queued['reproducibility']['source_sha256']
        assert queued['reproducibility']['worker_environment_verified'] is False
        assert queued['reproducibility']['packages'] is None
        extra.write_text('VERSION = 2\n')
        sol, request, budget = manager._queue.get_nowait()
        manager._pending.pop(ids[0])  # mirror _loop claiming the queued entry
        result = manager.execute(sol, request, budget)
        assert result['state'] == 'error' and '执行前来源身份核对失败' in result['error']
        assert 'process' not in result  # no child process was started
        assert db.get_run(ids[0])['state'] == 'error'
    finally:
        manager.shutdown()
        db.close()
    Store(tmp_path / 'lab.sqlite3').close()


def test_default_worker_rejects_different_virtual_environment(tmp_path, monkeypatch):
    fake_lab = tmp_path / 'lab'
    python = fake_lab / '.venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.write_text('not used')
    monkeypatch.setattr(jobs.paths, 'LAB', fake_lab)
    db = Store(tmp_path / 'lab.sqlite3')
    try:
        with pytest.raises(jobs.SourceChangedError, match='不同 Python 环境'):
            jobs.JobManager(db)
    finally:
        db.close()


def test_source_change_during_child_invalidates_published_result(tmp_path, monkeypatch):
    extra = tmp_path / 'executed-dependency.py'
    extra.write_text('VERSION = 1\n')
    original_files = jobs._execution_files
    monkeypatch.setattr(jobs, '_execution_files', lambda: original_files() + [extra])
    monkeypatch.setattr(jobs, '_GATEWAY_IMPORT_MANIFEST', jobs.execution_manifest())
    code = ('import json;from pathlib import Path;'
            f'Path({str(extra)!r}).write_text("VERSION = 2\\n");'
            'print(json.dumps({"status":"feasible","routes":[[1]],'
            '"objective":{"value":7},"check":{"valid":True},'
            '"metrics":{"cost":7},"raw_result":{"candidate":"raw"}}))')
    db = Store(tmp_path / 'lab.sqlite3')
    manager = jobs.JobManager(db, command=[sys.executable, '-c', code], concurrency=1)
    scenario = cvrp_scenarios()[0]
    algorithm = next(a for a in catalog() if a['id'] == 'pyvrp.ils')
    try:
        _, ids = manager.submit(scenario, scenario['instance'], [(algorithm, jobs.resolve_params(algorithm, {}))])
        sol, request, budget = manager._queue.get_nowait()
        manager._pending.pop(ids[0])
        result = manager.execute(sol, request, budget)
        assert result['state'] == 'error' and '执行期间来源身份核对失败' in result['error']
        assert result['process']['returncode'] == 0
        assert result['process']['source_identity_verified'] is False
        assert result['check'] is None and result['objective']['value'] is None
        assert result['metrics']['cost'] is None and result['routes'] == []
        assert result['raw_result'] == {'candidate': 'raw'}
        assert (tmp_path / 'runs' / ids[0] / 'stdout.json').is_file()
    finally:
        manager.shutdown()
        db.close()


def test_loaded_gateway_rejects_changed_dependency_before_new_submission(tmp_path, monkeypatch):
    extra = tmp_path / 'recommend-dependency.py'
    extra.write_text('VERSION = 1\n')
    original_files = jobs._execution_files
    monkeypatch.setattr(jobs, '_execution_files', lambda: original_files() + [extra])
    monkeypatch.setattr(jobs, '_GATEWAY_IMPORT_MANIFEST', jobs.execution_manifest())
    db = Store(tmp_path / 'lab.sqlite3')
    manager = jobs.JobManager(db, concurrency=1)
    scenario = cvrp_scenarios()[0]
    algorithm = next(a for a in catalog() if a['id'] == 'pyvrp.ils')
    try:
        extra.write_text('VERSION = 2\n')
        with pytest.raises(jobs.SourceChangedError):
            manager.submit(scenario, scenario['instance'], [(algorithm, jobs.resolve_params(algorithm, {}))])
        assert db.list_runs() == []
    finally:
        manager.shutdown()
        db.close()


def test_missing_optional_model_is_recorded_not_startup_failure(tmp_path, monkeypatch):
    original_files = jobs._execution_files
    missing = tmp_path / 'missing-model.json'
    monkeypatch.setattr(jobs, '_execution_files',
                        lambda: [p for p in original_files() if p.name != 'cvrp-knn-v1.json'] + [missing])
    manifest = jobs.execution_manifest()
    assert manifest['files'][str(missing)] is None


def test_failed_gateway_construction_releases_owner_lock(tmp_path, monkeypatch):
    extra = tmp_path / 'source-loaded-after-gateway.py'
    extra.write_text('VERSION = 1\n')
    original_files = jobs._execution_files
    monkeypatch.setattr(jobs, '_execution_files', lambda: original_files() + [extra])
    # A changed import-time tree must reject before recovery, even if the DB
    # itself opened. Its lock must not leak from Lab's failed constructor.
    with pytest.raises(jobs.SourceChangedError):
        create_app(Config(runtime=tmp_path, background=False))
    Store(tmp_path / 'lab.sqlite3').close()
