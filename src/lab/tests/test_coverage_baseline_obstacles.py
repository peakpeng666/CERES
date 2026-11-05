"""Regression witnesses for safe polyline coverage connectors, not curvature planning."""
import copy
import math
import pytest
from shapely.geometry import LineString, Polygon
from urp_lab.solvers import coverage
from urp_lab.solvers import coverage_baseline


def instance(**changes):
    return dict({'field': [[0, 0], [30, 0], [30, 20], [0, 20]],
                 'obstacles': [[[13, 8], [17, 8], [17, 12], [13, 12]]],
                 'headland_width': 2, 'swath_width': 2, 'robot_width': 1,
                 'turn_radius': 5, 'start': [1, 1]}, **changes)


def independently_check(inst, routes, check, metrics):
    assert check['valid'], check['errors']
    points = routes[0]['polyline']
    assert metrics['start_connection_included'] is False
    area = Polygon(inst['field'], inst.get('obstacles', []))
    path = LineString(points)
    assert area.covers(path.buffer(inst['robot_width'] / 2))
    # Independent exact-distance corroboration avoids relying only on round-buffer discretisation.
    assert path.distance(Polygon(inst['field']).boundary) >= inst['robot_width'] / 2 - 1e-9
    for hole in inst.get('obstacles', []):
        assert path.distance(Polygon(hole)) >= inst['robot_width'] / 2 - 1e-9
    length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    assert check['computed_cost'] == pytest.approx(length)
    assert routes[0]['cost'] == pytest.approx(length)
    assert metrics['working_area_fraction'] >= .90
    assert metrics['turn_radius_enforced'] is False


def test_hole_split_swaths_get_safe_connectors_without_altering_input():
    inst = instance(); original = copy.deepcopy(inst)
    routes, check, metrics = coverage(inst)
    independently_check(inst, routes, check, metrics)
    assert metrics['connector_detours'] == 5
    assert metrics['working_area_fraction'] == pytest.approx(.9943219701638527)
    assert inst == original


def test_no_obstacle_swaths_keep_existing_geometry_and_separate_approach_scope():
    inst = instance(obstacles=[])
    routes, check, metrics = coverage(inst)
    independently_check(inst, routes, check, metrics)
    assert routes[0]['polyline'][:2] == [[2, 3], [28, 3]]
    assert metrics['connector_detours'] == 0
    assert metrics['start_connection_included'] is False


@pytest.mark.parametrize('start', [[25, 10], [15, 10], [0, 0], [-1, 1], None])
def test_approach_pose_does_not_silently_become_a_field_constraint(start):
    inst = instance(start=start)
    if start is None:
        del inst['start']
    routes, check, metrics = coverage(inst)
    baseline, _, _ = coverage(instance())
    assert check['valid']
    assert routes[0]['polyline'] == baseline[0]['polyline']
    assert metrics['start_connection_included'] is False


def test_true_footprint_disconnection_remains_a_failure():
    # Two rooms joined by a .6 m corridor: centreline-connected, but a 1 m body cannot pass.
    field = [[0, 0], [10, 0], [10, 4.7], [20, 4.7], [20, 0], [30, 0],
             [30, 10], [20, 10], [20, 5.3], [10, 5.3], [10, 10], [0, 10]]
    _, check, _ = coverage(instance(field=field, obstacles=[], headland_width=1))
    assert not check['valid']
    assert any('没有找到' in e for e in check['errors'])


def test_obstacle_near_boundary_uses_available_side_not_subfootprint_gap():
    inst = instance(obstacles=[[[.2, 8], [14, 8], [14, 12], [.2, 12]]])
    routes, check, metrics = coverage(inst)
    independently_check(inst, routes, check, metrics)


def test_unsafe_work_swaths_are_not_replaced_then_falsely_credited():
    _, check, _ = coverage(instance(obstacles=[], headland_width=.1, robot_width=2))
    assert not check['valid']
    assert any('足迹' in e for e in check['errors'])


def test_connector_budget_failure_is_explicit_not_a_truncated_success(monkeypatch):
    monkeypatch.setattr(coverage_baseline, 'MAX_CONNECTOR_GRAPH_NODES', 2)
    _, check, _ = coverage(instance())
    assert not check['valid'] and any('节点预算' in e for e in check['errors'])


def test_catalog_hash_tracks_actual_coverage_implementation():
    import hashlib
    from pathlib import Path
    from urp_lab.solvers import catalog
    alg = next(a for a in catalog() if a['id'] == 'panel.boustrophedon')
    assert alg['origin'].endswith('/coverage_baseline.py')
    assert alg['version'] == hashlib.sha256(Path(coverage_baseline.__file__).read_bytes()).hexdigest()
    assert alg['guarantee'] == 'none'


def test_inscribed_round_buffer_cannot_hide_a_grazing_corner_collision():
    # A tiny obstacle lies outside a chord of Shapely's 64-sided radius-1 disk,
    # but inside the true footprint. The old buffer-only predicate accepts it.
    angle = math.pi / 2 - math.pi / 64
    x, y = .9995 * math.cos(angle), .9995 * math.sin(angle)
    eps = 1e-5
    hole = [[x-eps, y-eps], [x+eps, y-eps], [x+eps, y+eps], [x-eps, y+eps]]
    area = Polygon([[-5, -5], [5, -5], [5, 5], [-5, 5]], [hole])
    path = LineString([[0, -1], [0, 0]])
    assert area.covers(path.buffer(1)), 'counterexample must pass the old approximation'
    assert path.distance(area.boundary) < 1
    assert not coverage_baseline.footprint_valid(area, path, 1)
