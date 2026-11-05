"""Geometry and resource witnesses: never execute the million-row counterexample."""
import copy
import math
import sys
from types import SimpleNamespace

import pytest
from shapely.geometry import LineString, Polygon

from urp_lab import coverage_limits
from urp_lab.gateway import ApiError, instance_for
from urp_lab.scenarios import validate_instance
from urp_lab.solvers import coverage, coverage_baseline, f2c_coverage, run_algorithm


def rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def instance(**changes):
    return dict({'field': rect(0, 0, 20, 20), 'obstacles': [], 'headland_width': 1,
                 'swath_width': 1, 'robot_width': .2, 'turn_radius': .3}, **changes)


INVALID_HOLES = [
    [rect(5, 5, 10, 10), rect(8, 8, 13, 13)],  # overlapping holes
    [rect(18, 5, 23, 10)],                      # crosses shell
    [rect(25, 5, 30, 10)],                      # outside shell
    [rect(0, 5, 5, 10)],                        # shared shell edge
    [rect(5, 5, 10, 10), rect(10, 5, 15, 10)], # shared hole edge
    [rect(5, 5, 15, 15), rect(7, 7, 9, 9)],    # nested holes
]


@pytest.mark.parametrize('holes', INVALID_HOLES)
def test_invalid_hole_combination_is_rejected_without_repair(holes):
    inst = instance(obstacles=holes)
    before = copy.deepcopy(inst)
    with pytest.raises(ValueError, match='组合多边形无效'):
        validate_instance('coverage', inst)
    routes, checked, metrics = coverage(inst)  # direct callers are safe too
    assert routes == [] and metrics == {} and not checked['valid']
    assert '组合多边形无效' in checked['errors'][0]
    assert inst == before


@pytest.mark.parametrize('holes', [
    [rect(3, 3, 5, 5), rect(12, 12, 14, 14)],
    [[[0, 10], [2, 8], [4, 10], [2, 12]]],  # OGC-valid point touch
    [rect(3, 3, 5, 5), rect(5, 5, 7, 7)],    # holes touch at one point
])
def test_valid_multiple_holes_and_point_contacts_remain_supported(holes):
    inst = instance(obstacles=holes)
    before = copy.deepcopy(inst)
    result = validate_instance('coverage', inst)
    area, inside = coverage_limits.geometry(result['field'], result['obstacles'], result['headland_width'])
    assert area.is_valid and inside.is_valid and inside.area > 0
    assert inst == before
    assert validate_instance('coverage', result) == result


def test_normal_multi_hole_baseline_retains_coverage_and_safe_connectors():
    inst = validate_instance('coverage', instance(field=rect(0, 0, 30, 20), swath_width=2,
                headland_width=2, robot_width=1, obstacles=[rect(6, 7, 8, 10), rect(19, 12, 21, 14)]))
    routes, checked, metrics = coverage(inst)
    assert checked['valid'], checked['errors']
    line = LineString(routes[0]['polyline'])
    area = Polygon(inst['field'], inst['obstacles'])
    assert area.covers(line.buffer(.5)) and line.distance(area.boundary) >= .5 - 1e-9
    assert metrics['working_area_fraction'] >= .90
    assert metrics['coverage_required_fraction'] == .90
    assert metrics['start_connection_included'] is False


def test_holes_can_empty_the_working_domain_even_when_shell_buffer_survives():
    inst = instance(obstacles=[rect(.1, .1, 19.9, 19.9)])
    assert not Polygon(inst['field']).buffer(-1).is_empty
    with pytest.raises(ValueError, match='内缩作业区为空/无效'):
        validate_instance('coverage', inst)


def test_invalid_geometry_keeps_existing_api_error_contract():
    scenario = {'problem_class': 'coverage', 'frame': {'kind': 'field', 'units': 'm'}}
    with pytest.raises(ApiError) as failure:
        instance_for(scenario, instance(obstacles=INVALID_HOLES[0]))
    assert failure.value.status == 400 and failure.value.code == 'invalid_instance'
    assert '组合多边形无效' in failure.value.message


def test_zero_headland_does_not_call_buffer_zero(monkeypatch):
    original = Polygon.buffer
    def buffer(self, distance, *args, **kwargs):
        assert distance != 0, 'geometry validation must not silently repair input'
        return original(self, distance, *args, **kwargs)
    monkeypatch.setattr(Polygon, 'buffer', buffer)
    assert validate_instance('coverage', instance(headland_width=0))


def test_ten_million_scan_counterexample_is_stopped_before_intersection(monkeypatch):
    inst = instance(field=rect(0, 0, .001, 10_000_000), swath_width=1,
                    headland_width=0, robot_width=.0001)
    assert Polygon(inst['field']).area / inst['swath_width'] ** 2 == 10_000
    # No declared direction: do not reject an instance suitable for native auto
    # angle selection just because the separate panel uses horizontal swaths.
    assert validate_instance('coverage', inst)['angle_deg'] is None
    def unexpected(*args, **kwargs):
        pytest.fail('must not start scanning the counterexample')
    monkeypatch.setattr(Polygon, 'intersection', unexpected)
    routes, checked, metrics = coverage(inst)
    assert not routes and not metrics and not checked['valid']
    assert '扫描行数' in checked['errors'][0]


