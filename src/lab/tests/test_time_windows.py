"""Task time windows from the sentence to the recorded arrival: what is asked, what is solved, what is checked and what ran."""
import copy
import random
import time

import numpy as np
import pytest
from scipy import ndimage

from ceres_lab import field_coordination as fc
from ceres_lab import fieldworld, mujoco_field, paths, time_windows, worker
from ceres_lab.agent import contract as tc
from ceres_lab.agent import diagnose, llm
from ceres_lab.agent.runner import AgentRunner, _executed, _execution_fault
from ceres_lab.compose import compose
from ceres_lab.compose.nl import clock_value, duration_value, parse
from ceres_lab.field_tracking import Driver
from ceres_lab.gateway import Config, Lab
from ceres_lab.recommend import recommend
from ceres_lab.scenarios import validate_instance
from ceres_lab.solvers import catalog, fleet

WINDOWED = ('pyvrp.balanced', 'ortools.fleet')
NOT_WINDOWED = ('pyvrp.fleet', 'panel.sweep', 'panel.savings_fleet', 'panel.cluster_first')
SCENARIO = {'id': 't', 'problem_class': 'fleet_routing', 'frame': {'kind': 'field', 'units': 'm'}}


def field(window=None, tasks=8, vehicles=2, objective='total_distance', **extra):
    """Two rows of tasks in an open field, far enough apart that one vehicle cannot serve them all quickly."""
    points = [[12 + 12 * (i % 4), 8 + 18 * (i // 4)] for i in range(tasks)]
    rows = [{'id': i + 1, 'xy': p, 'label': f'T{i + 1}', **({'time_window': window} if window else {})} for i, p in enumerate(points)]
    return validate_instance('fleet_routing', {
        'boundary': [[0, 0], [70, 0], [70, 40], [0, 40]], 'depot': [4, 4], 'tasks': rows, 'objective': objective,
        'vehicles': {'count': vehicles, 'capacity': tasks, 'travel': 'free', 'speed_mps': 1, 'trips': 'multi'}, **extra})


def solve(inst, algorithm='pyvrp.balanced', budget=2.0):
    return fleet.solve(SCENARIO, inst, algorithm, {'time_limit_s': budget, 'seed': 7})


# ------------------------------------------------------------------ language --
@pytest.mark.parametrize('text, spec', [
    ('两台车，十二个投放点，每个点要在上午九点前送达', {'deadline': '09:00', 'tasks': 12, 'vehicles': 2}),
    ('上午九点前送达十二个投放点', {'deadline': '09:00', 'tasks': 12}),
    ('早上七点半从西南角车库出发，20个采样点，每个点 9 点前到', {'start_clock': '07:30', 'deadline': '09:00', 'tasks': 20, 'depot': 'sw'}),
    ('每个点要在出发后 30 分钟内送达，10个点', {'deadline': '1800s', 'tasks': 10}),
    ('半小时内全部送达，10 个投放点', {'deadline': '1800s', 'tasks': 10}),
    ('一个半小时内完成巡检，20 个点', {'deadline': '5400s', 'tasks': 20}),
    ('下午两点出发，五点前完成 12 个点的采样', {'start_clock': '14:00', 'deadline': '17:00', 'tasks': 12}),
    ('上午八点到九点之间送达，10 个点，七点出发', {'start_clock': '07:00', 'earliest': '08:00', 'deadline': '09:00', 'tasks': 10}),
    ('下午两点到五点之间采样 10 个点，一点出发', {'start_clock': '13:00', 'earliest': '14:00', 'deadline': '17:00', 'tasks': 10}),
    ('八点以后才能开始作业，十点前完成，七点半出发，10 个点', {'start_clock': '07:30', 'earliest': '08:00', 'deadline': '10:00', 'tasks': 10}),
    ('不晚于 9:30 到达，不早于 8:00，10 个点，7:00 开始', {'start_clock': '07:00', 'earliest': '08:00', 'deadline': '09:30', 'tasks': 10}),
    ('发车 10 分钟后才能开始作业，1 小时 30 分钟内完成', {'earliest': '600s', 'deadline': '5400s'}),
    ('start at 7:30, deliver by 9:00, 10 points', {'start_clock': '07:30', 'deadline': '09:00', 'tasks': 10}),
])
def test_time_phrases_are_read_and_do_not_leak_into_counts(text, spec):
    parsed = parse(text)
    assert not parsed['unsupported']
    assert {k: parsed['spec'].get(k) for k in spec} == spec


@pytest.mark.parametrize('text', ['12 点采样，2 台车', '巡检 40 点，3 台车', '在 5 分钟内算出方案，10 个点', '10 台车，每个点停留 30 秒', '要最早完成，10 个点'])
def test_numbers_that_are_not_times_stay_what_they_were(text):
    parsed = parse(text)
    assert not parsed['unsupported']
    assert not {'deadline', 'earliest', 'start_clock'} & set(parsed['spec'])


@pytest.mark.parametrize('text, clause', [
    ('A 区的点九点前完成，其他的十点前', 'A 区的点九点前完成'),
    ('明天上午九点前送达，10 个点', '明天上午九点前送达'),
    ('十二个点上午九点送到', '十二个点上午九点送到'),
    ('七点前出发，10 个采样点', '七点前出发'),
    ('要满足时间窗，10 个点', '要满足时间窗'),
    ('3 号点八点前到，5 号点九点前到', '3 号点八点前到'),
])
def test_what_one_shared_window_cannot_say_is_kept_verbatim(text, clause):
    parsed = parse(text)
    assert clause in parsed['unsupported']
    assert 'deadline' not in parsed['spec']


def test_written_times_and_durations_read_the_same_wherever_they_appear():
    assert [clock_value(x) for x in ('09:00', '上午九点', '8点一刻', '下午三点半', '七点', '7', '晚上8点', '9am', 'abc')] == \
        ['09:00', '09:00', '08:15', '15:30', '07:00', '07:00', '20:00', '09:00', None]
    assert [duration_value(x) for x in ('半小时', '一个半小时', '45 分钟', '1小时30分钟', '两刻钟', '90 秒', '30 minutes', 'abc')] == \
        [1800, 5400, 2700, 5400, 1800, 90, 1800, None]


# ------------------------------------------------------------------ contract --
def test_a_time_of_day_needs_a_departure_and_is_never_given_one():
    contract = tc.from_rule_parse('30 亩菜地，两台小车运输，十二个投放点，每个点要在上午九点前送达')
    assert tc.value(contract, 'task_deadline') == '09:00'
    assert tc.missing(contract) == ['start_clock']
    with pytest.raises(ValueError, match='出发时刻'):
        tc.confirm(contract)
    contract, _, _ = tc.apply(contract, {'start_clock': '七点半'}, 'user_edit')
    assert tc.missing(contract) == [] and tc.window(contract) == [0.0, 5400.0]
    assert [c['text'] for c in tc.cards(contract) if c['key'] in tc.TIME_FIELDS] == ['07:30', '09:00']


def test_a_relative_deadline_needs_no_departure():
    contract = tc.from_rule_parse('60×40米果园，2台车，8个采样点，每个点要在出发后 30 分钟内送达')
    assert tc.missing(contract) == [] and tc.window(contract) == [0.0, 1800.0]
    assert tc.format_value('task_deadline', '1800s') == '出发后 30 分钟'


@pytest.mark.parametrize('values, gap', [({'start_clock': '09:30', 'task_deadline': '09:00'}, 'start_clock'),
                                         ({'start_clock': '07:00', 'task_deadline': '09:00', 'task_earliest': '10:00'}, 'task_deadline')])
def test_times_that_cannot_form_a_window_are_asked_about(values, gap):
    contract, _, rejected = tc.apply(tc.empty(), {'task_count': 4, 'vehicle_count': 1, 'field_size_m': [40, 30], **values}, 'user_edit')
    assert not rejected and tc.missing(contract) == [gap]


@pytest.mark.parametrize('key, raw', [('task_deadline', 'abc'), ('task_deadline', -5), ('start_clock', '25:00'), ('task_deadline', True), ('task_earliest', 90000)])
def test_a_time_the_model_cannot_be_held_to_is_rejected_with_a_reason(key, raw):
    contract, accepted, rejected = tc.apply(tc.empty(), {key: raw}, 'llm')
    assert not accepted and rejected[0]['key'] == key and rejected[0]['reason']


def test_the_model_can_fill_the_same_fields_from_its_own_schema():
    schema = tc.tool_schema()['properties']['fields']['properties']
    assert schema['task_deadline']['type'] == schema['start_clock']['type'] == 'string'
    contract, _, rejected = tc.apply(tc.empty(), {'task_deadline': '半小时', 'task_earliest': 120, 'start_clock': '08:00'}, 'llm', {'task_deadline': '半小时内'})
    assert not rejected and tc.window(contract) == [120.0, 1800.0]


def test_a_later_deadline_is_written_the_way_the_user_wrote_it():
    clock, _, _ = tc.apply(tc.empty(), {'start_clock': '07:30', 'task_deadline': '09:00'}, 'user_edit')
    relative, _, _ = tc.apply(tc.empty(), {'task_deadline': '600s'}, 'user_edit')
    assert tc.later_deadline(clock, 6000) == '09:10' and tc.later_deadline(relative, 2000) == '2040s'


def test_per_task_requests_reach_the_contract_as_unsupported():
    contract = tc.from_rule_parse('60×40米果园，2台车，8个点，A 区九点前完成')
    assert contract['unsupported'] == ['A 区九点前完成'] and 'task_deadline' not in contract['fields']


# ---------------------------------------------------------------- validation --
def test_each_task_may_carry_its_own_window():
    inst = field([0, 600])
    assert inst['tasks'][0]['time_window'] == [0.0, 600.0]
    inst['tasks'][3]['time_window'] = [30, None]
    again = validate_instance('fleet_routing', inst)
    assert again['tasks'][3]['time_window'] == [30.0, None]
    assert validate_instance('fleet_routing', again) == again


@pytest.mark.parametrize('window, reason', [([100, 50], '早于最早时间'), ([-1, 50], '不能为负'), ('09:00', '时间窗应为'), ([1], '时间窗应为'), ([0, 'x'], '有限数值')])
def test_a_window_that_means_nothing_is_refused(window, reason):
    inst = field()
    inst['tasks'][0]['time_window'] = window
    with pytest.raises(ValueError, match=reason):
        validate_instance('fleet_routing', inst)


def test_a_window_that_bounds_nothing_is_dropped():
    inst = field()
    inst['tasks'][0]['time_window'] = [0, None]
    assert 'time_window' not in validate_instance('fleet_routing', inst)['tasks'][0]


def test_other_problem_classes_and_several_windows_stay_refused():
    cvrp = {'coordinates': [[0, 0], [1, 1]], 'demands': [0, 1], 'capacity': 1, 'customers': [{'id': 1, 'time_window': [0, 5]}]}
    with pytest.raises(ValueError, match='尚未实现时间窗'):
        validate_instance('euclidean_cvrp', cvrp)
    inst = field()
    inst['tasks'][0]['time_windows'] = [[0, 5], [10, 20]]
    with pytest.raises(ValueError, match='多段时间窗'):
        validate_instance('fleet_routing', inst)
    bad = field()
    bad['start_clock'] = '7 点'
    with pytest.raises(ValueError, match='start_clock'):
        validate_instance('fleet_routing', bad)


# ---------------------------------------------------------------- time model --
def line(*tasks, path=((0, 0), (30, 0))):
    return {'name': 'a', 'label': 'a', 'path': [list(map(float, p)) for p in path], 'tasks': list(tasks)}


def stops(*vehicles, speed=1.0):
    return fc.Schedule(list(vehicles), [], 0.0, speed).arrivals()


def test_arrival_is_before_the_work_and_counts_the_work_ahead():
    rows = stops(line({'id': 1, 'path_s': 10, 'service_s': 5}, {'id': 2, 'path_s': 20, 'service_s': 0}))[0]
    assert [(r['task'], r['arrive_s']) for r in rows] == [(1, 10), (2, 25)]


def test_a_stop_between_vertices_is_timed_where_it_is():
    rows = stops(line({'id': 1, 'path_s': 12.5}, path=((0, 0), (30, 0))))[0]
    assert rows[0]['arrive_s'] == 12.5


def test_a_sharp_corner_ahead_of_a_task_is_counted():
    rows = stops(line({'id': 1, 'path_s': 20}, path=((0, 0), (10, 0), (10, 10))))[0]
    assert rows[0]['arrive_s'] == pytest.approx(20 + 1.5707963 / 1.6 + 1.0, abs=.01)


def test_waiting_for_an_earliest_time_delays_what_comes_after_it():
    rows = stops(line({'id': 1, 'path_s': 10, 'earliest_s': 40}, {'id': 2, 'path_s': 20}))[0]
    assert rows[0] == {'task': 1, 'arrive_s': 10, 'start_s': 40} and rows[1]['arrive_s'] == 50


def test_two_tasks_on_one_spot_are_served_in_turn():
    rows = stops(line({'id': 1, 'path_s': 10, 'service_s': 4}, {'id': 2, 'path_s': 10, 'service_s': 4}))[0]
    assert [r['arrive_s'] for r in rows] == [10, 14]


def test_timeline_itself_is_unchanged_by_stops_being_recorded():
    assert fc.timeline(line({'id': 1, 'path_s': 10, 'service_s': 5}), 2.0) == ([0.0, 30.0], [0.0, 20.0])


def test_a_gate_wait_reaches_the_arrival_behind_it():
    a = {'name': 'a', 'label': 'a', 'path': [[0.0, 0.0], [40.0, 0.0]], 'tasks': [{'id': 1, 'path_s': 35.0}]}
    b = {'name': 'b', 'label': 'b', 'path': [[20.0, -20.0], [20.0, 20.0]], 'tasks': [{'id': 2, 'path_s': 35.0}]}
    plain = stops(a, b)
    gated = fc.compile_gates([a, b], 0.49, 1.0)['arrivals']
    assert gated['a'][0]['arrive_s'] + gated['b'][0]['arrive_s'] > plain[0][0]['arrive_s'] + plain[1][0]['arrive_s'] + 1


def crossing(deadline=None):
    """Two vehicles meet at (20, 0). The long drive gets there first and sets the finish; the short one has a task just past it."""
    long_drive = {'name': 'a', 'label': 'a', 'path': [[0.0, 0.0], [200.0, 0.0]], 'tasks': []}
    short_drive = {'name': 'b', 'label': 'b', 'path': [[20.0, -21.0], [20.0, 30.0]], 'tasks': [{'id': 1, 'path_s': 25.0, **({'latest_s': deadline} if deadline else {})}]}
    return [long_drive, short_drive]


def test_a_deadline_outranks_the_finish_when_vehicles_take_turns():
    free = fc.compile_gates(crossing(), 0.49, 1.0)
    assert free['arrivals']['b'][0]['arrive_s'] > 26, 'left to the finish alone, the short drive waits'
    held = fc.compile_gates(crossing(26), 0.49, 1.0)
    assert held['arrivals']['b'][0]['arrive_s'] <= 26 < free['arrivals']['b'][0]['arrive_s']
    assert held['predicted_makespan_s'] >= free['predicted_makespan_s'], 'the deadline is paid for in the finish'


def grid_meetings(vehicles, radius):
    """The full sample-by-sample grid, kept here as the reference the sparse search has to reproduce."""
    step = max(fc.SAMPLE_M, max(float(fc._arc(v['path'])[-1]) for v in vehicles) / fc.MAX_SAMPLES)
    clearance = 2 * radius + fc.CLEARANCE_MARGIN_M + step
    samples = [fc._sample(v['path'], step) for v in vehicles]
    found = []
    for a in range(len(vehicles)):
        for b in range(a + 1, len(vehicles)):
            (sa, pa), (sb, pb) = samples[a], samples[b]
            d = pa[:, None, :] - pb[None, :, :]
            labels, _ = ndimage.label(np.einsum('ijk,ijk->ij', d, d) < clearance * clearance, structure=np.ones((3, 3), dtype=int))
            for index, window in enumerate(ndimage.find_objects(labels), 1):
                rows = np.flatnonzero((labels[window] == index).any(axis=1)) + window[0].start
                span = max(1, int(round(fc.CHUNK_M / step)))
                boxes = []
                for lo in range(int(rows[0]), int(rows[-1]) + 1, span):
                    hi = min(lo + span, int(rows[-1]) + 1)
                    cols = np.flatnonzero((labels[lo:hi] == index).any(axis=0))
                    if cols.size:
                        boxes.append((a, b, float(sa[lo]), float(sa[hi - 1]), float(sb[cols[0]]), float(sb[cols[-1]])))
                found.append(boxes)
    return found, clearance


def test_the_sparse_search_finds_exactly_what_the_full_grid_finds():
    rng = random.Random(3)
    compared = 0
    for trial in range(15):
        paths_ = [{'name': f'v{i}', 'tasks': [], 'path': [[rng.uniform(0, 80), rng.uniform(0, 50)] for _ in range(rng.randint(2, 9))]} for i in range(rng.randint(2, 4))]
        if trial % 5 == 0:      # a path driven twice has samples at distance zero
            paths_[1]['path'] = [list(p) for p in paths_[0]['path']]
        found, clearance = fc.meetings(paths_, 0.49)
        reference, expected = grid_meetings(paths_, 0.49)
        assert clearance == expected
        assert [[(x.a, x.b, x.a_lo, x.a_hi, x.b_lo, x.b_hi) for x in m.boxes] for m in found] == reference
        compared += len(found)
    assert compared > 100


# ------------------------------------------------------------------- solvers --
@pytest.mark.parametrize('algorithm', WINDOWED)
@pytest.mark.parametrize('objective', ['total_distance', 'makespan'])
def test_a_deadline_that_can_be_met_is_met_and_shown(algorithm, objective):
    routes, chk, metrics, raw, status, _ = solve(field([0, 150], objective=objective), algorithm)
    assert status == 'feasible' and chk['valid']
    tw = chk['time_windows']
    assert tw['evaluated'] and tw['all_on_time'] and tw['total'] == tw['on_time'] == 8 and tw['model'] == 'coordinated'
    assert all(r['arrival_s'] <= 150 for r in tw['tasks'])
    assert metrics['time_window_on_time'] == 8 and metrics['time_window_worst_slack_s'] >= 0


@pytest.mark.parametrize('algorithm', WINDOWED)
def test_a_deadline_no_plan_can_meet_names_the_tasks(algorithm):
    routes, chk, metrics, raw, status, _ = solve(field([0, 70], vehicles=1), algorithm)
    assert status == 'no_solution' and routes == []
    tw = chk['time_windows']
    assert not tw['all_on_time'] and tw['late'] and tw['on_time'] + len(tw['late']) == 8
    assert all(r['arrival_s'] > 70 and r['slack_s'] < 0 and r['label'].startswith('T') for r in tw['late'])
    named = tw['late'][0]
    assert f"任务 {named['label']}" in chk['errors'][0] and '晚于截止' in chk['errors'][0]
    assert named['label'] in raw['reason'] and raw['rejected_routes'] and raw['time_window_rounds']


@pytest.mark.parametrize('algorithm', WINDOWED)
def test_a_task_out_of_reach_proves_the_window_is_what_fails(algorithm):
    inst = field([0, 600])
    inst['tasks'][7]['time_window'] = [0, 10]
    routes, chk, metrics, raw, status, _ = solve(inst, algorithm)
    assert status == 'infeasible' and routes == [] and chk is None
    assert [r['label'] for r in raw['time_window_unreachable']] == ['T8']
    assert '任务 T8' in raw['reason'] and raw['time_window_unreachable'][0]['need_s'] > 10


@pytest.mark.parametrize('algorithm', WINDOWED)
def test_an_early_vehicle_waits_and_only_the_deadline_can_be_missed(algorithm):
    routes, chk, *_, status, _ = solve(field([120, 900]), algorithm)
    assert status == 'feasible' and chk['valid']
    assert all(r['start_s'] >= 120 for r in chk['time_windows']['tasks'])
    assert any(r['arrival_s'] < r['start_s'] for r in chk['time_windows']['tasks'])


def test_each_task_keeps_its_own_window():
    inst = field([0, 400])
    inst['tasks'][7]['time_window'] = [0, 75]
    routes, chk, *_ , status, _ = solve(inst)
    assert status == 'feasible'
    row = next(r for r in chk['time_windows']['tasks'] if r['label'] == 'T8')
    assert row['latest_s'] == 75 and row['on_time']


@pytest.mark.parametrize('algorithm', NOT_WINDOWED)
def test_an_algorithm_without_windows_refuses_instead_of_ignoring_them(algorithm):
    with pytest.raises(ValueError, match='不适用于带时间窗的任务'):
        solve(field([0, 150]), algorithm)
    routes, chk, *_ = solve(field(), algorithm)
    assert chk['valid'] and 'time_windows' not in chk


def test_the_catalog_says_who_supports_windows_and_the_recommender_listens():
    rows = {a['id']: a for a in catalog() if 'fleet_routing' in a['problem_classes']}
    assert {k: v['limits']['time_windows'] for k, v in rows.items()} == {
        'pyvrp.balanced': 'native', 'ortools.fleet': 'time_dimension', **{k: 'unsupported' for k in NOT_WINDOWED}}
    assert all(v['limits']['time_window_note'] for v in rows.values())
    scenario = {'id': 'x', 'problem_class': 'fleet_routing', 'instance': field([0, 200])}
    advice = recommend(scenario, scenario['instance'])
    assert advice['recommended'] == 'pyvrp.balanced' and set(advice['compare']) == set(WINDOWED)
    assert all(not r['applicable'] and '不支持时间窗' in r['reasons'][0] for r in advice['ranking'] if r['algorithm_id'] in NOT_WINDOWED)
    assert any(f['key'] == 'time_windows' and f['value'] == 8 for f in advice['features'])
    plain = recommend({**scenario, 'instance': field()}, field())
    assert not any(f['key'] == 'time_windows' for f in plain['features']) and plain['recommended'] != 'pyvrp.balanced'


def test_windows_and_speed_limited_ground_do_not_mix_silently():
    with pytest.raises(ValueError, match='地面风险限速'):
        fleet.solve(SCENARIO, field([0, 150]), 'pyvrp.balanced', {'time_limit_s': 1, 'ground_policy': 'risk_adjust'})


def test_the_budget_holds_across_rounds():
    begun = time.monotonic()
    *_, raw, status, _ = solve(field([0, 70], vehicles=1), budget=1.5)
    assert status == 'no_solution' and time.monotonic() - begun < 2.5


def test_a_plan_is_checked_once_per_round(monkeypatch):
    calls = []
    real = fleet.check
    monkeypatch.setattr(fleet, 'check', lambda *a, **k: calls.append(1) or real(*a, **k))
    *_, raw, status, _ = solve(field([0, 300]))
    assert status == 'feasible' and len(calls) == len(raw['time_window_rounds']) == 1


def test_a_later_round_that_finds_nothing_keeps_the_closest_plan(monkeypatch):
    real = fleet._solve
    outcomes = []

    def flaky(scenario, inst, algorithm_id, params, graph=None, margin_s=0.0):
        outcomes.append(margin_s)
        return real(scenario, inst, algorithm_id, params, graph, margin_s) if len(outcomes) == 1 else ([], None, {}, {'reason': 'x'}, 'no_solution', [])
    monkeypatch.setattr(fleet, '_solve', flaky)
    routes, chk, metrics, raw, status, _ = solve(field([0, 70], vehicles=1))
    assert len(outcomes) == 2 and status == 'no_solution' and chk['time_windows']['late'] and raw['rejected_routes']


def test_worker_preserves_budget_protection_with_a_checker_estimate(monkeypatch):
    routes, chk, *_ = solve(field([0, 300]))
    assert chk['time_windows']['execution_estimate']['predicted_makespan_s'] > 0

    def must_not_start(*args, **kwargs):
        pytest.fail('an exhausted optional budget must not start another estimator')
    monkeypatch.setattr(worker.subprocess, 'Popen', must_not_start)
    result = {'status': 'feasible', 'routes': routes, 'check': chk, 'metrics': {}}
    worker.add_execution_estimate(
        {'scenario': {'problem_class': 'fleet_routing'}, 'instance': {}}, result,
        deadline=time.monotonic() - 1,
    )
    assert result['status'] == 'feasible' and result['routes'] == routes
    assert result['check'] == chk
    assert 'predicted_makespan_s' not in result['metrics']
    assert result['metrics']['execution_estimate'] == {
        'status': 'unavailable', 'reason': 'estimate budget exhausted',
    }


def test_an_instance_without_windows_takes_the_old_road():
    routes, chk, metrics, raw, status, _ = solve(field())
    assert status == 'feasible' and 'time_windows' not in chk and 'time_window_rounds' not in raw
    assert not any(k.startswith('time_window') for k in metrics)


# ------------------------------------------------------------------- checker --
def tighten(inst, late):
    return validate_instance('fleet_routing', {**inst, 'tasks': [{**t, 'time_window': [0, late]} for t in inst['tasks']]})


def test_the_checker_works_from_the_geometry_alone():
    inst = field()
    routes = solve(inst)[0]
    assert fleet.check(inst, routes)['valid']
    longest = max(r['arrival_s'] for r in fleet.check(tighten(inst, 900), routes)['time_windows']['tasks'])
    hurried = copy.deepcopy(routes)
    for r in hurried:
        r['time_window_arrivals'] = 'whatever the solver claimed'
    chk = fleet.check(tighten(inst, longest - 5), hurried)
    assert not chk['valid'] and chk['time_windows']['late'][0]['arrival_s'] == longest
    assert '晚于截止' in ' '.join(chk['errors'])


def test_an_arrival_exactly_at_the_deadline_is_on_time():
    inst = field()
    routes = solve(inst)[0]
    longest = max(r['arrival_s'] for r in fleet.check(tighten(inst, 900), routes)['time_windows']['tasks'])
    assert fleet.check(tighten(inst, longest), routes)['valid']


def test_a_route_the_checker_cannot_time_does_not_pass():
    inst = field([0, 300])
    routes = solve(inst)[0]
    routes[0]['polyline'] = []
    chk = fleet.check(inst, routes)
    assert not chk['valid'] and chk['time_windows']['evaluated'] is False
    assert any('时间窗未能校验' in e for e in chk['errors'])


def test_a_task_left_out_is_late_by_definition():
    inst = field([0, 900])
    routes = solve(inst)[0]
    dropped = copy.deepcopy(routes)
    gone = dropped[0]['sequence'].pop()
    dropped[0]['trips'][-1].remove(gone)
    dropped[0]['services'].pop()
    chk = fleet.check(inst, dropped)
    assert any(r['task'] == gone and r['arrival_s'] is None for r in chk['time_windows']['late'])
    assert any('未分配' in e for e in chk['errors'])


def test_no_parking_layout_times_the_routes_and_says_so(monkeypatch):
    inst = field([0, 900])
    routes = solve(inst)[0]

    def no_room(*_):
        raise ValueError('车库附近没有足够的安全停车位')
    monkeypatch.setattr(fieldworld, 'plan_vehicles', no_room)
    chk = fleet.check(inst, routes)
    assert chk['valid'] and chk['time_windows']['model'] == 'route_only' and '停车位' in chk['time_windows']['scope']


def test_paths_with_no_safe_order_cannot_promise_an_arrival(monkeypatch):
    inst = field([0, 900])
    routes = solve(inst)[0]
    monkeypatch.setattr(fc, 'compile_gates', lambda *a, **k: {'feasible': False, 'reason': '找不到无环的通行次序'})
    chk = fleet.check(inst, routes)
    assert not chk['valid'] and chk['time_windows']['evaluated'] is False and '无环' in chk['errors'][-1]


def test_recheck_reports_the_windows_as_their_own_item():
    inst = field([0, 300])
    routes, chk, metrics, raw, status, _ = solve(inst)
    run = {'problem_class': 'fleet_routing', 'state': 'done', 'status': 'feasible', 'instance': inst, 'routes': routes, 'check': chk, 'params': {}}
    result = diagnose.recheck({'instance': inst}, run)
    item = next(i for i in result['items'] if i['id'] == 'time_windows')
    assert result['valid'] and item['ok'] and '全部按时' in item['detail']


def test_the_precheck_catches_what_a_straight_line_already_rules_out():
    inst = field([0, 600])
    inst['tasks'][7]['time_window'] = [0, 5]
    found = diagnose.precheck(inst)
    assert not found['feasible_by_precheck']
    assert next(f for f in found['findings'] if f['code'] == 'deadline_out_of_reach')['tasks'][0].startswith('T8')
    assert diagnose.precheck(field([0, 600]))['feasible_by_precheck']


def test_a_miss_is_diagnosed_by_what_is_proven():
    pre = {'feasible_by_precheck': True, 'findings': []}
    unreachable = {'state': 'done', 'status': 'infeasible', 'raw_result': {'reason': '任务 T8', 'time_window_unreachable': [{'label': 'T8', 'need_s': 40.0, 'latest_s': 10}]}}
    cause = diagnose.classify(unreachable, pre)
    assert cause == {'category': 'task_infeasible', 'evidence': ['任务 T8'], 'code': 'time_window_unreachable'}
    actions = diagnose.repairs('task_infeasible', {**unreachable, 'algorithm': {'id': 'pyvrp.balanced'}}, pre, [], set(), 2)
    assert actions[0]['action'] == 'relax_deadline' and actions[0]['needs_user'] and actions[0]['need_s'] > 40
    late = {'state': 'done', 'status': 'no_solution', 'raw_result': {'reason': '没有找到全部按时的方案：任务 T3'},
            'check': {'time_windows': {'late': [{'arrival_s': 120.0}]}}}
    cause = diagnose.classify(late, pre)
    assert cause['category'] == 'budget_insufficient' and cause['code'] == 'time_window_missed' and 'T3' in cause['evidence'][0]


# ------------------------------------------------------------------ execution --
def tracked(earliest):
    vehicle = {'name': 'a', 'label': 'a', 'color': '#000', 'path': [[0.0, 0.0], [5.0, 0.0], [10.0, 0.0]],
               'tasks': [{'id': 1, 'label': 'T1', 'xy': [5.0, 0.0], 'stop': [5.0, 0.0], 'service_s': 2.0, 'path_s': 5.0, **earliest}]}
    driver = Driver(vehicle, 1.0)
    driver.state, driver.t_start, driver.pose, driver.pose_stamp, driver.s = 'driving', 100.0, (5.0, 0.0, 0.0), 103.0, 5.0
    return driver


def test_the_follower_holds_at_the_stop_until_the_task_opens():
    driver = tracked({'earliest_s': 8.0})
    assert driver.step(103.0) == (0.0, 0.0)
    task = driver.tasks[0]
    assert task['t_sim'] == 103.0 and task['service_started_at'] == 108.0 and driver.dwell_until == 110.0
    driver.pose_stamp = 109.9
    assert driver.step(109.9) == (0.0, 0.0) and driver.state == 'servicing'


def test_the_follower_is_untouched_when_nothing_holds_it():
    driver = tracked({})
    driver.step(103.0)
    assert driver.dwell_until == 105.0 and 'service_started_at' not in driver.tasks[0]
    late = tracked({'earliest_s': 1.0})
    late.step(103.0)
    assert late.dwell_until == 105.0 and 'service_started_at' not in late.tasks[0]


def test_a_task_with_no_work_still_waits_for_its_time():
    driver = tracked({'earliest_s': 8.0})
    driver.tasks[0]['service_s'] = 0.0
    driver.step(103.0)
    assert driver.state == 'servicing' and driver.dwell_until == 108.0


def record(*tasks, origin=1.0):
    return {'execution_started_sim_s': origin, 'tasks': [dict(zip(('id', 'vehicle', 't_sim', 'service_started_at'), t)) for t in tasks]}


def plan_for(window, traffic=None):
    rows = [{'id': 1, 'label': 'T1', 'time_window': window}, {'id': 2, 'label': 'T2', 'time_window': window}]
    return {'instance': {'tasks': rows, 'start_clock': '07:00'}, 'vehicles': [{'name': 'vehicle_1', 'label': '① 号车'}], **({'traffic': traffic} if traffic else {})}


def test_recorded_arrivals_start_at_the_release_and_name_the_late_task():
    out = time_windows.execution(plan_for([0, 50]), record((1, 'vehicle_1', 40.0, None), (2, 'vehicle_1', 61.0, None)))
    assert out['model'] == 'serial' and out['total'] == 2 and out['on_time'] == 1 and out['start_clock'] == '07:00'
    assert out['late'][0]['label'] == 'T2' and out['late'][0]['arrival_s'] == 60 and out['late'][0]['slack_s'] == -10
    assert 'T2' in time_windows.verdict(out) and '① 号车' in time_windows.verdict(out) and '晚 10.0 s' in time_windows.verdict(out)


def test_a_task_never_reached_is_not_on_time():
    out = time_windows.execution(plan_for([0, 50]), record((1, 'vehicle_1', 10.0, None), (2, 'vehicle_1', None, None)))
    assert out['late'][0]['arrival_s'] is None and not out['all_on_time'] and '未到达' in time_windows.late_text(out)


def test_work_begun_before_the_window_is_counted():
    out = time_windows.execution(plan_for([30, 90]), record((1, 'vehicle_1', 11.0, None), (2, 'vehicle_1', 11.0, 31.0)))
    assert out['early_starts'] == 1 and out['tasks'][0]['start_s'] in (10.0, 30.0)


def test_the_prediction_rides_along_for_a_coordinated_run():
    traffic = {'gates': {}, 'arrivals': {'vehicle_1': [{'task': 1, 'arrive_s': 38.5, 'start_s': 38.5}]}}
    out = time_windows.execution(plan_for([0, 50], traffic), record((1, 'vehicle_1', 40.0, None)))
    assert out['model'] == 'coordinated' and next(item for item in out['tasks'] if item['task'] == 1)['planned_arrival_s'] == 38.5


def test_nothing_to_judge_without_windows():
    assert time_windows.execution({'instance': {'tasks': [{'id': 1}]}, 'vehicles': []}, record()) is None
    assert time_windows.compact(None) is None and time_windows.verdict(None) == ''


def test_time_of_day_follows_the_departure():
    assert time_windows.clock('07:30', 5400) == '09:00:00' and time_windows.clock(None, 10) is None


def physics_plan(window, tmp_path):
    from ceres_lab import physics
    inst = validate_instance('fleet_routing', {
        'boundary': [[0, 0], [40, 0], [40, 30], [0, 30]], 'depot': [5, 5],
        'tasks': [{'id': 1, 'xy': [20, 8], 'label': 'T1', 'time_window': window}, {'id': 2, 'xy': [30, 20], 'label': 'T2', 'time_window': window}],
        'vehicles': {'count': 2, 'capacity': 1, 'travel': 'free', 'speed_mps': 1, 'trips': 'single'}})
    routes, chk, _, _, status, _ = solve(inst, budget=1.0)
    assert status == 'feasible', chk
    run = {'problem_class': 'fleet_routing', 'state': 'done', 'status': status, 'routes': routes, 'check': chk, 'instance': inst, 'params': {},
           'run_id': 'run_x', 'scenario_id': 's', 'algorithm': {'name': 'x'}}
    return physics.plan_from_run(run, tmp_path)


def test_mujoco_arrives_when_the_model_says_and_waits_for_the_earliest_time(tmp_path):
    plan = fc.apply(physics_plan([30, 120], tmp_path))
    assert all('earliest_s' in t for v in plan['vehicles'] for t in v['tasks'])
    result = mujoco_field.execute(plan)
    out = time_windows.execution(plan, result)
    assert result['state'] == 'succeeded' and out['model'] == 'coordinated'
    assert out['all_on_time'] and out['early_starts'] == 0
    first = min(out['tasks'], key=lambda r: r['arrival_s'])
    assert first['arrival_s'] < 30 <= first['start_s'] < 30.2
    assert all(abs(r['arrival_s'] - r['planned_arrival_s']) < 3.5 for r in out['tasks'])


def test_one_by_one_execution_shows_the_lateness_the_plan_avoids(tmp_path):
    plan = physics_plan([0, 40], tmp_path)
    serial = time_windows.execution(plan, mujoco_field.execute(plan))
    together = fc.apply(plan)
    concurrent = time_windows.execution(together, mujoco_field.execute(together))
    assert concurrent['all_on_time'] and concurrent['model'] == 'coordinated'
    assert serial['model'] == 'serial' and not serial['all_on_time'] and serial['late'][0]['label'] in ('T1', 'T2')


def test_the_clock_judges_the_execution_the_plan_describes():
    on_time, late = {'all_on_time': True, 'late': []}, {'all_on_time': False, 'total': 2, 'on_time': 1, 'late': [{'label': 'T2', 'vehicle': '车', 'arrival_s': 60, 'slack_s': -10}]}
    together = {'run_id': 'r', 'state': 'succeeded', 'check_valid': True, 'time_windows': late,
                'coordinated': {'state': 'succeeded', 'check_valid': True, 'time_windows': on_time}}
    assert _executed(together)
    missed = {**together, 'coordinated': {**together['coordinated'], 'time_windows': late}}
    assert not _executed(missed) and '任务 T2' in _execution_fault(missed)
    alone = {'run_id': 'r', 'state': 'succeeded', 'check_valid': True, 'time_windows': late}
    assert not _executed(alone)
    plain = {'run_id': 'r', 'state': 'succeeded', 'check_valid': True}
    assert _executed(plain)


# ---------------------------------------------------------------- whole agent --
@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    lab = Lab(Config(runtime=tmp_path, background=False))
    lab.start()
    yield lab
    lab.shutdown()


def ask(runner, text, sid=None):
    sid = sid or runner.create({'mode': 'rule', 'auto_confirm': True})['session_id']
    runner.message(sid, text)
    return sid, settle(runner, sid)


def settle(runner, sid):
    end = time.monotonic() + 120
    while time.monotonic() < end:
        session = runner.store.get(sid)
        if session['state'] != 'running':
            return session
        time.sleep(.05)
    raise AssertionError('session stuck')


BASE = '60×40米果园，2台车，8个采样点，每车容量4'


def test_a_sentence_with_a_deadline_goes_all_the_way_through(lab):
    runner = AgentRunner(lab)
    sid, s = ask(runner, BASE + '，早上七点出发，每个点要在七点半前送达')
    assert s['state'] == 'done', s.get('error')
    assert not s['contract']['unsupported'] and tc.window(s['contract']) == [0.0, 1800.0]
    scenario = next(c for c in s['tool_calls'] if c['name'] == 'build_scenario')['result']
    assert scenario['time_window'] == [0.0, 1800.0] and scenario['start_clock'] == '07:00'
    solved = next(c for c in s['tool_calls'] if c['name'] == 'solve')
    assert set(solved['arguments']['algorithms']) == set(WINDOWED)
    evidence = s['report']['evidence']
    assert evidence['time_window'] == [0.0, 1800.0]
    assert evidence['best_verified']['time_windows']['all_on_time'] and evidence['best_executable']
    row = evidence['physics'][0]
    assert row['coordinated']['time_windows']['model'] == 'coordinated' and row['coordinated']['time_windows']['all_on_time']
    assert row['time_windows']['model'] == 'serial'
    assert '全部按时' in s['report']['narrative'] and '实测到达' in s['report']['narrative']
    verified = next(c for c in s['tool_calls'] if c['name'] == 'verify_run')['result']
    assert verified['time_windows']['all_on_time'] and any(i['id'] == 'time_windows' for i in verified['recheck']['items'])
    full = lab.physics.get(row['coordinated']['physics_run_id'])
    assert len(full['time_windows']['tasks']) == 8 and all('planned_arrival_s' in r for r in full['time_windows']['tasks'])
    run = lab.store.get_run(evidence['best_verified']['run_id'])
    assert run['instance']['start_clock'] == '07:00' and run['check']['time_windows']['start_clock'] == '07:00'


def test_a_clock_deadline_asks_when_the_vehicles_leave(lab):
    runner = AgentRunner(lab)
    sid, s = ask(runner, BASE + '，每个点要在上午九点前送达')
    assert s['state'] == 'awaiting_user' and [q['field'] for q in s['pending']['questions']] == ['start_clock']
    assert not s['run_ids']
    sid, s = ask(runner, '八点半', sid)
    assert s['state'] == 'done', s.get('error')
    assert tc.value(s['contract'], 'start_clock') == '08:30' and tc.window(s['contract']) == [0.0, 1800.0]
    assert s['report']['evidence']['best_verified']['time_windows']['all_on_time']


def test_a_departure_after_the_deadline_is_asked_again(lab):
    runner = AgentRunner(lab)
    sid, s = ask(runner, BASE + '，每个点要在上午九点前送达，九点半出发')
    assert s['state'] == 'awaiting_user' and s['pending']['questions'][0]['field'] == 'start_clock'


def test_a_deadline_that_cannot_be_met_says_which_tasks_and_keeps_no_plan(lab):
    runner = AgentRunner(lab)
    sid, s = ask(runner, BASE + '，每个点要在出发后 70 秒内送达')
    assert s['state'] == 'done', s.get('error')
    evidence = s['report']['evidence']
    assert evidence['best_verified'] is None and evidence['best_executable'] is None
    assert any(r['status'] == 'no_solution' and r['time_windows']['late'] for r in evidence['runs'])
    named = next(r for r in evidence['runs'] if r['time_windows'])['time_windows']['late'][0]
    assert f"任务 {named['label']}" in s['report']['narrative'] and '目前没有通过独立复核的方案' in s['report']['narrative']
    assert not any(c['name'] == 'physics_check' for c in s['tool_calls'])


def test_an_impossible_deadline_asks_to_move_it_and_the_task_is_named(lab):
    runner = AgentRunner(lab)
    sid, s = ask(runner, BASE + '，每个点要在出发后 5 秒内送达')
    assert s['state'] == 'awaiting_approval', (s['state'], s.get('error'))
    pending = s['pending']
    assert pending['kind'] == 'contract_change' and [c['key'] for c in pending['changes']] == ['task_deadline']
    assert '直达至少' in pending['reason']
    precheck = next(c for c in s['tool_calls'] if c['name'] == 'build_scenario')['result']['precheck']
    assert any(f['code'] == 'deadline_out_of_reach' for f in precheck['findings'])
    diagnosed = next(c for c in s['tool_calls'] if c['name'] == 'diagnose_run')['result']
    assert diagnosed['diagnosis'] == 'task_infeasible' and '任务 ' in diagnosed['evidence'][0]


def test_per_task_windows_are_reported_not_solved(lab):
    runner = AgentRunner(lab)
    sid, s = ask(runner, BASE + '，A 区的点要在九点前完成')
    assert s['state'] == 'done', s.get('error')
    assert s['contract']['unsupported'] == ['A 区的点要在九点前完成']
    assert 'A 区的点要在九点前完成' in s['report']['narrative']
    assert s['report']['evidence']['time_window'] is None


def test_a_changed_deadline_rebuilds_the_task_and_a_cleared_one_removes_it(lab):
    from ceres_lab.scenarios import get_scenario
    runner = AgentRunner(lab)
    sid, s = ask(runner, BASE + '，每个点要在出发后 30 分钟内送达')
    assert s['state'] == 'done', s.get('error')
    first = s['scenario_id']
    assert get_scenario(first)['instance']['tasks'][0]['time_window'] == [0.0, 1800.0]
    runner.edit_contract(sid, {'task_deadline': None})
    runner.control(sid, 'confirm')
    s = settle(runner, sid)
    assert s['state'] == 'done', s.get('error')
    assert 'time_window' not in get_scenario(s['scenario_id'])['instance']['tasks'][0] and s['report']['evidence']['time_window'] is None


class ScriptedModel:
    """Stands in for a model: reads the request into the contract, then stops, so the rule flow finishes the task."""

    def __init__(self, fields, unsupported=()):
        self.calls = [('update_task_contract', {'fields': fields, 'evidence': {'task_deadline': '半小时内'}, 'unsupported': list(unsupported)})]

    def __call__(self, provider, messages, specs, **kwargs):
        calls = [self.calls.pop(0)] if self.calls else []
        return llm.Response(content='' if calls else '已按工具记录结束当前阶段。', usage={}, latency_s=0, attempts=1, finish_reason='stop', model='scripted', provider='scripted', cost=0, currency='CNY',
                            tool_calls=[{'id': f'c{i}', 'name': n, 'arguments': a, 'parse_error': None} for i, (n, a) in enumerate(calls)])


def test_the_model_fills_the_same_window_and_keeps_what_it_cannot_say(lab):
    fields = {'task_count': 8, 'vehicle_count': 2, 'field_size_m': [60, 40], 'vehicle_capacity': 4, 'task_deadline': '1800s', 'seed': 7}
    runner = AgentRunner(lab, chat=ScriptedModel(fields, ['A 区八点前']), providers=[llm.Provider(id='scripted', kind='openai', model='scripted', base_url='http://127.0.0.1:9')])
    sid = runner.create({'mode': 'llm', 'auto_confirm': True})['session_id']
    runner.message(sid, '果园采样，半小时内送达，A 区八点前')
    s = settle(runner, sid)
    assert s['state'] == 'done', s.get('error')
    assert s['contract']['fields']['task_deadline']['source'] == 'llm' and tc.window(s['contract']) == [0.0, 1800.0]
    assert s['contract']['unsupported'] == ['A 区八点前']
    assert s['report']['evidence']['best_verified']['time_windows']['all_on_time']


def test_the_map_composer_applies_a_complete_request_and_reports_an_incomplete_one():
    done = compose('60×40米果园，2台车，8个点，早上七点出发，每个点要在七点半前送达', 3)
    assert all(t['time_window'] == [0.0, 1800.0] for t in done['instance']['tasks']) and done['instance']['start_clock'] == '07:00'
    assert validate_instance('fleet_routing', done['instance'])
    assert any(c['key'] == 'deadline' and c['value'] == '07:30' for c in done['compose']['cards'])
    open_ended = compose('60×40米果园，2台车，8个点，每个点要在上午九点前送达', 3)
    assert not any('time_window' in t for t in open_ended['instance']['tasks'])
    assert open_ended['compose']['generation']['status'] == 'partial' and '时间窗未写入任务' in open_ended['compose']['generation']['shortfalls'][0]
    scoped = compose('60×40米果园，2台车，8个点，A 区九点前完成', 3)
    assert '未进入任务：A 区九点前完成' in scoped['compose']['generation']['shortfalls']


@pytest.mark.parametrize('records', [record(), record((1, 'vehicle_1', 10.0, None))])
def test_absent_task_records_are_not_counted_as_success(records):
    out = time_windows.execution(plan_for([0, 50]), records)
    assert out['total'] == 2 and not out['all_on_time']
    assert len(out['late']) == 2 - len(records['tasks'])
    assert all(item['arrival_s'] is None for item in out['late'])
