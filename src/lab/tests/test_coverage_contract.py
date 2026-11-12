import math

import pytest

from ceres_lab import analysis
from ceres_lab.planning import requires_coverage_recheck
from ceres_lab.simplan import NotSimulatable, to_sim_plan
from ceres_lab.solvers import coverage, f2c_coverage


def instance():
    return {'field': [[0, 0], [8, 0], [8, 5], [0, 5]], 'obstacles': [], 'headland_width': 1,
            'robot_width': .35, 'swath_width': .5, 'turn_radius': .3, 'start': [1, 1], 'angle_deg': 0}


def test_short_partial_path_cannot_pass_as_coverage():
    checked, metrics = f2c_coverage.check_path(instance(), [[1, 2], [7, 2]], [[[1, 2], [7, 2]]])
    assert not checked['valid'] and metrics['working_area_fraction'] < .9
    assert any('覆盖率' in error for error in checked['errors'])


def test_working_swaths_must_belong_to_the_exported_path():
    checked, _ = f2c_coverage.check_path(instance(), [[1, 2], [7, 2]], [[[1, 3], [7, 3]]])
    assert not checked['valid'] and any('导出路径' in error for error in checked['errors'])


def test_unverified_reversal_is_not_a_straight_forward_curve():
    checked, _ = f2c_coverage.check_path(instance(), [[1, 2], [7, 2], [1, 2]], [[[1, 2], [7, 2]]])
    assert not checked['valid'] and any('折返' in error for error in checked['errors'])


def test_panel_baseline_cannot_win_by_omitting_working_area():
    inst = {**instance(), 'field': [[0, 0], [8, 0], [8, 5.5], [0, 5.5]],
            'headland_width': .3, 'swath_width': 4}
    _, checked, metrics = coverage(inst)
    assert not checked['valid'] and metrics['working_area_fraction'] < .9


@pytest.mark.skipif(not f2c_coverage.available()['available'], reason='optional pinned Fields2Cover unavailable')
def test_native_full_pipeline_includes_the_last_state_endpoint():
    routes, checked, metrics, raw = f2c_coverage.solve_full(instance(), {})
    assert checked['valid'], checked['errors']
    assert raw['adapter_contract'] == 'urp.f2c-full/2' and raw['endpoint_included']
    assert raw['last_state_length_m'] > 1  # The omitted final state was an entire swath.
    assert raw['decimation'] == 1
    assert metrics['working_area_fraction'] > .99 and metrics['turn_radius_enforced']
    length = sum(math.dist(a, b) for a, b in zip(routes[0]['polyline'], routes[0]['polyline'][1:]))
    assert length == pytest.approx(raw['native_path_length_m'], abs=.01)
    assert checked['computed_cost'] == pytest.approx(length)


def test_legacy_full_coverage_is_retained_but_excluded_from_execution_and_ranking():
    old = {'algorithm': {'id': 'f2c.full'}, 'frame': {'kind': 'map', 'units': 'm'},
           'state': 'done', 'status': 'feasible', 'objective': {'value': 1},
           'check': {'valid': True}, 'map_check': {'collision_free': True},
           'instance': {'robot_width': .35}, 'routes': [{}]}
    assert requires_coverage_recheck(old) and analysis._value(old) is None
    with pytest.raises(NotSimulatable, match='重新求解'):
        to_sim_plan(old, {'problem_class': 'coverage'}, 'follow_path')
    reverse = {**old, 'raw_result': {'adapter_contract': 'urp.f2c-full/2'}, 'metrics': {'reverse_states': 1}}
    assert not requires_coverage_recheck(reverse)
    with pytest.raises(NotSimulatable, match='倒车'):
        to_sim_plan(reverse, {'problem_class': 'coverage'}, 'follow_path')
