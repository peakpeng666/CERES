"""Missing travel arcs must stay forbidden, not become signed integer costs."""
from copy import deepcopy

import numpy as np
import pytest

from urp_lab.scenarios.validate import validate_instance
from urp_lab.solvers import fleet
from urp_lab.solvers.fleet_engines import _forbid_unreachable_arcs, _scaled_costs, metaheuristic, ortools_makespan

pytestmark = pytest.mark.filterwarnings('error::RuntimeWarning')
ENGINES = [('pyvrp.fleet', 'total_distance'), ('ortools.fleet', 'total_distance'),
           ('ortools.fleet', 'makespan')]


def instance(*, trips='single', capacity=1, count=2, tasks=None, walls=True, **changes):
    raw = {'boundary': [[0, 0], [10, 0], [10, 10], [0, 10]], 'robot_radius': .2,
           'depot': [2, 2], 'depots': [[2, 2], [8, 2]], 'plots': [],
           'obstacles': [{'polygon': [[4.5, 0], [5.5, 0], [5.5, 10], [4.5, 10]]}] if walls else [],
           'tasks': tasks or [{'xy': [2, 8]}, {'xy': [8, 8]}],
           'vehicles': {'count': count, 'capacity': capacity, 'trips': trips, 'speed_mps': 1}}
    raw.update(changes)
    return validate_instance('fleet_routing', raw)


def solve_checked(inst, algorithm, objective):
    inst['objective'] = objective
    routes, check, metrics, raw, status, trace = fleet.solve({}, inst, algorithm, {'time_limit_s': .2, 'seed': 42})
    assert status == 'feasible', (status, raw, check)
    assert check['valid'], check
    # Re-run the unmodified geometry/capacity/service/home checker independently.
    assert fleet.check(inst, deepcopy(routes))['valid']
    assert sorted(t for route in routes for t in route['sequence']) == sorted(t['id'] for t in inst['tasks'])
    for route in routes:
        home = inst['depots'][route['vehicle'] % len(inst['depots'])]
        assert route['polyline'][0] == home == route['polyline'][-1]
    return routes, metrics


@pytest.mark.parametrize('algorithm,objective', ENGINES)
@pytest.mark.parametrize('trips', ['single', 'multi'])
def test_separate_depots_serve_their_own_components(algorithm, objective, trips):
    inst = instance(trips=trips)
    graph = fleet.Graph(inst)
    assert fleet.unreachable_tasks(inst, graph.D, 2) == []
    assert np.isinf(graph.D[0, 1]) and np.isinf(graph.D[2, 3])
    routes, metrics = solve_checked(inst, algorithm, objective)
    assert sorted((r['depot'], r['sequence'], r['cost']) for r in routes) == [(0, [1], 12.), (1, [2], 12.)]
    assert metrics['total_distance_m'] == 24


@pytest.mark.parametrize('algorithm,objective', ENGINES)
def test_duplicate_vehicle_start_end_indices(algorithm, objective):
    inst = instance(count=4, tasks=[{'xy': [2, 8]}, {'xy': [8, 8]}, {'xy': [2, 6]}, {'xy': [8, 6]}])
    routes, _ = solve_checked(inst, algorithm, objective)
    assert len(routes) == 4
    assert all(r['load'] == 1 for r in routes)


@pytest.mark.parametrize('algorithm,objective', ENGINES)
def test_component_capacity_shortage_is_not_bridged(algorithm, objective):
    inst = instance(capacity=2, tasks=[{'xy': [2, 8]}, {'xy': [2, 6]}, {'xy': [2, 4]}, {'xy': [8, 8]}])
    inst['objective'] = objective
    assert sum(t['demand'] for t in inst['tasks']) == inst['vehicles']['count'] * inst['vehicles']['capacity']
    result = fleet.solve({}, inst, algorithm, {'time_limit_s': .1, 'seed': 42})
    assert result[4] == 'no_solution' and not result[0]


