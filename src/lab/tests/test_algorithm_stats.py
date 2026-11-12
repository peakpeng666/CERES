from ceres_lab import algorithm_stats as A


def row(aid, value, *, key='k1', budget=1, state='done', valid=1, scenario='s1', wall=.5, utc='2026-11-05T00:00:00Z', run=None):
    return {'run_id': run or f'{aid}-{value}', 'scenario_id': scenario, 'algorithm_id': aid, 'state': state, 'status': 'feasible', 'created_utc': utc,
            'title': scenario, 'value': value, 'unit': 'm', 'wall_s': wall, 'budget_s': budget, 'valid': valid, 'key': key}


def test_head_to_head_counts_one_instance_once_and_keeps_the_best_of_each():
    rows = [row('a', 100), row('a', 90), row('b', 95), row('c', 90)]
    duel = A.head_to_head(rows)
    assert duel['a'] == {'instances': 1, 'wins': 1, 'mean_gap': 0.0}
    assert duel['c']['wins'] == 1 and duel['b']['wins'] == 0
    assert abs(duel['b']['mean_gap'] - 5 / 90) < 1e-12


def test_unlike_runs_are_not_compared():
    rows = [row('a', 90, key='k1'), row('b', 80, key='k2'),                 # different instances
            row('c', 90, budget=1), row('d', 80, budget=5),                  # different budgets
            row('e', 70, valid=0), row('f', 60, state='error', valid=None)]  # failed the check, or did not finish
    duel = A.head_to_head(rows)
    assert duel == {'a': {'instances': 1, 'wins': 1, 'mean_gap': 0.0}, 'c': {'instances': 1, 'wins': 1, 'mean_gap': 0.0}}


def test_a_lone_algorithm_has_no_head_to_head():
    assert A.head_to_head([row('a', 90), row('a', 80)]) == {}


def test_history_keeps_failures_and_the_best_verified_value_per_map():
    rows = [row('a', 90, scenario='s1', utc='2026-11-05T01:00:00Z'), row('a', 95, scenario='s1', utc='2026-11-05T02:00:00Z'),
            row('a', 10, scenario='s2', valid=0, utc='2026-11-05T03:00:00Z'), row('a', None, scenario='s2', state='error', valid=None, wall=None, utc='2026-11-05T04:00:00Z'), row('b', 80)]
    h = A.history(rows, 'a')
    assert (h['runs'], h['completed'], h['valid'], h['failed']) == (4, 3, 2, 1)
    maps = {m['scenario_id']: m for m in h['maps']}
    assert maps['s1']['best'] == 90 and maps['s2']['best'] is None
    assert h['wall_s']['median'] == .5


def test_physical_record_keeps_failures_and_ignores_engine_comparisons():
    rows = [row('a', 90, run='r1'), row('a', 91, run='r2'), row('b', 80, run='r3')]
    ok = {'run_id': 'r1', 'state': 'succeeded', 'check': {'valid': True}, 'metrics': {'total_distance_m': 102., 'planned_total_distance_m': 100., 'cross_track_max_m': .12}}
    crashed = {'run_id': 'r2', 'state': 'failed', 'check': {'valid': False}, 'metrics': {}}
    replay = {**ok, 'session_id': 'field_1', 'metrics': {'total_distance_m': 500., 'planned_total_distance_m': 100.}}
    other = {**ok, 'run_id': 'r3', 'metrics': {'total_distance_m': 400., 'planned_total_distance_m': 100.}}
    p = A.physical_record(rows, [ok, crashed, replay, other], 'a')
    assert p == {'runs': 2, 'passed': 1, 'distance_ratio': 1.02, 'cross_track_max_m': .12}
    assert A.physical_record(rows, [], 'a') is None
