"""Cloud integration regressions: prediction scope, comparable ranking and frozen replay labels."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from urp_lab import field_coordination as fc, fieldworld, physics
from urp_lab.agent import runner, tools


def test_explicit_fleet_parameters_are_not_silently_ignored(monkeypatch):
    inst = {'vehicles': {'speed_mps': 1, 'fleet': [{'speed_mps': .2, 'robot_radius': 2.0}]}, 'robot_radius': 2.0}
    monkeypatch.setattr(fieldworld, 'plan_vehicles', lambda *_: pytest.fail('unsupported model must not be compiled'))
    found = fc.estimate(inst, [{'polyline': [[0, 0], [1, 0]]}])
    assert found['status'] == 'unsupported' and found['reason'] and 'predicted_makespan_s' not in found


@pytest.mark.parametrize('change', [
    {'ground': {'zones': []}}, {'robot_radius': .1}, {'vehicles': {'speed_mps': 3.0}},
])
def test_estimate_rejects_unmodelled_execution_scope(change):
    inst = {'vehicles': {'speed_mps': 1}, 'robot_radius': 1}
    inst.update(change)
    assert fc.estimate_scope(inst, [{}])


def choose(monkeypatch, predictions, models=None, statuses=None):
    runs = {}
    for i, value in enumerate((100, 110)):
        metadata = {'status': (statuses or ['estimated'] * 2)[i], 'model': (models or [fc.ESTIMATE_MODEL] * 2)[i]}
        runs[str(i)] = {'objective': {'value': value, 'unit': 's'}, 'algorithm': {'name': str(i), 'id': str(i)},
                        'metrics': {'makespan_s': value, 'predicted_makespan_s': predictions[i], 'execution_estimate': metadata}}
    monkeypatch.setattr(tools, 'current_run_ids', lambda _: list(runs))
    monkeypatch.setattr(tools, 'run_matches_current', lambda *_: True)
    fake = SimpleNamespace(lab=SimpleNamespace(store=SimpleNamespace(get_run=runs.get)))
    session = {'verifications': {rid: {'valid': True, 'agrees': True} for rid in runs}}
    return runner.AgentRunner._best_verified(fake, session)


@pytest.mark.parametrize('predictions,models,statuses', [
    ([140, None], None, None), ([140, 120], [fc.ESTIMATE_MODEL, 'old-model'], None),
    ([140, 120], None, ['estimated', 'unavailable']), ([140, float('nan')], None, None),
    ([140, 0], None, None), ([140, 120], [None, None], None),
])
def test_incomparable_estimates_use_one_shared_objective_basis(monkeypatch, predictions, models, statuses):
    best = choose(monkeypatch, predictions, models, statuses)
    assert best['run_id'] == '0' and best['ranking_basis'] == 'objective'


def test_same_model_complete_estimates_can_rank_on_prediction(monkeypatch):
    best = choose(monkeypatch, [140, 120])
    assert best['run_id'] == '1' and best['ranking_basis'] == 'predicted_makespan_s'


def test_frozen_coordinated_replay_is_not_labelled_serial(monkeypatch, tmp_path):
    plan = {'run_id': 'run_1', 'traffic': {'policy': fc.POLICY}}
    emitted = []
    manager = SimpleNamespace(_dir=lambda _: tmp_path / 'record', _gazebo=lambda _: (deepcopy(plan), {}),
                              _emit=emitted.append, _run=lambda *_: None)
    monkeypatch.setattr(physics, 'available', lambda: {'available': True})
    monkeypatch.setattr(physics, 'supports', lambda _: None)
    monkeypatch.setattr(physics.threading, 'Thread', lambda **_: SimpleNamespace(start=lambda: None))
    rec = physics.PhysicsManager.submit(manager, session_id='field_1')
    assert rec['traffic_mode'] == 'coordinated'


@pytest.mark.parametrize('second,comparable', [
    ({'predicted_makespan_s': 90, 'execution_estimate': {'status': 'estimated', 'model': fc.ESTIMATE_MODEL}}, True),
    ({'predicted_makespan_s': 90, 'execution_estimate': {'status': 'estimated', 'model': 'old-model'}}, False),
    ({'predicted_makespan_s': None, 'execution_estimate': {'status': 'unavailable', 'reason': 'budget'}}, False),
    ({'predicted_makespan_s': 90, 'execution_estimate': {'status': 'unsupported', 'model': fc.ESTIMATE_MODEL}}, False),
    ({'predicted_makespan_s': 90, 'execution_estimate': {'status': 'estimated', 'model': ''}}, False),
    ({'predicted_makespan_s': 90, 'execution_estimate': {'status': 'estimated', 'model': {'bad': True}}}, False),
    ({'predicted_makespan_s': float('nan'), 'execution_estimate': {'status': 'estimated', 'model': fc.ESTIMATE_MODEL}}, False),
    ({'predicted_makespan_s': float('inf'), 'execution_estimate': {'status': 'estimated', 'model': fc.ESTIMATE_MODEL}}, False),
    ({'predicted_makespan_s': 0, 'execution_estimate': {'status': 'estimated', 'model': fc.ESTIMATE_MODEL}}, False),
    ({'predicted_makespan_s': True, 'execution_estimate': {'status': 'estimated', 'model': fc.ESTIMATE_MODEL}}, False),
])
def test_compare_tool_never_marks_incomparable_predictions_best(monkeypatch, second, comparable):
    from urp_lab import planning
    first = {'run_id': 'a', 'state': 'done', 'status': 'feasible', 'check_valid': True,
             'total_distance_m': 10, 'makespan_s': 100, 'wall_s': 1,
             'predicted_makespan_s': 140, 'execution_estimate': {'status': 'estimated', 'model': fc.ESTIMATE_MODEL}}
    other = {**first, 'run_id': 'b', 'total_distance_m': 20, 'makespan_s': 110, **second}
    runs = {'a': first, 'b': other}
    monkeypatch.setattr(tools, '_run', lambda _, rid: runs[rid])
    monkeypatch.setattr(tools, 'run_summary', lambda run, *_: run)
    monkeypatch.setattr(planning, 'comparison_key', lambda _: 'one-input')
    group = tools.compare_runs(SimpleNamespace(session={}), {'run_ids': ['a', 'b']})['groups'][0]
    assert group['best']['makespan_s'] == group['best']['total_distance_m'] == 'a'
    assert group['prediction_comparable'] is comparable
    if comparable:
        assert group['best']['predicted_makespan_s'] == 'b'
    else:
        assert 'predicted_makespan_s' not in group['best']
