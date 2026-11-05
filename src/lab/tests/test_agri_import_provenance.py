"""Imported generator parameters remain reproducible without granting simulation access."""
import copy
import json
import pytest
from urp_lab import paths
from urp_lab import scenarios


def payload():
    return {'id': 'agri-import-test', 'title': '农业生成场景', 'problem_class': 'coverage',
            'frame': {'kind': 'field', 'units': 'm', 'crs': 'local'},
            'instance': {'field': [[0, 0], [100, 0], [100, 100], [0, 100]],
                         'obstacles': [], 'swath_width': 3, 'headland_width': 5, 'angle_deg': 0},
            'provenance': {'generator': 'urp.studio.agri', 'params': {'field_w': 100, 'pattern': 'coverage-tillage'}, 'seed': 42},
            'sim': {'supported': True}}


def test_import_preserves_generator_inputs_and_server_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path)
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    source = payload()
    original = copy.deepcopy(source)
    result = scenarios.import_scenario(source)
    saved = json.loads((tmp_path / (result['id'] + '.json')).read_text())
    assert source == original
    assert saved['provenance']['generator'] == 'user_import'
    assert saved['provenance']['source_generator'] == 'urp.studio.agri'
    assert saved['provenance']['params'] == original['provenance']['params']
    assert saved['provenance']['seed'] == 42
    assert saved['sim']['supported'] is False


@pytest.mark.parametrize('seed', [True, '42', 1.5])
def test_import_rejects_noninteger_seed(seed, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path)
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    obj = payload(); obj['provenance']['seed'] = seed
    with pytest.raises(ValueError, match='种子'):
        scenarios.import_scenario(obj)
    assert not list(tmp_path.glob('*.json'))


def test_import_rejects_nonfinite_generator_parameter(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path)
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    obj = payload(); obj['provenance']['params']['field_w'] = float('nan')
    with pytest.raises(ValueError, match='生成参数'):
        scenarios.import_scenario(obj)
    assert not list(tmp_path.glob('*.json'))


def test_legacy_import_without_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path)
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    obj = payload(); del obj['provenance']
    result = scenarios.import_scenario(obj)
    saved = json.loads((tmp_path / (result['id'] + '.json')).read_text())
    assert saved['provenance']['params'] == {} and saved['provenance']['seed'] is None


def test_reimport_keeps_original_generator(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path)
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    scenarios.import_scenario(payload())
    saved = json.loads((tmp_path / 'agri-import-test.json').read_text())
    saved['id'] = 'agri-import-copy'
    scenarios.import_scenario(saved)
    restored = json.loads((tmp_path / 'agri-import-copy.json').read_text())
    assert restored['provenance']['source_generator'] == 'urp.studio.agri'
    assert restored['provenance']['params'] == saved['provenance']['params']
    assert restored['provenance']['seed'] == 42


def test_duplicate_import_preserves_existing_revision_and_content(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path)
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    scenarios.import_scenario(payload())
    target = tmp_path / 'agri-import-test.json'
    before = target.read_bytes()
    modified = payload(); modified['title'] = '不能覆盖原草稿'
    with pytest.raises(scenarios.ScenarioConflict, match='已存在'):
        scenarios.import_scenario(modified)
    assert target.read_bytes() == before
    assert json.loads(before)['revision'] == 1


def test_concurrent_import_creates_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path)
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    barrier = Barrier(2)
    def attempt(title):
        obj = payload(); obj['title'] = title
        barrier.wait(timeout=5)
        try:
            scenarios.import_scenario(obj)
            return title
        except scenarios.ScenarioConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ['first', 'second']))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    saved = json.loads((tmp_path / 'agri-import-test.json').read_text())
    assert saved['title'] == winners[0] and saved['revision'] == 1
    assert not list(tmp_path.glob('*.tmp'))


def test_http_duplicate_import_is_idempotent_and_changed_content_conflicts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from urp_lab.gateway import Config, create_app
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    with TestClient(create_app(Config(runtime=tmp_path, background=False, extra_hosts={'testserver'}))) as client:
        first = client.post('/lab/api/scenarios', json=payload())
        assert first.status_code == 201, first.text
        second = client.post('/lab/api/scenarios', json=payload())
        assert second.status_code == 200, second.text
        assert second.json()['id'] == first.json()['id']
        changed = payload(); changed['instance']['swath_width'] = 4
        third = client.post('/lab/api/scenarios', json=changed)
        assert third.status_code == 409, third.text
        restored = client.get('/lab/api/scenarios/agri-import-test')
        assert restored.status_code == 200
        assert restored.json()['revision'] == 1


@pytest.mark.parametrize('sid', ['maze-inspection', 'p2p-maze-cross'])
def test_official_map_ids_remain_reserved_without_maps(sid, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path)
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    obj = payload(); obj['id'] = sid
    with pytest.raises(ValueError, match='内置'):
        scenarios.import_scenario(obj)
    with pytest.raises(ValueError, match='内置'):
        scenarios.save_user_scenario(obj)
    assert not list(tmp_path.glob('*.json'))
