"""Seeded perturbations, repeated runs and what they add up to."""
import copy
import json
import math
import subprocess
import sys
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from urp_lab import field_coordination, fieldcheck, gateway, mujoco_field, physics, physics_stats, physics_worker
from urp_lab.algorithm_stats import physical_record
from urp_lab.gateway import Config, Lab
from urp_lab.physics import PhysicsError, batch_cpus, sample_seed

from test_physics import plan_of, solved

SAME_RUN = ('state', 'errors', 'metrics', 'trails', 'tasks', 'contacts', 'traffic', 'perturbation', 'model_sha256', 'rigid_body_poses', 'control_traces')


def same_run(a: dict, b: dict) -> bool:
    return all(a[k] == b[k] for k in SAME_RUN)


def without_time(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != 'wall_s'} for r in rows]


# --- one draw ------------------------------------------------------------------------------------------------------------------
def test_the_same_seed_gives_exactly_the_same_run(tmp_path):
    plan = plan_of(tmp_path, vehicles=2)
    a = mujoco_field.execute(mujoco_field.perturb(plan, 7))
    b = mujoco_field.execute(mujoco_field.perturb(copy.deepcopy(plan), 7))
    assert a['perturbation'] and same_run(a, b)


def test_another_seed_is_another_field(tmp_path):
    plan = plan_of(tmp_path, vehicles=2)
    a, b = mujoco_field.perturb(plan, 1), mujoco_field.perturb(plan, 2)
    assert a['perturbation']['ground_friction'] != b['perturbation']['ground_friction']
    assert a['perturbation']['vehicles'] != b['perturbation']['vehicles']


def test_intensity_zero_is_the_ideal_model_to_the_last_bit(tmp_path):
    plan = plan_of(tmp_path, vehicles=2)
    ideal, zero = mujoco_field.execute(plan), mujoco_field.execute(mujoco_field.perturb(plan, 5, 0.))
    assert zero['model_sha256'] == ideal['model_sha256']
    assert zero['trails'] == ideal['trails'] and zero['metrics'] == ideal['metrics']


def test_the_draw_is_written_into_the_record(tmp_path):
    plan = plan_of(tmp_path)
    actual = mujoco_field.perturb(plan, 3, 1.)
    record = mujoco_field.execute(actual)
    drawn = record['perturbation']
    assert drawn['schema'] == mujoco_field.PERTURBATION and drawn['seed'] == 3 and drawn['intensity'] == 1.
    assert drawn['factors'] == list(mujoco_field.FACTORS)
    assert record['physics']['ground_friction'] == drawn['ground_friction']
    assert set(drawn['vehicles']) == {v['name'] for v in plan['vehicles']}
    assert set(drawn['service_s']) == {str(t['id']) for v in plan['vehicles'] for t in v['tasks']}
    assert json.loads(json.dumps(record['perturbation'], allow_nan=False)) == drawn
    assert mujoco_field.execute(plan)['perturbation'] is None


def test_two_plans_of_one_field_meet_the_same_draws(tmp_path):
    """Comparing plans under one seed is a paired comparison only if the draws do not depend on the plan."""
    one, other = plan_of(tmp_path / 'a'), plan_of(tmp_path / 'b')
    other['vehicles'] = copy.deepcopy(other['vehicles'])[::-1]
    for t in other['vehicles'][0]['tasks']:
        t['service_s'] = 1.
    a, b = mujoco_field.perturb(one, 4)['perturbation'], mujoco_field.perturb(other, 4)['perturbation']
    assert a['ground_friction'] == b['ground_friction'] and a['vehicles'] == b['vehicles']
    assert set(a['service_s']) == set(b['service_s'])
    assert mujoco_field.perturb({**one, 'vehicles': one['vehicles'] + [{**one['vehicles'][0], 'name': 'extra', 'tasks': []}]}, 4)['perturbation']['ground_friction'] == a['ground_friction']


def test_a_seed_keeps_its_direction_as_the_intensity_grows(tmp_path):
    plan = plan_of(tmp_path)
    draws = [mujoco_field.perturb(plan, 11, k)['perturbation'] for k in (.25, .5, 1., 1.5)]
    friction = [d['ground_friction'] for d in draws]
    load = [next(iter(d['vehicles'].values()))['payload_kg'] for d in draws]
    assert friction == sorted(friction, reverse=True) and load == sorted(load)
    stops = [sum(d['service_s'].values()) for d in draws]
    assert stops == sorted(stops), 'a stop the plan budgets nothing for only ever gains time'


