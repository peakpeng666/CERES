import copy

import pytest
from fastapi.testclient import TestClient

from urp_lab import analysis, paths
from urp_lab.gateway import Config, create_app
from urp_lab.planning import comparison_key, context, fingerprint, trace_context, validate_frame, without_stale_reference
from urp_lab.scenarios import ScenarioConflict, get_scenario, save_user_scenario


def cvrp():
    return {'coordinates': [[0, 0], [3, 4]], 'demands': [0, 1], 'capacity': 2,
            'objective': 'sum_unrounded_euclidean_distance'}


def scenario():
    return {'id': 'planning-test', 'problem_class': 'euclidean_cvrp', 'instance': cvrp(),
            'frame': {'kind': 'benchmark', 'units': 'unitless'}, 'cost_unit': 'euclidean'}


def run():
    sc = scenario()
    return {'run_id': 'r1', 'scenario_id': sc['id'], 'problem_class': sc['problem_class'],
            'instance': sc['instance'], 'instance_sha256': fingerprint(sc['instance']), 'scenario_snapshot': sc,
            'frame': sc['frame'], 'state': 'done', 'status': 'feasible', 'check': {'valid': True},
            'algorithm': {'id': 'test', 'version': '1'}, 'params': {'time_limit_s': 1},
            'objective': {'value': 10, 'unit': 'euclidean'}, 'metrics': {'wall_s': 0.1}}


@pytest.mark.parametrize('change', ['coordinates', 'rule', 'frame', 'unit'])
def test_comparison_separates_problem_semantics(change):
    a, b = run(), run()
    if change == 'coordinates':
        b['instance']['coordinates'][1][0] = 4
    elif change == 'rule':
        b['instance']['objective'] = 'sum_rounded_euclidean_distance'
    elif change == 'frame':
        b['frame'] = {'kind': 'field', 'units': 'm', 'crs': 'local'}
    else:
        b['objective']['unit'] = 'm'
    assert comparison_key(a) != comparison_key(b)


def test_coverage_relaxation_is_not_the_same_comparison_model():
    sc = {'problem_class': 'coverage', 'frame': {'kind': 'field', 'units': 'm'}, 'cost_unit': 'm'}
    inst = {'turn_radius': 1}
    assert context(sc, inst, {'metrics': {'turn_radius_enforced': True}})['comparison_key'] != context(
        sc, inst, {'metrics': {'turn_radius_enforced': False}})['comparison_key']


def test_edited_instance_does_not_inherit_bks():
    sc = scenario()
    sc['reference'] = {'bks': 10}
    original = without_stale_reference(sc, sc['instance'])
    assert original['reference']['instance_sha256'] == fingerprint(sc['instance'])
    edited = copy.deepcopy(sc['instance'])
    edited['coordinates'][1][0] = 30
    changed = without_stale_reference(original, edited)
    assert 'reference' not in changed and sc['reference'] == {'bks': 10}
    assert changed['provenance']['reference_invalidated']['original']['bks'] == 10


def test_analysis_keeps_versions_parameters_failures_and_rejects_proxy_traces():
    a = run()
    b = copy.deepcopy(a)
    b.update(run_id='r2', algorithm={'id': 'test', 'version': '2'})
    c = copy.deepcopy(a)
    c.update(run_id='r3', state='timeout')
    d = copy.deepcopy(a)
    d.update(run_id='r4', params={'time_limit_s': 1, 'grid_cell_m': 0.1})
    result = analysis.analyze([a, b, c, d])
    assert len(result['algorithms']) == 3
    assert sum(s['runs'] for s in result['summary']) == 4
    assert sum(s['successes'] for s in result['summary']) == 3
    a['trace'] = [[0, 12], [1, 10]]
    assert not analysis.trace_matches(a)  # an equal endpoint does not prove semantics
    a['trace_contract'] = trace_context('turn_routing', {})
    assert not analysis.trace_matches(a)
    a['trace_contract'] = trace_context('euclidean_cvrp', {'objective': 'sum_rounded_euclidean_distance'})
    assert analysis.trace_matches(a)