@pytest.mark.parametrize('algorithm,objective', ENGINES)
def test_depot_without_vehicle_does_not_make_task_serviceable(algorithm, objective):
    inst = instance(count=1, capacity=2, tasks=[{'xy': [8, 8]}])
    inst['objective'] = objective
    assert fleet.unreachable_tasks(inst, fleet.Graph(inst).D, 2) == []
    result = fleet.solve({}, inst, algorithm, {'time_limit_s': .1, 'seed': 42})
    assert result[4] == 'no_solution' and not result[0]


@pytest.mark.parametrize('algorithm,objective', ENGINES)
def test_task_disconnected_from_every_depot_is_rejected(algorithm, objective):
    inst = instance(depots=[[2, 2]], tasks=[{'xy': [8, 8]}])
    inst['objective'] = objective
    with pytest.raises(ValueError, match='与所有车库不连通'):
        fleet.solve({}, inst, algorithm, {'time_limit_s': .1})


@pytest.mark.parametrize('algorithm,objective', ENGINES)
def test_single_connected_depot_regression(algorithm, objective):
    inst = instance(walls=False, depots=[[2, 2]], capacity=2)
    _, metrics = solve_checked(inst, algorithm, objective)
    assert np.isfinite(metrics['total_distance_m'])


@pytest.mark.parametrize('algorithm,objective', ENGINES)
def test_disconnected_heterogeneous_capacities_speeds_and_service(algorithm, objective):
    inst = instance(tasks=[{'xy': [2, 8], 'service_s': 3}, {'xy': [8, 8], 'demand': 2, 'service_s': 4}],
                    vehicles={'trips': 'single', 'fleet': [
                        {'name': 'left', 'count': 1, 'capacity': 1, 'speed_mps': 1, 'robot_radius': .2},
                        {'name': 'right', 'count': 1, 'capacity': 2, 'speed_mps': 2, 'robot_radius': .2}]})
    routes, metrics = solve_checked(inst, algorithm, objective)
    assert {r['fleet_class']: (r['load'], r['duration_s']) for r in routes} == {'left': (1, 15.), 'right': (2, 10.)}
    assert metrics['makespan_s'] == 15


@pytest.mark.parametrize('algorithm,objective', ENGINES)
def test_multitrip_disconnected_components_need_real_reloads(algorithm, objective):
    inst = instance(trips='multi', tasks=[{'xy': [2, 8]}, {'xy': [8, 8]}, {'xy': [2, 6]}, {'xy': [8, 6]}],
                    unload_points=[[2, 5], [8, 5]])
    routes, metrics = solve_checked(inst, algorithm, objective)
    assert metrics['trips'] == 4
    assert all(len(r['trips']) == 2 and r['load'] == 1 for r in routes)


def test_makespan_ignores_unreachable_optional_unload_copies():
    inst = instance(trips='multi', depots=[[1, 2], [9, 2]], depot=[1, 2],
                    obstacles=[{'polygon': [[3, 0], [4, 0], [4, 10], [3, 10]]},
                               {'polygon': [[6, 0], [7, 0], [7, 10], [6, 10]]}],
                    tasks=[{'xy': [1, 8]}, {'xy': [9, 8]}, {'xy': [1, 6]}, {'xy': [9, 6]}],
                    unload_points=[[5, 5]])
    routes, metrics = solve_checked(inst, 'ortools.fleet', 'makespan')
    assert metrics['trips'] == 4
    assert all([5., 5.] not in r['polyline'] for r in routes)


def test_ortools_forbidden_domains_cover_all_start_and_end_copies():
    from ortools.constraint_solver import pywrapcp
    manager = pywrapcp.RoutingIndexManager(4, 4, [0, 1, 0, 1], [0, 1, 0, 1])
    routing = pywrapcp.RoutingModel(manager)
    component = np.array([0, 1, 0, 1])
    reachable = component[:, None] == component[None, :]
    _forbid_unreachable_arcs(routing, manager, reachable)
    for i in range(routing.Size()):
        for j in range(manager.GetNumberOfIndices()):
            if not reachable[manager.IndexToNode(i), manager.IndexToNode(j)]:
                assert not routing.NextVar(i).Contains(j)
    for v in range(4):
        assert routing.NextVar(routing.Start(v)).Contains(routing.End(v))


