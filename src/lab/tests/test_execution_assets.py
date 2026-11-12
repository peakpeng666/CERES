import copy

import pytest
from fastapi.testclient import TestClient

from ceres_lab import paths
from ceres_lab.assets import bindings, importer, store
from ceres_lab.gateway import Config, create_app
from ceres_lab.services import ServiceError
from test_backend_integrity import solution


@pytest.fixture
def asset(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'RUNTIME', tmp_path)
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    importer.ensure_builtins()
    m = store.load(bindings.FIELD_VEHICLE)
    v = store.version(m)
    return {'id': m['id'], 'version': v['v'], 'sha256': v['sha256'], 'role': 'vehicle'}


def bound_run(asset):
    r = solution()
    r['instance']['asset_refs'] = [asset]
    r['instance']['fleet'] = {'asset': asset}
    r['instance']['vehicles'].update(capacity=12, speed_mps=1.0)
    return r


def test_execution_uses_server_asset_and_rejects_drift(asset):
    run = bound_run(asset)
    run['asset_snapshot'] = bindings.snapshot(run['instance'])
    assert bindings.field_vehicle(run)['reference'] == bindings.identity(asset)
    with pytest.raises(ServiceError) as e:
        bindings.field_vehicle(run, {**asset, 'sha256': 'wrong'})
    assert e.value.status == 409
    run['instance']['vehicles']['speed_mps'] = 2
    with pytest.raises(ServiceError, match='速度'):
        bindings.field_vehicle(run)


def test_imported_vehicle_cannot_borrow_builtin_adapter(asset):
    m = store.load(bindings.FIELD_VEHICLE)
    m.update(id='vehicle-imported-test', builtin=False)
    store.asset_dir(m['id']).mkdir()
    store.save(m)
    run = bound_run({**asset, 'id': m['id']})
    with pytest.raises(ServiceError) as e:
        bindings.field_vehicle(run)
    assert e.value.status == 422 and e.value.code == 'unsupported_vehicle_asset'


@pytest.mark.parametrize('change', ['hash', 'version', 'draft'])
def test_snapshot_rejects_invalid_or_unlocked_reference(asset, change):
    ref = copy.deepcopy(asset)
    if change == 'hash': ref['sha256'] = 'bad'
    elif change == 'version': ref['version'] = True
    else:
        m = store.load(ref['id']); store.version(m)['locked'] = False; store.save(m)
    with pytest.raises(ServiceError):
        bindings.snapshot({'asset_refs': [ref]})


def test_export_uses_frozen_inputs_and_detectable_digest(asset, tmp_path):
    run = bound_run(asset)
    run['scenario_snapshot'] = {'id': 'test', 'instance': copy.deepcopy(run['instance'])}
    run['asset_snapshot'] = bindings.snapshot(run['instance'])
    app = create_app(Config(runtime=tmp_path, background=False, archive_autostart=False))
    app.state.lab.store.put_run(run)
    with TestClient(app, base_url='http://127.0.0.1:4100') as c:
        path = '/lab/api/runs/test_field/record'
        a = c.get(path).json()
        assert a['integrity']['sha256'] == bindings.digest(a['record'])
        # Later edits of asset metadata cannot rewrite an existing run's snapshot/export.
        m = store.load(asset['id']); store.version(m)['meta']['planning']['speed_mps'] = 9; store.save(m)
        b = c.get(path).json()
        assert a['record'] == b['record'] and a['integrity'] == b['integrity']
        changed = copy.deepcopy(a['record']); changed['planning']['instance']['depot'][0] += 1
        assert bindings.digest(changed) != a['integrity']['sha256']
        assert c.get('/lab/api/runs/no-such-run/record').status_code == 404


def test_field_api_rejects_bad_asset_before_spawning(asset, tmp_path, monkeypatch):
    run = solution()
    run['instance']['fleet'] = {'asset': {**asset, 'sha256': 'tampered'}}
    app = create_app(Config(runtime=tmp_path, background=False, archive_autostart=False))
    app.state.lab.store.put_run(run)
    with TestClient(app, base_url='http://127.0.0.1:4100') as c:
        response = c.post('/lab/api/fieldsim', json={'run_id': run['run_id']})
        assert response.status_code == 409 and response.json()['error'] == 'asset_version_mismatch'
    assert not (tmp_path / 'fieldsim').exists()


@pytest.mark.parametrize('bad', [{'asset_refs': [None]}, {'asset_refs': {}}, {'asset_refs': None},
                                {'fleet': []}, {'fleet': None}, {'fleet': {'asset': None}}])
def test_malformed_legacy_bindings_fail_with_client_error(asset, bad):
    run = bound_run(asset)
    run['instance'].update(bad)
    for call in (lambda: bindings.snapshot(run['instance']), lambda: bindings.field_vehicle(run)):
        with pytest.raises(ServiceError) as exc:
            call()
        assert exc.value.status == 409


def test_actual_asset_file_tampering_is_rejected(asset):
    m = store.load(asset['id'])
    directory = store.vdir(m['id'], 1)
    directory.mkdir(exist_ok=True)
    path = directory / 'model.sdf'
    path.write_text('<sdf/>')
    store.version(m)['files'].append({'path': 'model.sdf', 'sha256': store.sha256_file(path)})
    store.save(m)
    assert bindings.field_vehicle(bound_run(asset))
    path.write_text('<sdf>changed</sdf>')
    with pytest.raises(ServiceError) as exc:
        bindings.field_vehicle(bound_run(asset))
    assert exc.value.code == 'asset_file_changed'


def test_field_vehicle_rejects_heterogeneous_fleet(asset):
    run = bound_run(asset)
    run['instance']['vehicles'] = {
        'count': 2, 'capacity': 12, 'speed_mps': 1.0, 'trips': 'multi', 'travel': 'free', 'names': ['车1', '车2'],
        'fleet': [{'name': 'A', 'count': 1, 'capacity': 12, 'speed_mps': 1.0, 'robot_radius': 0.49, 'payload': 'load'},
                  {'name': 'B', 'count': 1, 'capacity': 6, 'speed_mps': 1.0, 'robot_radius': 0.49, 'payload': 'load'}]}
    with pytest.raises(ServiceError) as e:
        bindings.field_vehicle(run)
    assert e.value.status == 422 and '异构' in str(e.value)


def test_snapshot_includes_per_class_vehicle_assets(asset):
    run = bound_run(asset)
    run['instance']['vehicles'] = {
        'count': 1, 'capacity': 12, 'speed_mps': 1.0, 'trips': 'multi', 'travel': 'free', 'names': ['车1'],
        'fleet': [{'name': '田间车', 'count': 1, 'capacity': 12, 'speed_mps': 1.0,
                   'robot_radius': 0.49, 'payload': 'load', 'asset': asset}]}
    snap = bindings.snapshot(run['instance'])
    assert any(s['reference'] == bindings.identity(asset) for s in snap)


def test_field_vehicle_rejects_curvature_constrained_class(asset):
    run = bound_run(asset)
    run['instance']['vehicles'] = {
        'count': 1, 'capacity': 12, 'speed_mps': 1.0, 'trips': 'multi', 'travel': 'free', 'names': ['车1'],
        'fleet': [{'name': '阿克曼车', 'count': 1, 'capacity': 12, 'speed_mps': 1.0,
                   'robot_radius': 0.49, 'payload': 'load', 'min_turn_radius_m': 1.5}]}
    with pytest.raises(ServiceError) as e:
        bindings.field_vehicle(run)
    assert e.value.status == 422 and '曲率约束' in str(e.value)