def test_scenario_revision_prevents_lost_updates_and_stale_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    sc = scenario()
    sc['reference'] = {'bks': 10}
    first = save_user_scenario(sc, overwrite=False)
    changed = copy.deepcopy(first)
    changed['instance']['capacity'] = 3
    second = save_user_scenario(changed, expected_revision=first['revision'])
    assert second['revision'] == first['revision'] + 1 and 'reference' not in second
    with pytest.raises(ScenarioConflict):
        save_user_scenario(first, expected_revision=first['revision'])
    assert get_scenario(sc['id'])['instance']['capacity'] == 3


def test_validation_and_revision_api(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    stored = save_user_scenario(scenario())
    with TestClient(create_app(Config(port=4199, runtime=tmp_path, background=False)), base_url='http://127.0.0.1:4199') as client:
        url = '/lab/api/scenarios/' + stored['id']
        checked = client.post('/lab/api/planning/validate', json={'scenario_id': stored['id'], 'instance': cvrp()})
        assert checked.status_code == 200 and checked.json()['planning']['service_kind'] == 'point_visit'
        invalid = cvrp()
        invalid['demands'] = [0, -1]
        assert client.post('/lab/api/planning/validate', json={'scenario_id': stored['id'], 'instance': invalid}).status_code == 400
        assert client.put(url, json={'title': 'newer', 'base_revision': stored['revision']}).status_code == 200
        stale = client.put(url, json={'title': 'overwrite', 'base_revision': stored['revision']})
        assert stale.status_code == 409 and stale.json()['error'] == 'scenario_conflict'
        assert client.get(url).json()['title'] == 'newer'


def test_abstract_cvrp_fleet_is_not_an_asset_binding(tmp_path, monkeypatch):
    import time
    from urp_lab.assets import bindings
    from urp_lab.services import ServiceError
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    stored = save_user_scenario(scenario())
    abstract = {**cvrp(), 'fleet': 'unlimited_identical_vehicles'}
    assert bindings.snapshot(abstract, problem_class='euclidean_cvrp') == []
    with pytest.raises(ServiceError, match='fleet'):
        bindings.snapshot(abstract, problem_class='fleet_routing')
    with pytest.raises(ServiceError):
        bindings.snapshot({**abstract, 'asset_refs': [{'id': 'forged'}]}, problem_class='euclidean_cvrp')
    with TestClient(create_app(Config(port=4199, runtime=tmp_path, background=False)), base_url='http://127.0.0.1:4199') as client:
        response = client.post('/lab/api/solve', json={'scenario_id': stored['id'], 'instance': abstract,
            'algorithms': [{'id': 'urp.js.savings', 'params': {}}]})
        assert response.status_code == 202, response.text
        rid = response.json()['runs'][0]
        end = time.monotonic() + 10
        while time.monotonic() < end:
            result = client.get('/lab/api/runs/' + rid).json()
            if result['state'] not in ('queued', 'running'):
                break
            time.sleep(.05)
        assert result['state'] == 'done' and result['check']['valid'], result


@pytest.mark.parametrize('problem', ['fleet_routing', 'coverage', 'point_to_point'])
def test_dimensionless_coordinates_are_not_silently_metres(problem):
    with pytest.raises(ValueError, match='米'):
        validate_frame(problem, {'kind': 'benchmark', 'units': 'unitless'})


def test_tb4_admission_preserves_motion_model_scope():
    from urp_lab.simplan import to_sim_plan, NotSimulatable
    sc = {'problem_class': 'point_to_point', 'instance': {'motion_model': 'dubins'}}
    r = {'frame': {'kind': 'map', 'units': 'm'}, 'check': {'valid': True},
         'map_check': {'collision_free': True}, 'routes': [{}], 'instance': sc['instance']}
    with pytest.raises(NotSimulatable, match='Dubins'):
        to_sim_plan(r, sc, 'follow_path')


def test_bool_is_not_a_numeric_solver_parameter():
    from urp_lab.jobs import resolve_params
    with pytest.raises(ValueError, match='布尔'):
        resolve_params({'id': 'test', 'params': [{'name': 'seed', 'type': 'integer'}]}, {'seed': True})


def test_frozen_map_identity_does_not_use_todays_installed_map(monkeypatch):
    r = run()
    r['frame'] = {'kind': 'map', 'units': 'm', 'world': 'maze'}
    from urp_lab.scenarios import maps
    monkeypatch.setattr(maps, 'fingerprint', lambda _: 'old-map')
    r['planning'] = context({**r['scenario_snapshot'], 'frame': r['frame']}, r['instance'], r)
    frozen = comparison_key(r)
    monkeypatch.setattr(maps, 'fingerprint', lambda _: 'new-map')
    assert comparison_key(r) == frozen
    assert context({**r['scenario_snapshot'], 'frame': r['frame']}, r['instance'], r)['comparison_key'] != frozen


@pytest.mark.parametrize('key', ['time_windows', 'line_tasks', 'energy_constraints', 'resource_capacities'])
def test_unimplemented_constraints_are_not_silently_ignored(key):
    from urp_lab.scenarios import validate_instance
    instance = cvrp()
    instance[key] = [{'required': True}]
    with pytest.raises(ValueError, match='尚未实现'):
        validate_instance('euclidean_cvrp', instance)
    assert instance[key] == [{'required': True}]


def test_seed_roles_and_frozen_override_input():
    from urp_lab.jobs import skeleton
    from urp_lab.solvers import catalog
    algorithms = {a['id']: a for a in catalog()}
    assert algorithms['pyvrp.ils']['randomness']['uses_seed']
    assert not algorithms['ortools.gls']['randomness']['uses_seed']
    assert not algorithms['urp.turn_mode_dp']['randomness']['uses_seed']
    sc = get_scenario('bench-cvrp-uniform-n20')
    edited = copy.deepcopy(sc['instance'])
    edited['capacity'] += 1
    r = skeleton('job', sc, edited, algorithms['pyvrp.ils'], {}, None)
    assert r['instance'] == r['scenario_snapshot']['instance'] == edited
    assert r['scenario_snapshot']['source_instance_sha256'] != r['instance_sha256']
    assert sc['instance']['capacity'] + 1 == edited['capacity']


def test_point_goal_tolerance_is_kept_and_independently_checked(monkeypatch):
    from urp_lab.simplan import to_sim_plan
    from urp_lab import execution_check
    inst = {'motion_model': 'holonomic', 'goal': [1, 0, 0], 'goal_tolerance': .01}
    r = {'frame': {'kind': 'map', 'units': 'm'}, 'check': {'valid': True}, 'map_check': {'collision_free': True},
         'instance': inst, 'algorithm': {'origin': 'test'}, 'routes': [{'polyline': [[0, 0], [1, 0]]}]}
    plan = to_sim_plan(r, {'problem_class': 'point_to_point', 'instance': {'goal_tolerance': .35}}, 'follow_path')
    assert plan['goal_tolerance_m'] == plan['task_radius_m'] == .01
    monkeypatch.setattr(execution_check, 'map_check', lambda *_: {'collision_free': True})
    execution = {'tracking_trajectory': [[0, 0, 0, 0], [1, .98, 0, 0]]}
    checked = execution_check.check(plan, execution, 'test')
    assert not checked['goal_reached_within_tolerance']
    assert checked['independent_goal_error_m'] == pytest.approx(.02)
    plan['goal_tolerance_m'] = .03
    assert execution_check.check(plan, execution, 'test')['goal_reached_within_tolerance']
