"""POST /scenarios and /fork are idempotent per client id: identical retry succeeds, changed content conflicts."""
import copy
import pytest
from fastapi.testclient import TestClient
from urp_lab import paths, scenarios
from urp_lab.gateway import Config, create_app


def payload():
    return {'id': 'idem-import', 'title': '幂等导入', 'problem_class': 'coverage',
            'frame': {'kind': 'field', 'units': 'm', 'crs': 'local'},
            'instance': {'field': [[0, 0], [100, 0], [100, 100], [0, 100]],
                         'obstacles': [], 'swath_width': 3, 'headland_width': 5, 'angle_deg': 0},
            'provenance': {'generator': 'test', 'params': {'w': 100}, 'seed': 7}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    monkeypatch.setattr(scenarios, '_builtin', lambda: {})
    with TestClient(create_app(Config(runtime=tmp_path, background=False, extra_hosts={'testserver'}))) as c:
        yield c


def user_files(tmp_path):
    return sorted(p.name for p in (tmp_path / 'scenarios').glob('*.json'))


def test_import_retry_with_identical_content_returns_existing(client, tmp_path):
    assert client.post('/lab/api/scenarios', json=payload()).status_code == 201
    again = client.post('/lab/api/scenarios', json=payload())
    assert again.status_code == 200 and again.json()['id'] == 'idem-import'
    assert user_files(tmp_path) == ['idem-import.json']
    assert client.get('/lab/api/scenarios/idem-import').json()['revision'] == 1


@pytest.mark.parametrize('mutate', [
    lambda p: p['instance'].update(swath_width=4),
    lambda p: p.update(title='别的标题'),
    lambda p: p['provenance'].update(seed=8),
    lambda p: p['provenance'].update(params={'w': 101}),
    lambda p: p['frame'].update(crs='EPSG:28992'),
    lambda p: p.update(cost_unit='different-unit'),
    lambda p: p.update(summary='Different summary'),
    lambda p: p.update(layers={'source': 'another-map'}),
    lambda p: p['provenance'].update(generator='another-generator'),
])
def test_import_same_id_different_content_conflicts(client, tmp_path, mutate):
    assert client.post('/lab/api/scenarios', json=payload()).status_code == 201
    changed = copy.deepcopy(payload()); mutate(changed)
    assert client.post('/lab/api/scenarios', json=changed).status_code == 409
    assert client.get('/lab/api/scenarios/idem-import').json()['title'] == '幂等导入'


def test_import_retry_after_edit_conflicts(client):
    first = client.post('/lab/api/scenarios', json=payload()).json()
    sc = client.get('/lab/api/scenarios/idem-import').json()
    edited = client.put('/lab/api/scenarios/idem-import', json={'instance': sc['instance'], 'base_revision': sc['revision'], 'title': '已改'})
    assert edited.status_code == 200, edited.text
    assert first['id'] == 'idem-import'
    assert client.post('/lab/api/scenarios', json=payload()).status_code == 409


def test_invalid_retry_frame_is_a_validation_error(client):
    assert client.post('/lab/api/scenarios', json=payload()).status_code == 201
    changed = payload()
    changed['frame'] = ['field']
    assert client.post('/lab/api/scenarios', json=changed).status_code == 400


def test_fork_with_client_id_is_idempotent(client, tmp_path):
    assert client.post('/lab/api/scenarios', json=payload()).status_code == 201
    inst = client.get('/lab/api/scenarios/idem-import').json()['instance']
    body = {'instance': inst, 'client_id': 'idem-import-my-abc123'}
    first = client.post('/lab/api/scenarios/idem-import/fork', json=body)
    assert first.status_code == 201, first.text
    assert first.json()['id'] == 'idem-import-my-abc123'
    again = client.post('/lab/api/scenarios/idem-import/fork', json=body)
    assert again.status_code == 200, again.text
    assert again.json()['id'] == 'idem-import-my-abc123' and again.json()['revision'] == 1
    assert user_files(tmp_path) == ['idem-import-my-abc123.json', 'idem-import.json']


def test_fork_same_client_id_different_content_conflicts(client, tmp_path):
    assert client.post('/lab/api/scenarios', json=payload()).status_code == 201
    inst = client.get('/lab/api/scenarios/idem-import').json()['instance']
    assert client.post('/lab/api/scenarios/idem-import/fork', json={'instance': inst, 'client_id': 'fork-x1'}).status_code == 201
    other = {**inst, 'swath_width': 4}
    assert client.post('/lab/api/scenarios/idem-import/fork', json={'instance': other, 'client_id': 'fork-x1'}).status_code == 409
    assert client.post('/lab/api/scenarios/idem-import/fork', json={'instance': inst, 'client_id': 'fork-x1', 'title': '别名'}).status_code == 409
    assert client.get('/lab/api/scenarios/fork-x1').json()['instance']['swath_width'] == 3


def test_fork_client_id_cannot_take_over_foreign_or_invalid_ids(client):
    assert client.post('/lab/api/scenarios', json=payload()).status_code == 201
    # Another source's fork id: content/provenance differ, so it is a conflict, not adoption.
    assert client.post('/lab/api/scenarios/idem-import/fork', json={'client_id': 'idem-import'}).status_code == 409
    assert client.post('/lab/api/scenarios/idem-import/fork', json={'client_id': 'Bad ID'}).status_code == 400
    assert client.post('/lab/api/scenarios/idem-import/fork', json={'client_id': 5}).status_code == 400


def test_fork_without_client_id_keeps_server_ids(client):
    assert client.post('/lab/api/scenarios', json=payload()).status_code == 201
    a = client.post('/lab/api/scenarios/idem-import/fork', json={}).json()['id']
    b = client.post('/lab/api/scenarios/idem-import/fork', json={}).json()['id']
    assert a != b and a.startswith('idem-import-my-')
