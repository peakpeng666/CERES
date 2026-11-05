"""Fleet service rows are opt-in, never a silent geometry CSV schema change."""
import copy
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient
from urp_lab import gateway
from urp_lab.export import export_run


def fleet():
    return dict(run_id='original-run', instance_sha256='original-input-hash',
                problem_class='fleet_routing', frame={'units': 'm'},
                instance={'tasks': [{'id': i, 'label': f'T{i}', 'xy': [i, i], 'demand': i} for i in (1, 2, 3)]},
                routes=[{'vehicle': 0, 'vehicle_name': 'A', 'sequence': [1, 2], 'trips': [[1], [2]],
                         'polyline': [[0, 0], [1, 1], [0, 0], [2, 2], [0, 0]]},
                        {'vehicle': 1, 'vehicle_name': 'B', 'sequence': [3], 'trips': [[3]],
                         'polyline': [[0, 0], [3, 3], [0, 0]]}])


def rows(data):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))


def test_geometry_compatible_and_tasks_reconcile_json_without_depot_rows():
    original = fleet()
    before = copy.deepcopy(original)
    geometry = rows(export_run(original, 'csv')[0])
    assert list(geometry[0]) == ['run_id', 'vehicle', 'point', 'x', 'y', 'units', 'instance_sha256']
    assert len(geometry) == 8
    data, mime, name = export_run(original, 'tasks-csv')
    services = rows(data)
    assert mime == 'text/csv' and name == 'original-run-tasks.csv'
    exported = json.loads(export_run(original, 'json')[0])
    expected = [(str(r['vehicle']), str(k+1), str(i+1), str(tid))
                for r in exported['routes'] for k, trip in enumerate(r['trips']) for i, tid in enumerate(trip)]
    assert [(r['vehicle'], r['trip'], r['order'], r['task']) for r in services] == expected
    assert all(r['run_id'] == original['run_id'] and r['instance_sha256'] == original['instance_sha256']
               and r['units'] == 'm' and r['row_kind'] == 'task_service' for r in services)
    assert original == before


def test_tasks_are_fleet_only_and_invalidated_routes_remain_empty():
    record = fleet()
    record['problem_class'] = 'euclidean_cvrp'
    assert len(rows(export_run(record, 'csv')[0])) == 8
    with pytest.raises(ValueError, match='仅适用于'):
        export_run(record, 'tasks-csv')
    record = fleet()
    record['process'] = {'source_identity_verified': False}
    assert rows(export_run(record, 'tasks-csv')[0]) == []
    assert rows(export_run(record, 'csv')[0]) == []


def test_export_endpoint_reaches_task_format(tmp_path):
    app = gateway.create_app(gateway.Config(runtime=tmp_path, background=False, extra_hosts={'testserver'}))
    with TestClient(app) as client:
        app.state.lab.store.put_run(fleet())
        response = client.get('/lab/api/runs/original-run/export?format=tasks-csv')
        assert response.status_code == 200
        assert len(rows(response.content)) == 3
        assert 'original-run-tasks.csv' in response.headers['content-disposition']