def test_each_factor_switches_on_alone_with_the_value_it_has_among_all_of_them(tmp_path):
    plan = plan_of(tmp_path)
    together = mujoco_field.perturb(plan, 6)['perturbation']
    alone = mujoco_field.perturb(plan, 6, 1., ['ground_friction'])
    assert alone['perturbation']['ground_friction'] == together['ground_friction']
    v = next(iter(alone['perturbation']['vehicles'].values()))
    assert v['payload_kg'] == 0 and set(v['wheel_radius_m'].values()) == {plan['physics']['wheel']['radius_m']}
    assert set(alone['perturbation']['service_s'].values()) <= {0., 1.}
    load = mujoco_field.perturb(plan, 6, 1., ['payload'])['perturbation']
    assert load['ground_friction'] == plan['physics']['ground_friction']
    assert next(iter(load['vehicles'].values()))['payload_kg'] == next(iter(together['vehicles'].values()))['payload_kg'] > 0


def test_every_draw_stays_inside_its_stated_range(tmp_path):
    plan = plan_of(tmp_path, vehicles=2)
    nominal = plan['physics']['wheel']['radius_m']
    for seed in range(200):
        d = mujoco_field.perturb(plan, seed, 1.5)['perturbation']
        assert 1 - mujoco_field.FRICTION_DROP * 1.5 <= d['ground_friction'] <= 1
        for v in d['vehicles'].values():
            assert 0 <= v['payload_kg'] <= mujoco_field.PAYLOAD_KG * 1.5
            assert all(abs(r / nominal - 1) <= mujoco_field.WHEEL_TOLERANCE * 1.5 for r in v['wheel_radius_m'].values())
        assert all(s >= 0 for s in d['service_s'].values())
    assert mujoco_field.perturb(plan, 3, mujoco_field.MAX_INTENSITY)['physics']['ground_friction'] >= mujoco_field.MIN_FRICTION


def test_only_the_world_changes_not_the_plan_or_its_order(tmp_path):
    plan = field_coordination.apply(plan_of(tmp_path, vehicles=2))
    frozen = copy.deepcopy(plan)
    actual = mujoco_field.perturb(plan, 9, 1.5)
    assert plan == frozen, 'the frozen plan is not touched'
    assert [v['path'] for v in actual['vehicles']] == [v['path'] for v in plan['vehicles']]
    assert actual['traffic'] == plan['traffic'] and actual['planned'] == plan['planned']
    assert actual['physics']['wheel'] == plan['physics']['wheel'], 'the controller keeps the nominal radius'


def test_outside_the_model_the_request_is_refused(tmp_path):
    plan = plan_of(tmp_path)
    with pytest.raises(ValueError):
        mujoco_field.perturb(plan, 1, mujoco_field.MAX_INTENSITY + .1)
    with pytest.raises(ValueError):
        mujoco_field.perturb(plan, 1, -.1)
    with pytest.raises(ValueError):
        mujoco_field.perturb(plan, 1, 1., ['rain'])
    with pytest.raises(mujoco_field.Unsupported):
        mujoco_field.execute(mujoco_field.perturb({**plan, 'execution': {'ground_preset': 'mixed'}}, 1))


def test_a_stop_the_plan_budgets_nothing_for_still_takes_time_and_the_check_reads_the_real_time(tmp_path):
    plan = plan_of(tmp_path)
    ideal = mujoco_field.execute(plan)
    actual = mujoco_field.perturb(plan, 2, 1., ['service_time'])
    held = mujoco_field.execute(actual)
    assert held['state'] == 'succeeded'
    assert held['metrics']['makespan_s'] > ideal['metrics']['makespan_s'] + .5 * sum(actual['perturbation']['service_s'].values())
    errors, _ = fieldcheck.track_check(actual, held)
    assert not errors
    late = copy.deepcopy(actual)
    late['vehicles'][0]['tasks'][0]['service_s'] += 100
    assert fieldcheck.track_check(late, held)[0], 'the dwell is checked against the drawn time, not against the plan'


