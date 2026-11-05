import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from urp_lab import gateway, lanes
from urp_lab.compose import EXAMPLES, compose
from urp_lab.compose.geo import export_geojson, import_geojson


def test_partial_generation_reports_actual_counts_in_cards_and_summary():
    scenario = compose(EXAMPLES[2])  # reproducibly cannot place its default building
    report = scenario['compose']['generation']
    assert report['status'] == 'partial'
    assert report['requested']['obstacles']['building'] == 1
    assert report['actual']['obstacles'].get('building', 0) == 0
    assert report['shortfalls'] and '未完全生成' in scenario['summary']
    obstacle_card = next(c['value'] for c in scenario['compose']['cards'] if c['key'] == 'obstacles')
    assert '建筑' not in obstacle_card
    assert scenario['provenance']['params']['spec']['obstacles']['building'] == 1


def test_task_placement_shortfall_is_explicit(monkeypatch):
    from urp_lab.compose import generate
    original = generate._tasks
    monkeypatch.setattr(generate, '_tasks', lambda *args: original(*args)[:1])
    scenario = compose(EXAMPLES[0], seed=7)
    report = scenario['compose']['generation']
    assert report['actual']['tasks'] == 1 and report['requested']['tasks'] == 40
    assert report['status'] == 'partial' and '未完全生成' in scenario['summary']
    assert next(c['value'] for c in scenario['compose']['cards'] if c['key'] == 'tasks').startswith('1 个')


def corridor_geojson(properties):
    obj = json.loads(export_geojson(compose(EXAMPLES[0], seed=7)))
    obj['features'].append({'type': 'Feature', 'properties': {'role': 'corridor', **properties},
        'geometry': {'type': 'LineString', 'coordinates': [[5, 5], [150, 5]]}})
    return obj


@pytest.mark.parametrize('width', [None, False, 'bad', 0, .9, 8.1, float('nan'), float('inf')])
def test_explicit_invalid_corridor_width_is_not_replaced(width):
    with pytest.raises(ValueError, match='width'):
        import_geojson(corridor_geojson({'width_m': width}))


@pytest.mark.parametrize('properties,expected', [({}, 2.5), ({'width': 3}, 3), ({'width_m': 4}, 4)])
def test_missing_and_valid_corridor_width(properties, expected):
    assert import_geojson(corridor_geojson(properties))['instance']['corridors'][0]['width_m'] == expected


@pytest.mark.parametrize('failure,status', [(ValueError('bad geometry'), 400), (RuntimeError('implementation bug'), 500)])
def test_lane_preview_distinguishes_input_and_internal_errors(tmp_path, monkeypatch, failure, status):
    monkeypatch.setattr(lanes, 'preview', Mock(side_effect=failure))
    app = gateway.create_app(gateway.Config(runtime=tmp_path, background=False, extra_hosts={'testserver'}))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/lab/api/fleet/lanes', json={'instance': {}})
    assert response.status_code == status


def test_unreadable_makespan_trace_keeps_valid_incumbent(monkeypatch, caplog):
    from urp_lab.solvers import fleet_engines, run_algorithm
    from test_integration_regressions import SC, instance
    monkeypatch.setattr(fleet_engines, '_makespan_sample', Mock(side_effect=RuntimeError('unreadable cumul')))
    result = run_algorithm(SC, instance(supply=False), 'ortools.fleet', {'time_limit_s': .1})
    assert result['status'] == 'feasible' and result['check']['valid']
    assert result['trace'] == []
    diagnostics = result['raw_result']['trace_diagnostics']
    assert diagnostics['samples_read'] == 0 and diagnostics['samples_omitted'] > 0
    assert diagnostics['first_error'] == 'RuntimeError: unreadable cumul'
    assert 'unreadable cumul' in caplog.text


def test_complete_generation_retains_requested_counts():
    scenario = compose(EXAMPLES[0], seed=7)
    report = scenario['compose']['generation']
    assert report['status'] == 'complete' and not report['shortfalls']
    assert report['actual'] == report['requested']
    assert '未完全生成' not in scenario['summary']