def test_solver_dispatch_does_not_label_budget_rejection_feasible():
    inst = instance(field=rect(0, 0, .001, 10_000_000), headland_width=0, robot_width=.0001)
    scenario = {'problem_class': 'coverage', 'frame': {'kind': 'field', 'units': 'm'}, 'cost_unit': 'm'}
    result = run_algorithm(scenario, inst, 'panel.boustrophedon', {})
    assert result['status'] == 'no_solution' and result['routes'] == []
    assert result['objective']['value'] is None and not result['check']['valid']


def test_explicit_direction_budget_and_fixed_panel_direction_are_distinct():
    inst = instance(field=rect(0, 0, .001, 10_000_000), headland_width=0, robot_width=.0001)
    with pytest.raises(ValueError, match='扫描行数'):
        validate_instance('coverage', dict(inst, angle_deg=0))
    assert validate_instance('coverage', dict(inst, angle_deg=90))['angle_deg'] == 90
    _, checked, _ = coverage(dict(inst, angle_deg=90))
    assert not checked['valid'] and '扫描行数' in checked['errors'][0]


def test_horizontal_extreme_long_strip_is_not_rejected_by_longest_edge():
    inst = instance(field=rect(0, 0, 10_000_000, .001), headland_width=.0001,
                    swath_width=1, robot_width=.0001)
    normalized = validate_instance('coverage', inst)
    routes, checked, metrics = coverage(normalized)
    assert checked['valid'], checked['errors']
    assert len(routes[0]['polyline']) == 2
    assert metrics['working_area_fraction'] == pytest.approx(1)


@pytest.mark.parametrize('angle, expected', [(0, 2), (90, 100), (180, 2), (270, 100), (450, 100)])
def test_directional_scan_estimate_uses_projected_span(angle, expected):
    assert coverage_limits.scan_budget(Polygon(rect(0, 0, 100, 2)), 1, angle) == expected


def test_rotated_scan_estimate_is_not_axis_aligned_box_diagonal():
    from shapely.affinity import rotate
    field = rotate(Polygon(rect(0, 0, 1000, 2)), 45, origin=(0, 0))
    assert coverage_limits.scan_budget(field, 1, 45) == 2
    assert coverage_limits.scan_budget(field, 1, 135) == 1000


def test_scan_budget_at_limit_and_just_above(monkeypatch):
    monkeypatch.setattr(coverage_limits, 'MAX_SCAN_LINES', 3)
    assert coverage_limits.scan_range(0, 3.5, 1)[1] == 3
    with pytest.raises(ValueError, match='扫描行数'):
        coverage_limits.scan_range(0, 3.50001, 1)


def test_no_progress_due_to_large_coordinate_is_rejected_before_scan():
    inst = instance(field=rect(0, 1e16, 1, 1e16 + 100), headland_width=0)
    with pytest.raises(ValueError, match='浮点步进'):
        validate_instance('coverage', dict(inst, angle_deg=0))
    routes, checked, _ = coverage(inst)
    assert not routes and not checked['valid'] and '浮点步进' in checked['errors'][0]


@pytest.mark.parametrize('swath', [1e-200, 1e-300])
def test_tiny_width_area_budget_is_structured_not_zero_division(swath):
    with pytest.raises(ValueError, match='面积预算'):
        validate_instance('coverage', instance(swath_width=swath))


def test_split_segments_have_independent_budget(monkeypatch):
    monkeypatch.setattr(coverage_limits, 'MAX_SWATH_SEGMENTS', 2)
    inst = instance(field=rect(0, 0, 20, 1), obstacles=[rect(5, .1, 6, .9), rect(12, .1, 13, .9)],
                    headland_width=0, swath_width=1, robot_width=.01)
    assert coverage_limits.scan_budget(Polygon(inst['field'], inst['obstacles']), 1) == 1
    routes, checked, metrics = coverage(inst)
    assert not routes and not metrics and not checked['valid']
    assert '分段数' in checked['errors'][0]


def test_path_point_budget_returns_failure_without_truncating_success(monkeypatch):
    monkeypatch.setattr(coverage_limits, 'MAX_PATH_POINTS', 3)
    routes, checked, metrics = coverage(instance())
    assert not routes and not metrics and not checked['valid']
    assert '路径点数' in checked['errors'][0]


def test_f2c_checker_checks_budget_before_geometry_processing(monkeypatch):
    monkeypatch.setattr(coverage_limits, 'MAX_PATH_POINTS', 2)
    checked, metrics = f2c_coverage.check_path(instance(), [[1, 1], [2, 1], [3, 1]], [])
    assert not checked['valid'] and metrics == {} and '路径点数' in checked['errors'][0]