def test_a_wheel_larger_than_the_controller_assumes_drives_faster(tmp_path):
    plan = plan_of(tmp_path)
    radius = plan['physics']['wheel']['radius_m']

    def on_wheels(scale):
        vehicles = [{**v, 'actual': {'wheel_radius_m': {'left': radius * scale, 'right': radius * scale}}} for v in plan['vehicles']]
        return mujoco_field.execute({**plan, 'vehicles': vehicles})['metrics']['makespan_s']

    assert on_wheels(1.04) < mujoco_field.execute(plan)['metrics']['makespan_s'] < on_wheels(.96)


def test_the_body_stays_level_at_the_most_load_and_the_worst_wheels(tmp_path):
    """The servo gain is fixed, not fitted: a chattering contact would show as roll, pitch or a bouncing body."""
    plan = plan_of(tmp_path)
    actual = mujoco_field.perturb(plan, 3, mujoco_field.MAX_INTENSITY, ['payload', 'wheel_radius'])
    record = mujoco_field.execute(actual)
    assert record['state'] == 'succeeded'
    pose = np.array(next(iter(record['rigid_body_poses'].values()))[100:])
    assert np.abs(pose[:, 4:6]).max() < .01 and np.ptp(pose[:, 3]) < .01


def test_a_heavier_vehicle_uses_more_energy_in_the_books(tmp_path):
    plan = plan_of(tmp_path)
    heavy = mujoco_field.perturb(plan, 1, 2., ['payload'])
    assert mujoco_field.execute(heavy)['metrics']['energy_wh'] > mujoco_field.execute(plan)['metrics']['energy_wh']


def test_the_worker_reduces_a_draw_to_a_row_and_keeps_the_seed(tmp_path):
    plan = field_coordination.apply(plan_of(tmp_path, vehicles=2))
    row = physics_worker.sample(plan, 12, .5)
    assert row['seed'] == 12 and row['state'] == 'succeeded' and row['valid'] and row['reason'] is None
    assert row['makespan_s'] > 0 and row['min_gap_m'] > 0 and row['perturbation']['seed'] == 12
    json.dumps(row, allow_nan=False)
    assert physics_worker.sample(plan, 12, .5) == {**row, 'wall_s': pytest.approx(row['wall_s'], abs=100)}


# --- what a run reduces to -------------------------------------------------------------------------------------------------------
def held_record(rolled_m, *, released=True):
    """A vehicle held at s=10 of a gate at 9.9, rolling on by ``rolled_m`` before it stops."""
    trail = [[t / 10, 10 + min(t / 10, 1) * rolled_m, 0., 0.] for t in range(30)]
    events = [{'event': 'hold', 'vehicle': 'a', 't_sim': 0., 's': 10., 'gate_s': 9.9, 'after': []}]
    if released:
        events.append({'event': 'release', 'vehicle': 'a', 't_sim': 2.5, 'wait_s': 2.5})
    return {'traffic': {'stop_m': .45}}, {'traffic': {'events': events}, 'trails': {'a': trail}}


def test_a_held_vehicle_that_stops_short_of_the_place_keeps_a_margin():
    assert physics_stats.gate_margin(*held_record(.2)) == pytest.approx(9.9 + .45 - 10 - .2)


def test_a_held_vehicle_that_rolls_into_the_place_breaks_the_order():
    assert physics_stats.gate_margin(*held_record(.6)) == pytest.approx(9.9 + .45 - 10 - .6) and physics_stats.gate_margin(*held_record(.6)) < 0
    assert physics_stats.gate_margin(*held_record(.6, released=False)) < 0, 'a run can end with the vehicle still held'


def test_no_hold_means_no_margin_to_report():
    assert physics_stats.gate_margin({'traffic': {'stop_m': .45}}, {'traffic': {'events': []}, 'trails': {}}) is None
    assert physics_stats.gate_margin({'traffic': {}}, {'traffic': {'events': held_record(.1)[1]['traffic']['events']}, 'trails': {}}) is None


def test_the_margin_ignores_the_jitter_of_a_vehicle_at_rest():
    plan, result = held_record(.2)
    trail = result['trails']['a']
    trail += [[3. + i / 50, trail[-1][1] + (.0005 if i % 2 else 0), 0., 0.] for i in range(2000)]
    result['traffic']['events'][1]['t_sim'] = 50.
    assert physics_stats.gate_margin(plan, result) == pytest.approx(9.9 + .45 - 10 - .2, abs=.002)


