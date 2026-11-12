import copy
import json

import pytest

from ceres_lab import fieldworld, mujoco_field, sim2sim
from ceres_lab.gateway import Config, Lab
from ceres_lab.physics import PhysicsError, plan_from_run
from ceres_lab.scenarios import validate_instance
from ceres_lab.solvers import fleet


def solved(vehicles=1):
    inst = validate_instance('fleet_routing', {
        'boundary': [[0, 0], [40, 0], [40, 30], [0, 30]], 'depot': [5, 5],
        'tasks': [{'id': 1, 'xy': [20, 8], 'service_s': 1.0}, {'id': 2, 'xy': [30, 20]}],
        'vehicles': {'count': vehicles, 'capacity': 2 if vehicles == 1 else 1, 'travel': 'free', 'speed_mps': 1, 'trips': 'single'}})
    routes, check, metrics, *_ = fleet.solve({}, inst, 'panel.sweep', {})
    assert check['valid']
    return inst, routes, check


def plan_of(out, vehicles=1):
    inst, routes, check = solved(vehicles)
    world = fieldworld.build(inst, routes, out, 'test', 4.0, None, None, inherit_ground=False)
    for v, r in zip(world['vehicles'], routes):
        v['planned_length_m'], v['planned_duration_s'] = r.get('cost'), r.get('duration_s')
    return {**world, 'run_id': 'run_x', 'planned': {'makespan_s': max(r['duration_s'] for r in routes), 'total_distance_m': check['computed_cost']}}


def test_the_plan_runs_to_its_end_on_a_flat_field(tmp_path):
    plan = plan_of(tmp_path)
    r = mujoco_field.execute(plan)
    assert r['state'] == 'succeeded' and not r['contacts']
    assert r['metrics']['tasks_completed'] == 2
    v = r['vehicles'][0]
    assert v['cross_track_max_m'] < .3 and abs(v['distance_m'] - v['path_length_m']) < 2
    z = [p[3] for p in r['rigid_body_poses'][v['name']][100:]]
    assert max(z) - min(z) < .01, 'the body stays on the ground; a chattering servo would lift it'


def test_two_runs_of_one_plan_are_identical_and_show_no_gap(tmp_path):
    plan = plan_of(tmp_path)
    a, b = mujoco_field.execute(plan), mujoco_field.execute(plan)
    gap = sim2sim.compare(a, b)
    assert gap['path_max_m'] == pytest.approx(0, abs=1e-9) and gap['time_rmse_m'] == pytest.approx(0, abs=1e-9)
    assert a['model_sha256'] == b['model_sha256']


def test_a_lateral_shift_of_one_record_is_measured_as_a_path_gap(tmp_path):
    plan = plan_of(tmp_path)
    a = mujoco_field.execute(plan)
    b = copy.deepcopy(a)
    for rows in b['rigid_body_poses'].values():
        for row in rows:
            row[1] += .05
    gap = sim2sim.compare(a, b)
    assert .02 < gap['path_mean_m'] <= .05
    assert gap['time_rmse_m'] == pytest.approx(.05, abs=1e-6)


def test_a_late_departure_is_not_a_path_difference(tmp_path):
    plan = plan_of(tmp_path)
    a = mujoco_field.execute(plan)
    b = copy.deepcopy(a)
    for rows in b['rigid_body_poses'].values():
        for row in rows:
            row[0] += 7
    b['execution_started_sim_s'] += 7
    gap = sim2sim.compare(a, b)
    assert gap['path_max_m'] < 1e-6 and gap['time_rmse_m'] < 1e-6


def test_records_of_different_plans_are_refused():
    with pytest.raises(ValueError):
        sim2sim.compare({'run_id': 'a', 'vehicles': []}, {'run_id': 'b', 'vehicles': []})