def test_pyvrp_missing_edges_are_native_and_infeasible(monkeypatch):
    from pyvrp import Model, Route, Solution
    from pyvrp.constants import MAX_VALUE
    original_solve = Model.solve

    def inspect_model(self, *args, **kwargs):
        data = self.data()
        assert data.distance_matrix(0)[0, 3] == MAX_VALUE
        assert data.distance_matrix(0)[0, 2] == 600
        assert data.vehicle_type(0).max_distance == 1800 < MAX_VALUE
        # A task from the wrong component must be infeasible even if capacity fits.
        assert not Solution(data, [Route(data, [1], 0), Route(data, [0], 1)]).is_feasible()
        return original_solve(self, *args, **kwargs)

    monkeypatch.setattr(Model, 'solve', inspect_model)
    solve_checked(instance(), 'pyvrp.fleet', 'total_distance')


def test_scaling_never_converts_positive_infinity():
    costs, reachable = _scaled_costs(np.array([[0., np.inf], [1.234, 0.]]), 100, 1000)
    assert costs.tolist() == [[0, 0], [123, 0]]
    assert reachable.tolist() == [[True, False], [True, True]]


@pytest.mark.parametrize('bad', [np.nan, -np.inf, -1.])
def test_invalid_costs_fail_explicitly(bad):
    with pytest.raises(ValueError, match='非负'):
        _scaled_costs(np.array([[0., bad], [0., 0.]]), 100, 1000)


@pytest.mark.parametrize('bad,scale', [(1e308, 100), (1e30, 1), (float(2**63), 1)])
def test_cost_scaling_overflow_fails_before_integer_cast(bad, scale):
    with pytest.raises(ValueError, match='安全整数标度'):
        _scaled_costs(np.array([[0., bad], [bad, 0.]]), scale, np.iinfo(np.int64).max)


def test_explicit_reachability_detects_arithmetic_overflow():
    with pytest.raises(ValueError, match='安全整数标度'):
        _scaled_costs(np.array([[0., np.inf], [1., 0.]]), 10, 1000, np.ones((2, 2), dtype=bool))


@pytest.mark.parametrize('engine', ['pyvrp', 'ortools'])
def test_distance_solver_rejects_unsafe_aggregate_scale(engine):
    inst = instance()
    matrix = np.ones((4, 4)) * 1e30
    np.fill_diagonal(matrix, 0)
    with pytest.raises(ValueError, match='安全整数标度'):
        metaheuristic(engine, inst, matrix, [0, 0, 1, 1], {}, 2)


@pytest.mark.parametrize('large', ['distance', 'service', 'speed'])
def test_makespan_rejects_unsafe_horizon_or_arithmetic(large):
    inst = instance()
    matrix = np.ones((4, 4))
    np.fill_diagonal(matrix, 0)
    if large == 'distance':
        matrix[0, 2] = 1e30
    elif large == 'service':
        inst['tasks'][0]['service_s'] = 1e30
    else:
        inst['vehicles']['speed_mps'] = 1e-310
    with pytest.raises(ValueError, match='安全整数标度'):
        ortools_makespan(inst, matrix, [0, 0, 1, 1], {}, 2, False)


@pytest.mark.parametrize('algorithm,objective', ENGINES)
def test_colocated_zero_cost_routes_do_not_enable_missing_edges(algorithm, objective):
    inst = instance(tasks=[{'xy': [2, 2]}, {'xy': [8, 2]}])
    routes, metrics = solve_checked(inst, algorithm, objective)
    assert metrics['total_distance_m'] == 0
    assert sorted(r['depot'] for r in routes) == [0, 1]


@pytest.mark.parametrize('algorithm,objective', ENGINES)
@pytest.mark.parametrize('missing_arc', [(0, 1), (1, 0)])
def test_missing_start_or_return_arc_is_infeasible(algorithm, objective, missing_arc):
    inst = instance(depots=[[2, 2]], count=1, tasks=[{'xy': [2, 8]}])
    matrix = np.array([[0., 6.], [6., 0.]])
    matrix[missing_arc] = np.inf
    if objective == 'makespan':
        result = ortools_makespan(inst, matrix, [0, 1], {'time_limit_s': .05}, 1, False)
    else:
        result = metaheuristic(algorithm.split('.')[0], inst, matrix, [0, 1], {'time_limit_s': .05}, 1)
    assert result[0] is None