@pytest.mark.parametrize('errors, reason', [
    (['车间距离保护：a/b 间距 1.1 m；全部停车', '甲 未完整跟踪计划路径或未回到终点', '甲 任务 3 未完成规定的作业停留时间'], 'vehicle_guard'),
    (['a 执行失败，全部停车：60 秒仿真时间内未取得路径进展'], 'no_progress'),
    (['MuJoCo 记录到车辆与实体障碍或其他车辆接触', '甲 的实测轨迹圆足迹侵入障碍/作物或越出边界'], 'contact'),
    (['执行超时：仿真时间超过 400 s'], 'timeout'),
    (['甲 的实测轨迹圆足迹侵入障碍/作物或越出边界'], 'footprint'),
    (['车间实测时间轨迹的足迹净空或采样完整性检查未通过'], 'separation'),
    (['甲 未完整跟踪计划路径或未回到终点'], 'tracking'),
    (['甲 任务 3 未完成规定的作业停留时间'], 'service'),
    (['终点发出停车命令后，实测速度未在 5 秒仿真时间内稳定'], 'stop'),
    (['vehicle_1 终点实测速度未稳定停止'], 'stop'),
    (['vehicle_1 在线累计里程与保存的真值轨迹不一致'], 'record'),
    (['vehicle_1 的 MuJoCo 状态发散'], 'diverged'),
    (['物理执行进程异常退出（退出码 -11）'], 'worker_crash'),
    (['没见过的情况'], 'other'),
])
def test_a_failure_is_counted_once_under_its_first_cause(errors, reason):
    assert physics_stats.primary_reason(errors) == reason
    assert reason in physics_stats.REASON_LABELS


# --- what a set of runs says ----------------------------------------------------------------------------------------------------
def test_quantiles_match_the_reference_implementation():
    values = list(np.random.default_rng(0).normal(100, 15, 37))
    for q in (0, .1, .5, .9, 1):
        assert physics_stats.quantile(values, q) == pytest.approx(float(np.quantile(values, q)))
    assert physics_stats.quantile([3.], .9) == 3.


def test_a_clean_sweep_of_sixteen_runs_supports_about_eighty_percent_not_certainty():
    assert physics_stats.wilson(16, 16) == pytest.approx([.8064, 1.], abs=1e-3)
    low, high = physics_stats.wilson(8, 16)
    assert low == pytest.approx(.28, abs=.01) and high == pytest.approx(.72, abs=.01)
    assert physics_stats.wilson(0, 0) is None


def run(index, valid=True, makespan=100., gap=.8, **more):
    return {'index': index, 'seed': index, 'state': 'succeeded' if valid else 'failed', 'valid': valid, 'reason': None if valid else more.pop('reason', 'vehicle_guard'),
            'makespan_s': makespan, 'min_gap_m': gap, 'held_s': 1., 'gate_margin_m': .2, **more}


def test_the_distribution_is_over_the_runs_it_says_it_is_over():
    rows = [run(i, makespan=100. + i) for i in range(10)]
    rows += [run(10, False, makespan=500., gap=.15), run(11, False, makespan=None, gap=None, reason='timeout', gate_margin_m=-.3)]
    rows += [{'index': 12, 'seed': 12, 'state': 'cut_off'}, {'index': 13, 'seed': 13, 'state': 'not_run'}]
    s = physics_stats.summarize(rows, 14)
    assert (s['requested'], s['finished'], s['cut_off'], s['passed']) == (14, 12, 2, 10)
    assert s['success_rate'] == pytest.approx(10 / 12, abs=1e-4)
    assert s['makespan_s']['n'] == 10 and s['makespan_s']['max'] == 109, 'a failed run has no finish'
    assert s['makespan_s']['median'] == pytest.approx(104.5) and s['makespan_s']['p90'] == pytest.approx(108.1)
    assert s['min_gap_m']['n'] == 11 and s['min_gap_m']['min'] == .15, 'the closest approaches are in the failed runs'
    assert s['order_violations'] == 1 and s['gate_margin_m']['min'] == -.3
    assert [(f['reason'], f['count']) for f in s['failures']] == [('vehicle_guard', 1), ('timeout', 1)]
    assert all(f['label'] for f in s['failures'])
    assert '仿真' in s['scope']


