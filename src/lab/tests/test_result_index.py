import json
import threading
from types import SimpleNamespace

import pytest

from ceres_lab.fieldsim import FieldSimManager


def test_index_caches_compact_data_and_invalidates_replacements_and_deletions(tmp_path, monkeypatch):
    service = FieldSimManager(None, SimpleNamespace(_lock=threading.RLock()), runtime=tmp_path)
    p = tmp_path / 'fieldsim' / 's' / 'result.json'
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({'session_id': 's', 'run_id': 'r', 'metrics': {'value': 1},
                            'trails': {'v': [[0, 0, 0, 0]] * 1000}}))
    rows = service.results()
    assert rows[0]['metrics'] == {'value': 1} and 'trails' not in rows[0]
    rows[0]['metrics']['value'] = 999
    with monkeypatch.context() as m:
        m.setattr(type(p), 'read_text', lambda *a, **k: (_ for _ in ()).throw(AssertionError('cache missed')))
        assert service.results('r')[0]['metrics']['value'] == 1
        assert service.results('another') == []
    replacement = p.with_suffix('.tmp')
    replacement.write_text(json.dumps({'session_id': 's', 'run_id': 'r', 'metrics': {'value': 2}}))
    replacement.replace(p)
    assert service.results()[0]['metrics']['value'] == 2
    p.unlink()
    assert service.results() == [] and service._result_cache == {}


def test_live_evidence_omits_traces_but_full_export_preserves_them(tmp_path):
    from fastapi.testclient import TestClient
    from ceres_lab.gateway import Config, create_app
    p = tmp_path / 'fieldsim' / 'large' / 'result.json'
    p.parent.mkdir(parents=True)
    original = {'session_id': 'large', 'run_id': 'r', 'state': 'failed', 'check': {'valid': False},
                'physics': {'schema': 'test'}, 'trails': {'v': [[0, 0, 0, 0]] * 10000},
                'wheel_odometry': {'v': [[0, 0, 0, 0, 0, 0]] * 10000}}
    p.write_text(json.dumps(original))
    (p.parent / 'plan.json').write_text('{}')
    (p.parent / 'session-report.json').write_text('{"status":"FAIL","cleanup_status":"PASS"}')
    with TestClient(create_app(Config(runtime=tmp_path, background=False, archive_autostart=False,
                                      extra_hosts={'testserver'}))) as client:
        full = client.get('/lab/api/fieldsim/results/large')
        thin = client.get('/lab/api/fieldsim/results/large?evidence=true')
        assert full.status_code == thin.status_code == 200
        assert full.json()['trails'] == original['trails'] and full.json()['wheel_odometry'] == original['wheel_odometry']
        assert len(thin.content) < len(full.content) / 100
        assert thin.json()['check'] == {'valid': False} and thin.json()['session_report']['status'] == 'FAIL'
        assert 'trails' not in thin.json() and thin.headers['cache-control'] == 'no-store'
        assert client.get('/lab/api/fieldsim/results/missing?evidence=true').status_code == 404
    assert json.loads(p.read_text()) == original


@pytest.mark.parametrize('endpoint', ['runs', 'runs/r', 'runs/r/record', 'sim/runs', 'sim/runs/s', 'sim/state'])
def test_large_history_encoding_does_not_block_health_or_drop_evidence(tmp_path, monkeypatch, endpoint):
    """A stalled large reply must leave the ASGI loop available to controls."""
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from starlette.responses import JSONResponse
    from ceres_lab.assets.bindings import digest
    from ceres_lab.gateway import Config, create_app

    app = create_app(Config(runtime=tmp_path, background=False, archive_autostart=False,
                            extra_hosts={'testserver'}))
    samples = [[i / 50, i * .01, .2] for i in range(10000)]
    run = {'run_id': 'r', 'scenario_id': 'fixture', 'state': 'done', 'asset_snapshot': [],
           'instance': {}, 'raw_result': {'samples': samples}}
    sim = {'sim_run_id': 's', 'run_id': 'r', 'state': 'failed', 'actual': {'ground_truth': samples}}
    app.state.lab.store.put_run(run)
    app.state.lab.store.put_sim_run(sim)
    monkeypatch.setattr(app.state.lab.sim, 'current_state', lambda: sim)
    entered, release = threading.Event(), threading.Event()
    original = JSONResponse.render

    def blocked_render(self, content):
        if isinstance(content, dict) and any(k in content for k in ('run_id', 'record', 'runs', 'sim_runs')):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test render barrier was not released')
        return original(self, content)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(JSONResponse, 'render', blocked_render)
        pending = pool.submit(client.get, '/lab/api/' + endpoint)
        try:
            assert entered.wait(3)
            health = pool.submit(client.get, '/lab/api/health').result(timeout=2)
            assert health.status_code == 200
        finally:
            release.set()
        response = pending.result(timeout=3)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        data = response.json()
        if endpoint == 'runs/r/record':
            assert data['record']['planning'] == run
            assert data['record']['simulations']['nav2'] == [sim]
            assert data['integrity']['sha256'] == digest(data['record'])
        elif endpoint == 'runs':
            assert data == {'runs': [run]}
        elif endpoint == 'sim/runs':
            assert data == {'sim_runs': [sim]}
        else:
            assert data == (run if endpoint == 'runs/r' else sim)
