"""ROS final-result time-window semantics, without importing ROS or starting a simulator."""
import ast
import copy
from pathlib import Path

import pytest

from ceres_lab import time_windows


@pytest.fixture(scope='module')
def apply_time_windows():
    source = Path(__file__).resolve().parents[1] / 'ros' / 'field_sim_session.py'
    tree = ast.parse(source.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_apply_time_windows')
    namespace = {'time_windows': time_windows}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_apply_time_windows']


def timed_plan():
    return {'instance': {'tasks': [{'id': 1, 'label': 'T1', 'time_window': [20.0, 50.0]},
                                  {'id': 2, 'label': 'T2', 'time_window': [0.0, 50.0]}]},
            'vehicles': [{'name': 'vehicle_1', 'label': '车 1'}], 'traffic': {'gates': {}}}


def successful_result():
    return {'state': 'succeeded', 'execution_started_sim_s': 100.0,
            'tasks': [{'id': 1, 'vehicle': 'vehicle_1', 't_sim': 110.0, 'service_started_at': 120.0},
                      {'id': 2, 'vehicle': 'vehicle_1', 't_sim': 140.0}],
            'errors': [], 'check': {'valid': True, 'errors': []}}


@pytest.mark.parametrize('violation', ['late', 'missing_task', 'early_service'])
def test_ros_time_windows_failure_overrides_geometric_success(apply_time_windows, violation):
    plan, result = timed_plan(), successful_result()
    if violation == 'late':
        result['tasks'][1]['t_sim'] = 160.0
    elif violation == 'missing_task':
        result['tasks'].pop()
    else:
        result['tasks'][0]['service_started_at'] = 110.0
    original = copy.deepcopy(result)
    assert apply_time_windows(plan, result) is result
    assert result['state'] == 'failed' and result['check']['valid'] is False
    assert result['errors'] and result['check']['errors']
    assert result['time_windows']['total'] == 2
    assert result['time_windows']['early_starts'] == (violation == 'early_service')
    assert result['time_windows']['all_on_time'] is (violation == 'early_service')
    assert result['check']['time_windows'] == time_windows.compact(result['time_windows'])
    assert result['tasks'] == original['tasks'], 'failure classification must retain the actual task evidence'


def test_ros_time_windows_early_arrival_waiting_until_open_stays_successful(apply_time_windows):
    result = successful_result()
    apply_time_windows(timed_plan(), result)
    assert result['state'] == 'succeeded' and result['check']['valid'] is True
    assert result['errors'] == [] and result['check']['errors'] == []
    assert result['time_windows']['all_on_time'] and result['time_windows']['early_starts'] == 0
    assert result['check']['time_windows'] == time_windows.compact(result['time_windows'])


def test_ros_time_windows_does_not_replace_cancellation_with_failure(apply_time_windows):
    result = successful_result()
    result.update(state='canceled', tasks=[], errors=['用户取消'])
    apply_time_windows(timed_plan(), result)
    assert result['state'] == 'canceled' and '用户取消' in result['errors']
    assert result['time_windows']['all_on_time'] is False


def test_ros_time_windows_keeps_existing_failure_and_reason(apply_time_windows):
    result = successful_result()
    result.update(state='failed', errors=['桥接退出'])
    result['check'].update(valid=False, errors=['缺少真值'])
    apply_time_windows(timed_plan(), result)
    assert result['state'] == 'failed' and result['check']['valid'] is False
    assert '桥接退出' in result['errors'] and '缺少真值' in result['check']['errors']


def test_ros_time_windows_without_windows_preserves_existing_result(apply_time_windows):
    plan = timed_plan()
    for task in plan['instance']['tasks']:
        task.pop('time_window')
    result = successful_result()
    original = copy.deepcopy(result)
    apply_time_windows(plan, result)
    assert result.get('time_windows') is None
    assert {key: value for key, value in result.items() if key != 'time_windows'} == original


def test_execution_horizon_without_windows_keeps_the_existing_minimum():
    plan = {'instance': {'tasks': []}, 'vehicles': [{'planned_duration_s': 12, 'tasks': []}]}
    assert time_windows.execution_horizon_s(plan) == 270


@pytest.mark.parametrize('source', ['instance', 'serialized_task', 'both'])
def test_execution_horizon_allows_the_same_long_earliest_time_from_either_source(source):
    plan = {'instance': {'tasks': [{'id': 1}]},
            'vehicles': [{'planned_duration_s': 12, 'tasks': [{'id': 1}]}]}
    if source in ('instance', 'both'):
        plan['instance']['tasks'][0]['time_window'] = [3600, 4000]
    if source in ('serialized_task', 'both'):
        plan['vehicles'][0]['tasks'][0]['earliest_s'] = 3600
    assert time_windows.execution_horizon_s(plan) == 3870


def test_execution_horizon_uses_latest_opening_once_for_the_whole_fleet():
    plan = {'instance': {'tasks': [{'id': 1, 'time_window': [100, 900]},
                                  {'id': 2, 'time_window': [300, 900]}]},
            'vehicles': [{'planned_duration_s': 12, 'tasks': [{'id': 1, 'earliest_s': 100}]},
                         {'planned_duration_s': 12, 'tasks': [{'id': 2, 'earliest_s': 300}]}]}
    assert time_windows.execution_horizon_s(plan) == 570, 'opening times share a clock and must not be added per vehicle or task'


def test_execution_horizon_retains_the_existing_route_budget_before_allowing_waits():
    plan = {'vehicles': [{'planned_duration_s': 80, 'tasks': [{'id': 1, 'earliest_s': 3600}]},
                         {'planned_duration_s': 120, 'tasks': []}]}
    assert time_windows.execution_horizon_s(plan) == 4220