def test_an_empty_batch_claims_nothing():
    s = physics_stats.summarize([{'index': 0, 'seed': 0, 'state': 'cut_off'}], 1)
    assert s['success_rate'] is None and s['success_ci95'] is None and s['makespan_s']['median'] is None and s['failures'] == []


def test_a_cut_off_run_is_neither_a_pass_nor_a_failure():
    done = [run(i) for i in range(3)]
    s = physics_stats.summarize(done + [{'index': 3, 'seed': 3, 'state': 'cut_off'}], 4)
    assert s['success_rate'] == 1 and s['cut_off'] == 1 and s['failures'] == []


def test_two_plans_are_compared_draw_by_draw():
    a = [run(0, makespan=100.), run(1, makespan=90.), run(2, makespan=120.), run(3, False, makespan=None), run(4, makespan=80.), {'index': 5, 'seed': 5, 'state': 'cut_off'}]
    b = [run(0, makespan=110.), run(1, makespan=80.), run(2, makespan=130.), run(3, makespan=70.), run(4, False, makespan=None), run(5, makespan=70.)]
    d = physics_stats.paired(a, b)
    assert (d['pairs'], d['both_passed'], d['only_a_passed'], d['only_b_passed']) == (5, 3, 1, 1)
    assert (d['a_faster'], d['b_faster'], d['median_delta_s']) == (2, 1, -10)
    assert physics_stats.paired([], b) == {'pairs': 0, 'both_passed': 0, 'only_a_passed': 0, 'only_b_passed': 0, 'a_faster': 0, 'b_faster': 0, 'median_delta_s': None}


# --- a batch ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def lab(tmp_path, monkeypatch):
    lab = Lab(Config(runtime=tmp_path / 'runtime', background=False))
    inst, routes, _ = solved(vehicles=2)
    record = {'run_id': 'run_b', 'scenario_id': 'sc', 'problem_class': 'fleet_routing', 'state': 'done', 'status': 'feasible', 'check': {'valid': True},
              'instance': inst, 'routes': routes, 'params': {}, 'algorithm': {'name': '扫描法'}}
    monkeypatch.setattr(lab.store, 'get_run', lambda rid: record if rid == 'run_b' else None)
    yield lab
    lab.shutdown()


def finished(lab, rec, timeout_s=90):
    done = lab.physics.wait(rec['physics_run_id'], timeout_s=timeout_s)
    assert done['state'] != 'running', 'the batch did not end'
    return done


@pytest.fixture
def one_core(monkeypatch):
    monkeypatch.setattr(physics, 'batch_cpus', lambda: [8])


def test_a_batch_gives_a_distribution_and_keeps_what_each_run_drew(lab):
    rec = lab.physics.submit_batch(run_id='run_b', traffic='coordinated', runs=4, seed=3, intensity=1.)
    assert rec['state'] == 'running' and rec['kind'] == 'batch' and rec['batch']['workers'] >= 1
    done = finished(lab, rec)
    s = done['summary']
    assert done['state'] == 'succeeded' and s['finished'] == 4 and s['passed'] == 4 and s['makespan_s']['n'] == 4
    assert s['makespan_s']['min'] <= s['makespan_s']['median'] <= s['makespan_s']['p90'] <= s['makespan_s']['max']
    assert s['min_gap_m']['n'] == 4 and s['success_ci95'][1] == 1
    assert [r['seed'] for r in done['samples']] == [sample_seed(3, i) for i in range(4)]
    assert done['predicted_makespan_s'] > 0 and done['engine_version']
    full = lab.physics.get(rec['physics_run_id'])['samples']
    assert all(r['perturbation']['seed'] == r['seed'] for r in full), 'the drawn values stay with the batch'


def test_a_batch_repeats_exactly_however_many_processes_run_it(lab, monkeypatch):
    monkeypatch.setattr(physics, 'batch_cpus', lambda: [8, 9, 10, 11])
    wide = finished(lab, lab.physics.submit_batch(run_id='run_b', traffic='coordinated', runs=4, seed=8))
    monkeypatch.setattr(physics, 'batch_cpus', lambda: [8])
    narrow = finished(lab, lab.physics.submit_batch(run_id='run_b', traffic='coordinated', runs=4, seed=8))
    assert (wide['batch']['workers'], narrow['batch']['workers']) == (4, 1)
    assert without_time(lab.physics.get(wide['physics_run_id'])['samples']) == without_time(lab.physics.get(narrow['physics_run_id'])['samples'])
    assert wide['summary'] == narrow['summary']