def test_largest_representable_float_below_int64_limit_is_safe():
    value = np.nextafter(float(2**63), 0.)
    costs, _ = _scaled_costs(np.array([[value]]), 1, np.iinfo(np.int64).max)
    assert int(costs[0, 0]) == int(value) > 0


@pytest.mark.parametrize('algorithm,objective', ENGINES)
@pytest.mark.parametrize('trips', ['single', 'multi'])
@pytest.mark.parametrize('home_x', [2, 8])
@pytest.mark.parametrize('walls', [False, True])
def test_unused_depot_does_not_require_a_visit(algorithm, objective, trips, home_x, walls):
    inst = instance(count=1, capacity=2 if trips == 'single' else 1, trips=trips, walls=walls,
                    depots=[[home_x, 2], [10 - home_x, 2]],
                    tasks=[{'xy': [home_x, 6]}, {'xy': [home_x, 8]}])
    routes, metrics = solve_checked(inst, algorithm, objective)
    assert len(routes) == 1 and routes[0]['vehicle'] == 0
    assert metrics['trips'] == (1 if trips == 'single' else 2)
    assert all(task > 0 for trip in routes[0]['trips'] for task in trip)
    assert inst['depots'][1] not in routes[0]['polyline']


@pytest.mark.parametrize('algorithm,objective', ENGINES)
@pytest.mark.parametrize('walls', [False, True])
def test_multiple_unused_depots_leave_real_reload_copies_intact(algorithm, objective, walls):
    inst = instance(count=1, trips='multi', walls=walls,
                    depots=[[2, 2], [8, 2], [3, 2], [8, 4]],
                    tasks=[{'xy': [2, 6]}, {'xy': [2, 8]}])
    routes, metrics = solve_checked(inst, algorithm, objective)
    assert len(routes) == 1 and metrics['trips'] == 2
    assert all(depot not in routes[0]['polyline'] for depot in inst['depots'][1:])


def test_unused_original_depots_are_forced_inactive_not_just_optional():
    from ortools.constraint_solver import pywrapcp
    from urp_lab.solvers.fleet_engines import _disable_unused_depots
    # Four original depots, two tasks and two separately modelled reload copies.
    manager = pywrapcp.RoutingIndexManager(8, 2, [0, 0], [0, 0])
    routing = pywrapcp.RoutingModel(manager)
    _disable_unused_depots(routing, manager, 4, [0, 0])
    for node in [1, 2, 3]:
        idx = manager.NodeToIndex(node)
        assert routing.ActiveVar(idx).Min() == routing.ActiveVar(idx).Max() == 0
    for node in [4, 5, 6, 7]:
        assert routing.ActiveVar(manager.NodeToIndex(node)).Max() == 1
    assert all(routing.ActiveVar(routing.Start(v)).Max() == 1 for v in range(2))


def test_unused_depot_does_not_overflow_total_cost_with_hidden_extra_arc():
    inst = instance(count=1, walls=False, tasks=[{'xy': [2, 8]}])
    matrix = np.full((3, 3), 4e16)
    np.fill_diagonal(matrix, 0)
    trips, classes, depots, trace = metaheuristic('ortools', inst, matrix, [0, 0, 1], {'time_limit_s': .05}, 1)
    assert trips == [[2]] and classes == [0] and depots == [0]
    # Two real arcs = 8e18 scaled cost, NOT three arcs via an unused depot
    # (1.2e19, which used to saturate the trace at INT64_MAX / 100).
    assert trace and all(cost == 8e16 for _, cost in trace)


def test_active_route_total_above_int64_is_rejected_even_with_unused_depot():
    inst = instance(count=1, walls=False, tasks=[{'xy': [2, 8]}])
    matrix = np.full((3, 3), 5e16)
    np.fill_diagonal(matrix, 0)
    with pytest.raises(ValueError, match='安全整数标度'):
        metaheuristic('ortools', inst, matrix, [0, 0, 1], {'time_limit_s': .05}, 1)