def test_f2c_straight_sampling_budget_precedes_densify(monkeypatch):
    # Adapter-only mock: this verifies allocation order, not native F2C behavior.
    from urp_lab.scenarios import maps
    point = lambda x, y: SimpleNamespace(getX=lambda: x, getY=lambda: y)
    swath = SimpleNamespace(startPoint=lambda: point(1, 1), endPoint=lambda: point(30_001, 1))
    swaths = SimpleNamespace(size=lambda: 1, at=lambda i: swath)
    native = SimpleNamespace(
        Point=point, LinearRing=lambda p: p, VectorPoint=lambda p: p, Cell=lambda p: p,
        Robot=lambda *a: SimpleNamespace(setMinTurningRadius=lambda radius: None),
        SG_BruteForce=lambda: SimpleNamespace(generateSwaths=lambda *a: swaths),
        RP_Boustrophedon=lambda: SimpleNamespace(genSortedSwaths=lambda s: s),
        PP_DubinsCurves=lambda: SimpleNamespace(setDiscretization=lambda step: None))
    monkeypatch.setitem(sys.modules, 'fields2cover', native)
    def unexpected(*args):
        pytest.fail('oversized straight swath must not allocate dense samples')
    monkeypatch.setattr(maps, 'densify', unexpected)
    with pytest.raises(ValueError, match='路径点数'):
        f2c_coverage.solve(instance(field=rect(0, 0, 30_002, 4)), 'boustrophedon')


def test_connector_graph_budget_precedes_any_dense_matrix_build(monkeypatch):
    from urp_lab.solvers import fleet
    monkeypatch.setattr(coverage_baseline, 'MAX_CONNECTOR_GRAPH_NODES', 2)
    def unexpected(*args, **kwargs):
        pytest.fail('over-budget connectors must not construct the graph')
    monkeypatch.setattr(fleet, 'Graph', unexpected)
    inst = instance(field=rect(0, 0, 30, 20), obstacles=[rect(13, 8, 17, 12)],
                    headland_width=2, swath_width=2, robot_width=1)
    _, checked, metrics = coverage(inst)
    assert not checked['valid'] and any('节点预算' in message for message in checked['errors'])
    assert metrics['connector_graph_node_limit'] == 2


def _fake_full_native(path):
    point = lambda x, y: SimpleNamespace(getX=lambda: x, getY=lambda: y)
    cells = SimpleNamespace(size=lambda: 1, getGeometry=lambda i: None)
    generator_calls = []
    native = SimpleNamespace(
        Point=point, LinearRing=lambda p: p, VectorPoint=lambda p: p,
        Cell=lambda p: SimpleNamespace(addRing=lambda p: None), Cells=lambda p: cells,
        Robot=lambda *a: SimpleNamespace(setMinTurningRadius=lambda r: None, getCovWidth=lambda: 1),
        HG_Const_gen=lambda: SimpleNamespace(generateHeadlands=lambda *a: cells),
        OBJ_NSwathModified=lambda: None,
        SG_BruteForce=lambda: SimpleNamespace(generateBestSwaths=lambda *a: generator_calls.append(a)),
        RP_RoutePlannerBase=lambda: SimpleNamespace(genRoute=lambda *a: None),
        PP_DubinsCurves=lambda: SimpleNamespace(setDiscretization=lambda step: None),
        PP_PathPlanning=lambda: SimpleNamespace(planPath=lambda *a: path))
    return native, generator_calls


def test_full_native_export_budget_includes_endpoint_before_reading_states(monkeypatch):
    def unexpected(*args):
        pytest.fail('oversized native path must not be copied into Python')
    native, calls = _fake_full_native(SimpleNamespace(size=lambda: 3, getState=unexpected))
    monkeypatch.setitem(sys.modules, 'fields2cover', native)
    monkeypatch.setattr(coverage_limits, 'MAX_PATH_POINTS', 3)
    with pytest.raises(ValueError, match='路径点数'):
        f2c_coverage.solve_full(instance(), {'decomposition': 'none'})
    assert len(calls) == 1  # Automatic candidate selection is still delegated unchanged.


def test_full_native_swath_segment_budget_precedes_sweep_union(monkeypatch):
    point = lambda x, y: SimpleNamespace(getX=lambda: x, getY=lambda: y)
    states = [SimpleNamespace(point=point(x, 1), type=1) for x in (1, 2)]
    path = SimpleNamespace(size=lambda: 2, getState=lambda i: states[i], atEnd=lambda: point(3, 1))
    native, _ = _fake_full_native(path)
    monkeypatch.setitem(sys.modules, 'fields2cover', native)
    monkeypatch.setattr(coverage_limits, 'MAX_SWATH_SEGMENTS', 1)
    with pytest.raises(ValueError, match='分段数'):
        f2c_coverage.solve_full(instance(), {'decomposition': 'none'})