def test_two_batch_seeds_share_no_draw(lab):
    assert {sample_seed(1, i) for i in range(physics.BATCH_MAX_RUNS)}.isdisjoint(sample_seed(2, i) for i in range(physics.BATCH_MAX_RUNS))


def test_one_at_a_time_and_all_at_once_both_run_and_the_second_is_shorter(lab):
    serial = lab.physics.submit_batch(run_id='run_b', traffic='serial', runs=2, seed=1)
    together = lab.physics.submit_batch(run_id='run_b', traffic='coordinated', runs=2, seed=1)
    a, b = finished(lab, serial), finished(lab, together)
    assert a['summary']['passed'] == b['summary']['passed'] == 2
    assert b['summary']['makespan_s']['max'] < a['summary']['makespan_s']['min']
    assert a['predicted_makespan_s'] is None and b['predicted_makespan_s'], 'only an ordered plan has a prediction'


def test_a_cancelled_batch_stops_its_processes_and_keeps_what_finished(lab, one_core):
    rec = lab.physics.submit_batch(run_id='run_b', traffic='coordinated', runs=12, seed=2)
    rid = rec['physics_run_id']
    end = time.monotonic() + 60
    while (lab.store.get_physics_run(rid).get('progress') or {}).get('runs_done', 0) < 1 and time.monotonic() < end:
        time.sleep(.02)
    lab.physics.cancel(rid)
    done = finished(lab, rec)
    assert done['state'] == 'canceled' and done['errors'] == ['已取消']
    s = done['summary']
    assert 1 <= s['finished'] < 12 and s['cut_off'] == 12 - s['finished'] and s['failures'] == []
    assert not lab.physics._procs and not lab.physics._batches, 'no process outlives the batch'
    assert {r['state'] for r in done['samples']} <= {'succeeded', 'cut_off', 'not_run'}


def test_the_wall_limit_ends_the_batch_and_says_how_far_it_got(lab, one_core):
    rec = lab.physics.submit_batch(run_id='run_b', traffic='coordinated', runs=12, seed=2, wall_limit_s=4)
    started = time.monotonic()
    done = finished(lab, rec)
    assert time.monotonic() - started < 15
    s = done['summary']
    assert done['state'] == 'truncated' and s['finished'] < 12 and s['cut_off'] == 12 - s['finished']
    assert s['failures'] == [], 'runs stopped by the limit say nothing about the plan'
    assert '4 s' in done['errors'][0] and f"{s['finished']}/12" in done['errors'][0]


def test_a_process_that_dies_is_a_counted_result_not_a_lost_run(lab, monkeypatch, one_core):
    def dies(rec, index):
        with (lab.physics._dir(rec['physics_run_id']) / 'samples' / f'{index:03d}.log').open('wb') as err:
            return subprocess.Popen([sys.executable, '-c', 'import sys; sys.stderr.write("segfault somewhere\\n"); sys.exit(7)'], stderr=err)

    monkeypatch.setattr(lab.physics, '_sample_process', dies)
    done = finished(lab, lab.physics.submit_batch(run_id='run_b', traffic='coordinated', runs=2, seed=1))
    assert done['state'] == 'succeeded', 'the batch itself ended normally'
    s = done['summary']
    assert s['finished'] == 2 and s['passed'] == 0 and s['failures'] == [{'reason': 'worker_crash', 'label': '进程异常退出', 'count': 2}]
    full = lab.physics.get(done['physics_run_id'])['samples']
    assert 'segfault somewhere' in full[0]['errors'] and '退出码 7' in full[0]['errors'][0]


def test_shutting_down_closes_a_running_batch_while_the_store_is_still_open(tmp_path, monkeypatch):
    lab = Lab(Config(runtime=tmp_path / 'rt', background=False))
    inst, routes, _ = solved(vehicles=2)
    record = {'run_id': 'run_b', 'scenario_id': 'sc', 'problem_class': 'fleet_routing', 'state': 'done', 'status': 'feasible', 'check': {'valid': True},
              'instance': inst, 'routes': routes, 'params': {}, 'algorithm': {'name': '扫描法'}}
    monkeypatch.setattr(lab.store, 'get_run', lambda rid: record)
    monkeypatch.setattr(physics, 'batch_cpus', lambda: [8])
    rid = lab.physics.submit_batch(run_id='run_b', runs=6)['physics_run_id']
    lab.shutdown()
    again = Lab(Config(runtime=tmp_path / 'rt', background=False))
    closed = again.store.get_physics_run(rid)
    again.shutdown()
    assert closed['state'] == 'canceled' and closed['summary']['requested'] == 6


