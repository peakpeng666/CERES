"""Original-polygon clearance regression, independent of fleet.check/buffer arcs."""
import math

import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon

from urp_lab.solvers.fleet import Graph


def instance(a, b, *, obstacles=None, boundary=None, radius=1.):
    return {'boundary': boundary or [[-5,-5],[5,-5],[5,5],[-5,5]],
            'obstacles': [{'polygon': p} for p in (obstacles or [])],
            'robot_radius': radius, 'depot': a,
            'tasks': [{'id': 1, 'xy': b}], 'plots': []}


def assert_clear(inst, path):
    geom = Point(path[0]) if all(p == path[0] for p in path) else LineString(path)
    boundary = Polygon(inst['boundary'])
    assert boundary.covers(geom)
    assert geom.distance(boundary.boundary) >= inst['robot_radius'] - 1e-6
    for obstacle in inst['obstacles']:
        assert geom.distance(Polygon(obstacle['polygon'])) >= inst['robot_radius'] - 1e-6


def test_inscribed_buffer_shortcut_is_filtered_then_safe_detour_is_found():
    a = [-1.0001, 0.]
    b = [-1.0001 * math.cos(math.pi / 8), -1.0001 * math.sin(math.pi / 8)]
    inst = instance(a, b, obstacles=[[[0,0],[1,0],[1,1],[0,1]]])
    graph = Graph(inst)
    shortcut = LineString([a, b])
    # This reproduces the old edge predicate; old Graph necessarily selected
    # the direct Euclidean edge even though the actual robot disc clips a corner.
    assert graph.free.buffer(1e-6).covers(shortcut)
    assert shortcut.distance(Polygon(inst['obstacles'][0]['polygon'])) == pytest.approx(.9808833589312708)
    path = graph.path(0, 1)
    assert len(path) > 2
    assert graph.D[0, 1] > shortcut.length
    assert_clear(inst, path)


def test_open_convex_map_keeps_direct_distance():
    inst = instance([-3., -3.], [3., 3.])
    graph = Graph(inst)
    assert graph.path(0, 1) == [inst['depot'], inst['tasks'][0]['xy']]
    assert graph.D[0, 1] == pytest.approx(math.sqrt(72))
    assert_clear(inst, graph.path(0, 1))


@pytest.mark.parametrize('obstacles', [[], [[[0,0],[1,0],[1,1],[0,1]]]])
def test_colocated_service_positions_remain_zero_edges(obstacles):
    inst = instance([-3., -3.], [-3., -3.], obstacles=obstacles)
    graph = Graph(inst)
    assert graph.D[0, 1] == 0
    assert graph.path(0, 1) == [[-3., -3.], [-3., -3.]]
    assert_clear(inst, graph.path(0, 1))


def test_concave_boundary_routes_around_notch_with_body_clearance():
    inst = instance([1., 8.], [8., 1.], radius=.5,
                    boundary=[[0,0],[10,0],[10,4],[4,4],[4,10],[0,10]])
    graph = Graph(inst)
    assert not Polygon(inst['boundary']).covers(LineString([inst['depot'], inst['tasks'][0]['xy']]))
    assert len(graph.path(0, 1)) > 2
    assert_clear(inst, graph.path(0, 1))


def test_open_convex_fast_path_does_not_accept_endpoint_without_clearance():
    graph = Graph(instance([-4.5, 0.], [0., 0.]))
    assert np.isinf(graph.D[0, 1])
    with pytest.raises(ValueError, match='不连通'):
        graph.path(0, 1)


def test_obstacle_separating_boundary_remains_disconnected():
    inst = instance([-3., 0.], [3., 0.], obstacles=[[[-.5,-5],[.5,-5],[.5,5],[-.5,5]]])
    graph = Graph(inst)
    assert np.isinf(graph.D[0, 1])
    with pytest.raises(ValueError, match='不连通'):
        graph.path(0, 1)
