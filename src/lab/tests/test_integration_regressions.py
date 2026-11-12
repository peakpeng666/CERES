"""Non-vacuous checks of the independently reproduced integration defects."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from ceres_lab.field_energy import account
from ceres_lab.field_physics import profile
from ceres_lab.recommend import recommend
from ceres_lab.scenarios.validate import validate_instance
from ceres_lab.solvers import run_algorithm
from ceres_lab.solvers.fleet import min_curvature_radius, travel_graph


@pytest.mark.parametrize('schema', [None, 'urp.fleet-model/1'])
def test_legacy_resupply_plan_cannot_bypass_ui_gate(schema):
    from ceres_lab.fieldsim import FieldSimManager
    from ceres_lab.services import ServiceError
    run = {'problem_class': 'fleet_routing', 'state': 'done', 'routes': [{}],
           'status': 'feasible', 'check': {'valid': True},
           'instance': {'unload_points': [[38, 15]]},
           'raw_result': {'model_contract': {'schema': schema}}}
    manager = object.__new__(FieldSimManager)
    manager.store = SimpleNamespace(get_run=lambda _: run)
    with pytest.raises(ServiceError) as caught:
        manager.start({'run_id': 'legacy'})
    assert caught.value.code == 'obsolete_resupply_model'

SC = {'problem_class': 'fleet_routing', 'frame': {'kind': 'field'}, 'cost_unit': 's'}


def instance(travel='free', multidepot=False, supply=True):
    return validate_instance('fleet_routing', {
        'boundary': [[0, 0], [60, 0], [60, 30], [0, 30]],
        'depot': [3, 15], 'depots': [[3, 15], [55, 25]] if multidepot else [[3, 15]],
        'objective': 'makespan', 'unload_points': [[38, 15]] if supply else [],
        'tasks': [{'id': i + 1, 'xy': [45 + 4 * (i % 3), 8 + 12 * (i % 2)], 'demand': 4} for i in range(8)],
        'vehicles': {'count': 2 if multidepot else 1, 'capacity': 16, 'speed_mps': 1,
                     'trips': 'multi', 'travel': travel}, 'robot_radius': .5})


@pytest.mark.parametrize('travel', ['free', 'rows'])
@pytest.mark.parametrize('multidepot', [False, True])
@pytest.mark.parametrize('supply', [False, True])
def test_graph_identity_and_decoded_routes(travel, multidepot, supply):
    inst = instance(travel, multidepot, supply)
    graph = travel_graph(inst)
    nd = len(inst['depots'])
    assert list(graph.points[:nd]) == [tuple(p) for p in inst['depots']]
    assert list(graph.points[nd:nd + 8]) == [tuple(t['xy']) for t in inst['tasks']]
    result = run_algorithm(SC, inst, 'ortools.fleet', {'time_limit_s': .4, 'seed': 42})
    assert result['status'] == 'feasible', result
    assert result['check']['valid']
    for route in result['routes']:
        home = inst['depots'][route.get('depot', 0)]
        assert math.dist(route['polyline'][0], home) < 1e-8
        assert math.dist(route['polyline'][-1], home) < 1e-8
        if route.get('reload_stops'):
            assert len(route['reload_stops']) == len(route['trips']) - 1
    if supply and not multidepot:
        assert result['routes'][0]['reload_stops'] == [[38., 15.]]
        assert result['metrics']['makespan_s'] < 145  # old extraction was 203.93 s
    # Decisecond rounding can accumulate at most 0.05 s per model arc.
    assert abs(result['metrics']['makespan_s'] - result['trace'][-1][1]) < 1
    if not multidepot:
        from ceres_lab.fieldworld import plan_vehicles
        vehicles = plan_vehicles(inst, result['routes'])
        assert len(vehicles[0].tasks) == len(inst['tasks'])
        stops = [t['path_s'] for t in vehicles[0].tasks]
        assert stops == sorted(stops)


@pytest.mark.parametrize('algorithm', ['ortools.fleet', 'pyvrp.fleet'])
def test_small_vehicle_can_serve_small_demand(algorithm):
    inst = instance(supply=False)
    inst['tasks'] = [{'id': 1, 'xy': [10, 10], 'demand': 1}, {'id': 2, 'xy': [20, 10], 'demand': 10}]
    inst['vehicles'] = {'fleet': [{'name': 'small', 'count': 1, 'capacity': 1},
                                  {'name': 'large', 'count': 1, 'capacity': 10}], 'trips': 'single'}
    inst = validate_instance('fleet_routing', inst)
    result = run_algorithm(SC, inst, algorithm, {'time_limit_s': .4})
    assert result['status'] == 'feasible' and result['check']['valid'], result
    assert {r['fleet_class']: r['sequence'] for r in result['routes']} == {'small': [1], 'large': [2]}
    rec = recommend(SC, inst)
    baselines = [r for r in rec['ranking'] if r['algorithm_id'].startswith('panel.')]
    assert baselines and all(not r['applicable'] for r in baselines)
    assert all(not a.startswith('panel.') for a in rec['compare'])


def test_multidepot_recommendation_rejects_constructive_baselines():
    rec = recommend(SC, instance(multidepot=True, supply=False))
    assert all(not r['applicable'] for r in rec['ranking'] if r['algorithm_id'].startswith('panel.'))


def test_forward_cusp_with_duplicate_samples_is_rejected():
    pts = [[x, 0] for x in np.linspace(0, 1, 21)] + [[1, 0]] * 3 + [[x, 0] for x in np.linspace(1, 0, 21)]
    assert min_curvature_radius(pts) == 0
    arc = [[2 * math.cos(a), 2 * math.sin(a)] for a in np.linspace(0, math.pi, 200)]
    assert min_curvature_radius(arc) == pytest.approx(2, rel=1e-8)
    obtuse_arc = [[2 * math.cos(a), 2 * math.sin(a)] for a in [0, 2 * math.pi / 3, 4 * math.pi / 3]]
    assert min_curvature_radius(obtuse_arc) == pytest.approx(2)


def samples(dt, tail):
    result = []
    for t in np.arange(0, 8 + tail + dt / 2, dt):
        x = .25 * t * t if t <= 2 else t - 1 if t <= 5 else 4 + (t-5) - .25*(t-5)**2 if t <= 7 else 5
        result.append([float(t), float(x), 0, 0, 0, 0, 0])
    return result


def test_energy_fixed_time_filter_preserves_parked_tail_and_sampling():
    model = profile()
    base = account(samples(.02, 0), model)
    for tail in [120, 600]:
        changed = account(samples(.02, tail), model)
        for key in ['energy_j', 'peak_power_w', 'distance_m', 'peak_wheel_torque_nm']:
            assert changed[key] == pytest.approx(base[key], rel=1e-8), key
    for dt in [.01, .04]:
        changed = account(samples(dt, 0), model)
        assert changed['energy_j'] == pytest.approx(base['energy_j'], rel=.01)
        assert changed['peak_power_w'] == pytest.approx(base['peak_power_w'], rel=.02)