def test_a_batch_cut_short_by_a_restart_is_marked_failed(tmp_path):
    lab = Lab(Config(runtime=tmp_path, background=False))
    lab.store.put_physics_run({'physics_run_id': 'phys_b', 'kind': 'batch', 'run_id': 'r', 'state': 'running', 'engine': 'mujoco', 'created_utc': '2026-11-06T00:00:00Z', 'errors': []})
    lab.shutdown()
    again = Lab(Config(runtime=tmp_path, background=False))
    assert again.store.get_physics_run('phys_b')['state'] == 'failed'
    again.shutdown()


@pytest.mark.parametrize('request_, code', [
    ({'run_id': 'none'}, 'unknown_run'), ({'traffic': 'platoon'}, 'invalid_request'), ({'runs': 0}, 'invalid_request'), ({'runs': physics.BATCH_MAX_RUNS + 1}, 'invalid_request'),
    ({'runs': True}, 'invalid_request'), ({'runs': 2.5}, 'invalid_request'), ({'seed': -1}, 'invalid_request'), ({'intensity': 3}, 'invalid_request'), ({'intensity': -.5}, 'invalid_request'),
    ({'factors': 1}, 'invalid_request'), ({'factors': True}, 'invalid_request'),
    ({'factors': [{}]}, 'invalid_request'), ({'factors': [[]]}, 'invalid_request'),
    ({'factors': ['rain']}, 'invalid_request'), ({'factors': 'payload'}, 'invalid_request'), ({'wall_limit_s': 0}, 'invalid_request'), ({'wall_limit_s': physics.BATCH_MAX_WALL_S + 1}, 'invalid_request'),
])
def test_requests_outside_the_contract_are_refused_before_anything_runs(lab, request_, code):
    with pytest.raises(PhysicsError) as e:
        lab.physics.submit_batch(**{'run_id': 'run_b', **request_})
    assert e.value.code == code
    assert not lab.store.list_physics_runs()


def test_the_default_batch_is_sixteen_runs_of_every_factor(lab, monkeypatch):
    started = []
    monkeypatch.setattr(lab.physics, '_run_batch', lambda rec, stop: started.append(rec))
    rec = lab.physics.submit_batch(run_id='run_b')
    assert rec['batch']['runs'] == physics.BATCH_RUNS == 16
    assert rec['batch']['factors'] == list(mujoco_field.FACTORS) and rec['batch']['intensity'] == 1.
    assert physics.BATCH_WALL_LIMIT_S <= physics.BATCH_MAX_WALL_S


def test_a_batch_leaves_the_per_plan_record_of_the_algorithm_alone():
    rows = [{'run_id': 'r', 'algorithm_id': 'a'}]
    single = {'run_id': 'r', 'state': 'succeeded', 'traffic_mode': 'serial', 'check': {'valid': True},
              'metrics': {'total_distance_m': 10, 'planned_total_distance_m': 10, 'cross_track_max_m': .1}}
    batch = {'run_id': 'r', 'kind': 'batch', 'state': 'succeeded', 'traffic_mode': 'serial'}
    assert physical_record(rows, [single, batch], 'a')['runs'] == 1


# --- where a batch runs ------------------------------------------------------------------------------------------------------------
@pytest.fixture
def cpu_tree(tmp_path, monkeypatch):
    def write(frequencies):
        for cpu, khz in frequencies.items():
            (tmp_path / f'cpu{cpu}' / 'cpufreq').mkdir(parents=True)
            if khz:
                (tmp_path / f'cpu{cpu}' / 'cpufreq' / 'base_frequency').write_text(f'{khz}\n')
        monkeypatch.setattr(physics, 'CPU_ROOT', tmp_path)
        monkeypatch.setattr(physics.os, 'sched_getaffinity', lambda _pid: set(frequencies), raising=False)
        monkeypatch.delenv('URP_PHYSICS_CPUS', raising=False)
    return write