def test_a_solid_on_the_route_stops_the_mission_and_is_recorded(tmp_path):
    plan = plan_of(tmp_path)
    # The wall exists only in the physics world: the route was never planned around it, so the robot runs into it.
    line = plan['vehicles'][0]['path']
    (x0, y0), (x1, y1) = line[len(line) // 2 - 1], line[len(line) // 2]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    plan['instance'] = {**plan['instance'], 'obstacles': [{'id': 'wall', 'kind': 'building', 'polygon': [[cx - 2, cy - 2], [cx + 2, cy - 2], [cx + 2, cy + 2], [cx - 2, cy + 2]]}]}
    r = mujoco_field.execute(plan)
    assert r['contacts'] and r['state'] == 'failed'


def test_two_vehicles_keep_the_mission_queue(tmp_path):
    plan = plan_of(tmp_path, vehicles=2)
    assert len(plan['vehicles']) == 2
    r = mujoco_field.execute(plan)
    assert r['state'] == 'succeeded' and r['metrics']['vehicles_done'] == 2
    first, second = [v['queue_wait_s'] for v in r['vehicles']]
    assert first < 1 < second, 'the second mission departs after the first has finished'


def test_configurations_outside_the_model_are_refused_not_approximated(tmp_path):
    plan = plan_of(tmp_path)
    assert mujoco_field.supports(plan) is None
    for key, value in (('execution', {'mode': 'nonideal'}), ('physics', {**plan['physics'], 'wheel_lateral_friction': .3})):
        with pytest.raises(mujoco_field.Unsupported):
            mujoco_field.execute({**plan, key: value})
    old = {k: v for k, v in plan['physics'].items() if k != 'actuator_mode'}
    assert '缺少完整参数' in mujoco_field.supports({**plan, 'physics': old})


def test_a_torque_limited_drive_runs_through_the_same_loop(tmp_path):
    plan = plan_of(tmp_path)
    plan = {**plan, 'physics': {**plan['physics'], 'actuator_mode': 'torque_limited'}}
    assert 'forcelimited="true"' in mujoco_field.build_mjcf(plan)


@pytest.fixture
def lab(tmp_path):
    lab = Lab(Config(runtime=tmp_path, background=False))
    yield lab
    lab.shutdown()


def test_unverified_or_unfinished_plans_are_refused(tmp_path):
    inst, routes, _ = solved()
    run = {'run_id': 'r', 'scenario_id': 's', 'problem_class': 'fleet_routing', 'state': 'done', 'status': 'infeasible', 'check': {'valid': False}, 'instance': inst, 'routes': routes}
    with pytest.raises(PhysicsError) as e:
        plan_from_run(run, tmp_path / 'w')
    assert e.value.code == 'unchecked_plan'


def test_a_gazebo_session_is_replayed_from_its_frozen_plan_and_compared(lab):
    out = lab.runtime / 'fieldsim' / 'field_x'
    plan = plan_of(out)
    plan['session_id'] = 'field_x'
    (out / 'plan.json').write_text(json.dumps(plan))
    gazebo = mujoco_field.execute(plan)
    gazebo.pop('engine')
    (out / 'result.json').write_text(json.dumps(gazebo))
    rec = lab.physics.submit(session_id='field_x')
    done = lab.physics.wait(rec['physics_run_id'])
    assert done['state'] == 'succeeded', done
    assert done['comparison']['a']['engine'] == 'gazebo' and done['comparison']['b']['engine'] == 'mujoco'
    assert done['comparison']['path_max_m'] == pytest.approx(0, abs=1e-9)
    assert lab.physics.get(done['physics_run_id'])['trails']


def test_a_cancelled_run_ends_and_leaves_a_record(lab):
    out = lab.runtime / 'fieldsim' / 'field_y'
    plan = plan_of(out)
    (out / 'plan.json').write_text(json.dumps(plan))
    (out / 'result.json').write_text('{}')
    rec = lab.physics.submit(session_id='field_y')
    lab.physics.cancel(rec['physics_run_id'])
    done = lab.physics.wait(rec['physics_run_id'])
    assert done['state'] in ('canceled', 'succeeded')  # a run can finish before the signal lands
    assert done['state'] != 'running'


def test_a_run_interrupted_by_a_restart_is_marked_failed(tmp_path):
    lab = Lab(Config(runtime=tmp_path, background=False))
    lab.store.put_physics_run({'physics_run_id': 'phys_old', 'run_id': 'r', 'state': 'running', 'engine': 'mujoco', 'created_utc': '2026-11-05T00:00:00Z', 'errors': []})
    lab.shutdown()
    again = Lab(Config(runtime=tmp_path, background=False))
    assert again.store.get_physics_run('phys_old')['state'] == 'failed'
    again.shutdown()


def test_the_windows_shims_leave_this_platform_alone():
    import os
    import signal
    from ceres_lab import compat
    assert os.killpg is not compat._kill_tree and signal.SIGKILL == 9


def test_the_windows_kill_ends_the_tree_whatever_the_signal(monkeypatch):
    import signal
    import subprocess
    from types import SimpleNamespace
    from ceres_lab import compat
    seen = []
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(subprocess, 'run', lambda argv, **_: seen.append(argv) or SimpleNamespace(returncode=0))
    compat._kill_tree(7, signal.SIGINT)
    compat._kill_tree(7, compat._Kill(9))
    assert seen == [['taskkill', '/PID', '7', '/T', '/F']] * 2 and compat._Kill(9).name == 'SIGKILL'
    monkeypatch.setattr(subprocess, 'run', lambda argv, **_: SimpleNamespace(returncode=128))
    with pytest.raises(ProcessLookupError):
        compat._kill_tree(7, signal.SIGTERM)


def test_no_node_marks_the_js_baselines_unavailable(monkeypatch):
    from ceres_lab import paths
    from ceres_lab.solvers import catalog
    monkeypatch.setattr(paths, 'node_command', lambda: None)
    rows = {a['id']: a for a in catalog()}
    assert rows['urp.js.savings']['available'] is False and 'Node' in rows['urp.js.savings']['unavailable_reason']
    monkeypatch.setenv('CERES_NODE', '/opt/app/electron')
    monkeypatch.undo()
    monkeypatch.setenv('CERES_NODE', '/opt/app/electron')
    assert paths.node_command() == ['/opt/app/electron']


def test_the_solver_watchdog_reports_a_timeout_without_an_interval_timer(monkeypatch, capsys):
    import signal
    from ceres_lab import worker
    monkeypatch.delattr(signal, 'SIGALRM')
    exits = []
    monkeypatch.setattr(worker.os, '_exit', exits.append)
    disarm = worker.limit(0.01)
    import time
    time.sleep(0.15)
    disarm()
    assert exits == [1] and json.loads(capsys.readouterr().out)['error_kind'] == 'timeout'


def test_an_installed_app_always_takes_gazebo_from_the_container(monkeypatch):
    from conftest import REAL_GAZEBO_GAP
    monkeypatch.setenv('CERES_DESKTOP', '1')
    assert '容器后端' in REAL_GAZEBO_GAP()
