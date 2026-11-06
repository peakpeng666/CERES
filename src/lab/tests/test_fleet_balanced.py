"""The solver for time goals, and the execution estimate every fleet plan carries."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from urp_lab import compose, field_coordination as fc
from urp_lab.recommend import recommend
from urp_lab.scenarios import validate_instance
from urp_lab.solvers import catalog, fleet


def instance(text='在 90×60 米的果园里有一个池塘，3 台车访问 18 个点，单车容量 6', seed=42, objective='makespan'):
    scenario = compose.compose(text, seed)
    inst = validate_instance('fleet_routing', scenario['instance'])
    inst['objective'] = objective
    return scenario, inst


def solved(inst, scenario, algorithm, seconds=1):
    routes, check, metrics, raw, status, _ = fleet.solve(scenario, inst, algorithm, {'time_limit_s': seconds, 'seed': 42})
    assert status == 'feasible' and check['valid'], check
    return routes, metrics, raw


def test_it_shortens_the_latest_finish_of_the_distance_solver():
    scenario, inst = instance()
    _, plain, _ = solved(inst, scenario, 'pyvrp.fleet')
    _, balanced, raw = solved(inst, scenario, 'pyvrp.balanced')
    assert balanced['makespan_s'] < plain['makespan_s']
    assert raw['model_contract']['optimization']['trip_allocation'] == 'PyVRP multi-trip routes'


def test_it_reloads_inside_a_route_when_the_load_needs_more_trips_than_vehicles():
    scenario, inst = instance('在 80×60 米的菜地里，2 台车访问 14 个点，单车容量 3')
    routes, metrics, _ = solved(inst, scenario, 'pyvrp.balanced')
    assert len(routes) == 2 and metrics['trips'] > 2
    assert sorted(t for r in routes for trip in r['trips'] for t in trip) == sorted(t['id'] for t in inst['tasks'])


def test_single_trip_mode_stays_one_trip_per_vehicle():
    scenario, inst = instance('在 80×60 米的菜地里，3 台车访问 9 个点，单车容量 3')
    inst['vehicles']['trips'] = 'single'
    routes, metrics, _ = solved(inst, scenario, 'pyvrp.balanced')
    assert metrics['trips'] == len(routes) <= 3


def test_it_is_in_the_library_and_leads_when_time_is_the_goal():
    entry = next(a for a in catalog() if a['id'] == 'pyvrp.balanced')
    assert 'fleet_routing' in entry['problem_classes'] and entry['available']
    scenario, inst = instance()
    ranked = recommend({**scenario, 'problem_class': 'fleet_routing'}, inst)
    assert ranked['recommended'] == 'pyvrp.balanced' and 'ortools.fleet' in ranked['compare']
    scenario, inst = instance(objective='total_distance')
    assert recommend({**scenario, 'problem_class': 'fleet_routing'}, inst)['recommended'] == 'pyvrp.fleet'


def test_the_estimate_is_the_same_every_time_and_above_the_constant_speed_figure():
    scenario, inst = instance()
    routes, metrics, _ = solved(inst, scenario, 'pyvrp.fleet')
    first, second = fc.estimate(inst, routes), fc.estimate(inst, routes)
    assert first['predicted_makespan_s'] == second['predicted_makespan_s']
    assert first['predicted_makespan_s'] > metrics['makespan_s']


def test_the_worker_records_an_optional_estimate_without_losing_the_fleet_run():
    scenario, inst = instance()
    request = {'scenario': {**scenario, 'problem_class': 'fleet_routing'}, 'instance': inst, 'algorithm_id': 'panel.sweep', 'params': {'time_limit_s': 1, 'seed': 1}}
    done = subprocess.run([sys.executable, '-m', 'urp_lab.worker'], input=json.dumps(request), capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=120)
    result = json.loads(done.stdout)
    assert done.returncode == 0 and result['status'] == 'feasible' and result['routes']
    estimate = result['metrics']['execution_estimate']
    if estimate['status'] == 'estimated':
        assert result['metrics']['predicted_makespan_s'] > 0 and result['metrics']['route_meetings'] >= 0
    else:
        # Import/compilation time varies by host; optional postprocessing cannot consume the solver result.
        assert estimate['status'] == 'unavailable' and estimate['reason']
        assert 'predicted_makespan_s' not in result['metrics']