def test_on_a_hybrid_cpu_a_batch_takes_the_efficiency_cores(cpu_tree):
    cpu_tree({0: 3400000, 1: 3400000, 2: 3000000, 3: 3000000})
    assert batch_cpus() == [2, 3]


def test_on_a_uniform_cpu_a_batch_leaves_one_core_free(cpu_tree):
    cpu_tree({0: 3000000, 1: 3000000, 2: 3000000})
    assert batch_cpus() == [0, 1]


def test_without_frequency_information_all_cores_are_alike(cpu_tree):
    cpu_tree({0: None, 1: 3400000, 2: 3000000})
    assert batch_cpus() == [0, 1]


def test_a_single_core_is_still_used(cpu_tree):
    cpu_tree({0: None})
    assert batch_cpus() == [0]


def test_the_cores_can_be_named_and_only_usable_ones_count(cpu_tree, monkeypatch):
    cpu_tree({0: 3400000, 1: 3000000, 2: 3000000, 3: 3000000})
    monkeypatch.setenv('URP_PHYSICS_CPUS', '1-2,9')
    assert batch_cpus() == [1, 2]
    monkeypatch.setenv('URP_PHYSICS_CPUS', 'many')
    assert batch_cpus() == [1, 2, 3], 'an unreadable setting falls back to the default'


def test_without_affinity_support_a_batch_still_runs(monkeypatch, tmp_path):
    monkeypatch.delattr(physics.os, 'sched_getaffinity', raising=False)
    monkeypatch.setattr(physics, 'CPU_ROOT', tmp_path)
    monkeypatch.setattr(physics.os, 'cpu_count', lambda: 4)
    monkeypatch.delenv('URP_PHYSICS_CPUS', raising=False)
    assert batch_cpus() == [0, 1, 2]


# --- through the gateway -------------------------------------------------------------------------------------------------------------
def test_the_gateway_starts_lists_and_cancels_a_batch(tmp_path, monkeypatch):
    app = gateway.create_app(Config(port=4199, runtime=tmp_path / 'rt', background=False))
    with TestClient(app, base_url='http://127.0.0.1:4199') as client:
        lab = app.state.lab
        inst, routes, _ = solved(vehicles=2)
        record = {'run_id': 'run_b', 'scenario_id': 'sc', 'problem_class': 'fleet_routing', 'state': 'done', 'status': 'feasible', 'check': {'valid': True},
                  'instance': inst, 'routes': routes, 'params': {}, 'algorithm': {'name': '扫描法'}}
        monkeypatch.setattr(lab.store, 'get_run', lambda rid: record if rid == 'run_b' else None)
        assert client.post('/lab/api/physics/batches', json={}).status_code == 400
        assert client.post('/lab/api/physics/batches', json={'run_id': 'run_b', 'color': 'red'}).status_code == 400
        assert client.post('/lab/api/physics/batches', json={'run_id': 'none'}).status_code == 404
        started = client.post('/lab/api/physics/batches', json={'run_id': 'run_b', 'traffic': 'coordinated', 'runs': 2, 'seed': 1, 'intensity': .5, 'factors': ['service_time']})
        assert started.status_code == 202
        rid = started.json()['physics_run_id']
        assert any(r['physics_run_id'] == rid for r in client.get('/lab/api/physics/runs').json()['runs'])
        assert client.post(f'/lab/api/physics/runs/{rid}/cancel', json={}).status_code == 200
        done = lab.physics.wait(rid, timeout_s=60)
        assert done['state'] in ('canceled', 'succeeded')
        assert client.get(f'/lab/api/physics/runs/{rid}').json()['batch']['factors'] == ['service_time']


@pytest.mark.parametrize('windows', [
    {'all_on_time': False, 'total': 2, 'on_time': 1},
    {'all_on_time': True, 'total': 2, 'on_time': 2, 'early_starts': 1},
])
def test_a_physically_clean_batch_sample_still_must_meet_its_windows(windows):
    result = {'state': 'succeeded', 'check': {'valid': True}, 'time_windows': windows}
    sample = physics_stats.sample_row({}, result)
    summary = physics_stats.summarize([sample], 1)
    assert sample['valid'] is False and sample['reason'] == 'time_window'
    assert summary['passed'] == 0 and summary['finished'] == 1
    assert sample['time_windows'] == windows
