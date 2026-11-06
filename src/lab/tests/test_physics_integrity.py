"""Lifecycle, asset binding and same-condition comparison without a live simulator."""
import copy
import io
import json

import pytest

from urp_lab import paths, physics, sim2sim
from urp_lab.assets import bindings, importer, store as assets
from urp_lab.physics import PhysicsError, PhysicsManager, plan_from_run
from urp_lab.scenarios import validate_instance
from urp_lab.solvers import fleet


class Store:
    def __init__(self):
        self.records = {}

    def list_physics_runs(self, limit):
        return list(self.records.values())[:limit]

    def get_physics_run(self, rid):
        return copy.deepcopy(self.records.get(rid))

    def put_physics_run(self, record):
        self.records[record['physics_run_id']] = copy.deepcopy(record)


class Process:
    def __init__(self, stdout=''):
        self.stdout = io.StringIO(stdout)
        self.returncode = None
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = -9

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@pytest.fixture
def manager(tmp_path):
    manager = PhysicsManager(Store(), tmp_path)
    record = {'physics_run_id': 'phys_test', 'state': 'running', 'errors': []}
    manager._dir('phys_test').mkdir(parents=True)
    manager._emit(record)
    return manager, record


def assert_closed(manager, record, state):
    done = manager.store.get_physics_run(record['physics_run_id'])
    assert done['state'] == state and done['finished_utc']
    assert not manager._procs and not manager._canceled
    return done


def test_spawn_failure_finishes_record_and_closes_stderr(manager, monkeypatch):
    manager, record = manager
    handles = []

    def fail(*args, **kwargs):
        handles.append(kwargs['stderr'])
        raise OSError('cannot spawn worker')

    monkeypatch.setattr(physics.subprocess, 'Popen', fail)
    manager._run(record, None)
    assert 'cannot spawn worker' in assert_closed(manager, record, 'failed')['errors'][0]
    assert handles[0].closed


def test_cancel_before_worker_entry_never_spawns(manager, monkeypatch):
    manager, record = manager
    manager.cancel(record['physics_run_id'])
    monkeypatch.setattr(physics.subprocess, 'Popen', lambda *a, **k: pytest.fail('canceled work must not start'))
    manager._run(record, None)
    assert_closed(manager, record, 'canceled')


def test_cancel_during_spawn_is_applied_after_registration(manager, monkeypatch):
    manager, record = manager
    process = Process()

    def spawn(*args, **kwargs):
        manager.cancel(record['physics_run_id'])
        return process

    monkeypatch.setattr(physics.subprocess, 'Popen', spawn)
    manager._run(record, None)
    assert process.killed and process.stdout.closed
    assert_closed(manager, record, 'canceled')


def test_progress_read_failure_reaps_worker(manager, monkeypatch):
    manager, record = manager
    process = Process()

    class BrokenOutput(io.StringIO):
        def __next__(self):
            raise OSError('stream interrupted')

    process.stdout = BrokenOutput()
    monkeypatch.setattr(physics.subprocess, 'Popen', lambda *a, **k: process)
    manager._run(record, None)
    assert process.killed and process.stdout.closed
    assert 'stream interrupted' in assert_closed(manager, record, 'failed')['errors'][0]


def test_cancel_during_result_processing_wins_and_late_cancel_stays_closed(manager, monkeypatch):
    manager, record = manager
    process = Process()
    (manager._dir(record['physics_run_id']) / 'result.json').write_text(json.dumps({'state': 'succeeded'}))
    monkeypatch.setattr(physics.subprocess, 'Popen', lambda *a, **k: process)

    def comparison(*args):
        manager.cancel(record['physics_run_id'])
        return {'available': False}

    monkeypatch.setattr(sim2sim, 'compare', comparison)
    manager._run(record, {'run_id': 'r'})
    assert_closed(manager, record, 'canceled')
    manager.cancel(record['physics_run_id'])
    assert_closed(manager, record, 'canceled')


def test_thread_start_failure_finishes_submitted_record(tmp_path, monkeypatch):
    manager = PhysicsManager(Store(), tmp_path)
    monkeypatch.setattr(physics, 'available', lambda: {'available': True})
    monkeypatch.setattr(manager, '_frozen_plan', lambda *args: {})

    class BrokenThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError('cannot start thread')

    monkeypatch.setattr(physics.threading, 'Thread', BrokenThread)
    record = manager.submit(run_id='r')
    assert 'cannot start thread' in assert_closed(manager, record, 'failed')['errors'][0]


def solved_run():
    instance = validate_instance('fleet_routing', {
        'boundary': [[0, 0], [40, 0], [40, 30], [0, 30]], 'depot': [5, 5],
        'tasks': [{'id': 1, 'xy': [20, 8], 'service_s': 1}],
        'vehicles': {'count': 1, 'capacity': 2, 'travel': 'free', 'speed_mps': 1, 'trips': 'single'}})
    routes, check, *_ = fleet.solve({}, instance, 'panel.sweep', {})
    assert check['valid']
    return {'run_id': 'r', 'scenario_id': 's', 'problem_class': 'fleet_routing', 'state': 'done',
            'status': 'feasible', 'check': check, 'instance': instance, 'routes': routes}


@pytest.fixture
def vehicle_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'RUNTIME', tmp_path / 'assets-runtime')
    importer.ensure_builtins()
    manifest = assets.load(bindings.FIELD_VEHICLE)
    version = assets.version(manifest)
    return {'id': manifest['id'], 'version': version['v'], 'sha256': version['sha256'], 'role': 'vehicle'}


def test_supported_vehicle_binding_and_check_are_preserved(vehicle_asset, tmp_path):
    run = solved_run()
    plan = plan_from_run(run, tmp_path / 'world')
    assert plan['vehicle_asset']['reference'] == bindings.identity(vehicle_asset)
    assert plan['vehicle_asset']['binding'] == 'explicit_legacy_builtin'
    assert plan['planning_check']['valid'] and plan['planning_solution'] == run


@pytest.mark.parametrize(('classes', 'code'), [
    ([{'name': 'one'}, {'name': 'two'}], 'heterogeneous_fleet_execution'),
    ([{'name': 'curved', 'min_turn_radius_m': 2}], 'curvature_constrained_execution'),
])
def test_no_builtin_substitution_for_unsupported_fleet(vehicle_asset, tmp_path, monkeypatch, classes, code):
    run = solved_run()
    run['instance']['vehicles']['fleet'] = classes
    # The regression is the execution binding after independent planning succeeds.
    monkeypatch.setattr(physics, 'validate_instance', lambda *args: run['instance'])
    monkeypatch.setattr(physics, 'check_fleet', lambda *args: run['check'])
    with pytest.raises(PhysicsError) as caught:
        plan_from_run(run, tmp_path / 'world')
    assert caught.value.code == code
    assert not (tmp_path / 'world').exists()


def test_display_only_vehicle_is_rejected_before_world_creation(vehicle_asset, tmp_path):
    manifest = assets.load(bindings.FIELD_VEHICLE)
    manifest.update(id='vehicle-display-only', builtin=False)
    assets.asset_dir(manifest['id']).mkdir()
    assets.save(manifest)
    run = solved_run()
    run['instance']['asset_refs'] = [{**vehicle_asset, 'id': manifest['id']}]
    with pytest.raises(PhysicsError) as caught:
        plan_from_run(run, tmp_path / 'world')
    assert caught.value.code == 'unsupported_vehicle_asset'
    assert not (tmp_path / 'world').exists()


def test_asset_hash_drift_keeps_binding_error(vehicle_asset, tmp_path):
    run = solved_run()
    run['instance']['asset_refs'] = [{**vehicle_asset, 'sha256': 'changed'}]
    with pytest.raises(PhysicsError) as caught:
        plan_from_run(run, tmp_path / 'world')
    assert caught.value.code == 'asset_version_mismatch' and caught.value.status == 409


def plan():
    return {'physics': {'model': 'builtin', 'sha256': 'nominal', 'ground_friction': 1},
            'traffic': {'policy': 'serial', 'gates': []},
            'vehicles': [{'name': 'v1', 'start': [0, 0, 0], 'path': [[0, 0], [1, 0]],
                          'tasks': [{'id': 1, 'xy': [1, 0], 'earliest_s': 0, 'latest_s': 2}]}],
            'instance': {'boundary': [[0, 0], [2, 0], [2, 2], [0, 2]]}, 'bounds': [0, 0, 2, 2],
            'robot_radius_m': .5, 'speed_mps': 1, 'travel': 'free', 'execution': None}


def result(source=None):
    source = copy.deepcopy(source or plan())
    return {'run_id': 'r', 'state': 'succeeded', 'physics': source['physics'],
            'plan_identity': sim2sim.plan_identity(source), 'execution_started_sim_s': 0,
            'vehicles': [{'name': 'v1', 'duration_s': 1.9}],
            'rigid_body_poses': {'v1': [[i / 10, min(i / 10, 1), 0, 0, 0, 0, 0] for i in range(21)]},
            'tasks': [{'vehicle': 'v1', 'id': 1, 't_sim': 1}], 'metrics': {}}


def test_identical_execution_evidence_and_complete_tracks_are_comparable():
    first = result()
    before = copy.deepcopy(first)
    compared = sim2sim.compare(first, copy.deepcopy(first))
    assert compared['available'] and not compared['errors']
    assert compared['path_max_m'] == compared['time_rmse_m'] == compared['mission_clock_rmse_m'] == 0
    assert first == before


@pytest.mark.parametrize('condition', ['physics', 'traffic', 'path', 'window', 'bounds', 'execution', 'perturbation'])
def test_same_run_id_different_frozen_conditions_are_unavailable(condition):
    changed = plan()
    if condition == 'physics':
        changed['physics']['ground_friction'] = .3  # old internal sha256 must not conceal a change
    elif condition == 'traffic':
        changed['traffic']['gates'] = [{'s': 1}]
    elif condition == 'path':
        changed['vehicles'][0]['path'][1][0] = 2
    elif condition == 'window':
        changed['vehicles'][0]['tasks'][0]['latest_s'] = 4
    elif condition == 'bounds':
        changed['bounds'][0] = -1
    else:
        changed[condition] = {'mode': 'changed'}
    target = result(changed)
    target['perturbation'] = changed.get('perturbation')
    compared = sim2sim.compare(result(), target)
    assert not compared['available'] and compared['identity']['different']
    assert compared['path_max_m'] is None and compared['vehicles'] == []
    assert '比较不可用' in compared['scope']


def test_missing_identity_is_unavailable_without_rewriting_old_record():
    old = result()
    old.pop('plan_identity')
    before = copy.deepcopy(old)
    compared = sim2sim.compare(old, result())
    assert not compared['available'] and 'a.plan_identity' in compared['identity']['missing']
    assert old == before


@pytest.mark.parametrize('change', ['missing_vehicle', 'duplicate_vehicle', 'missing_truth', 'head', 'tail', 'gap', 'nan', 'time', 'short', 'column'])
def test_incomplete_or_invalid_track_does_not_produce_partial_comparison(change):
    target = result()
    rows = target['rigid_body_poses']['v1']
    if change == 'missing_vehicle':
        target['vehicles'] = []
    elif change == 'duplicate_vehicle':
        target['vehicles'] *= 2
    elif change == 'missing_truth':
        target['trails'] = {'v1': [[row[0], row[1], row[2], row[6]] for row in rows]}
        target['rigid_body_poses'] = {}  # cannot borrow display trails after losing truth
    elif change == 'head':
        target['rigid_body_poses']['v1'] = rows[5:]
    elif change == 'tail':
        target['rigid_body_poses']['v1'] = rows[:10]
    elif change == 'gap':
        target['rigid_body_poses']['v1'] = rows[:5] + rows[10:]
    elif change == 'nan':
        rows[3][1] = float('nan')
    elif change == 'time':
        rows[3][0] = rows[2][0]
    elif change == 'short':
        target['rigid_body_poses']['v1'] = rows[:1]
    else:
        target['rigid_body_poses']['v1'] = [[0, 0], [1, 1]]
    compared = sim2sim.compare(result(), target)
    assert not compared['available'] and compared['errors'] and compared['vehicles'] == []
    assert compared['time_rmse_m'] is None


def test_departure_alignment_removes_queue_delay_but_mission_clock_retains_it():
    reference, target = result(), result()
    target['rigid_body_poses']['v1'] = [[i / 10, min(max(i / 10 - .5, 0), 1), 0, 0, 0, 0, 0] for i in range(26)]
    target['vehicles'][0]['duration_s'] += .5
    compared = sim2sim.compare(reference, target)
    assert compared['available']
    assert compared['time_alignment'] == 'per_vehicle_departure'
    assert compared['time_rmse_m'] == pytest.approx(0, abs=1e-12)
    assert compared['mission_clock_rmse_m'] > .2


def test_identity_excludes_output_location_but_preserves_execution_order():
    original = plan()
    first = sim2sim.plan_identity(original)
    original.update(world='another-world', sdf='/different/location', session_id='new-session', real_time_factor=9)
    original['vehicles'][0].update(label='display label', color='blue')
    assert sim2sim.plan_identity(original) == first
    original['vehicles'].append({**original['vehicles'][0], 'name': 'v2'})
    two = sim2sim.plan_identity(original)
    original['vehicles'].reverse()
    assert sim2sim.plan_identity(original)['sha256'] != two['sha256']


def test_batch_thread_start_failure_finishes_record(tmp_path, monkeypatch):
    manager = PhysicsManager(Store(), tmp_path)
    monkeypatch.setattr(physics, 'available', lambda: {'available': True, 'version': 'test'})
    monkeypatch.setattr(manager, '_frozen_plan', lambda *args: {})
    monkeypatch.setattr(physics, 'batch_cpus', lambda: [0])

    class BrokenThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError('cannot start batch thread')

    monkeypatch.setattr(physics.threading, 'Thread', BrokenThread)
    record = manager.submit_batch(run_id='r', runs=1)
    assert 'cannot start batch thread' in assert_closed(manager, record, 'failed')['errors'][0]
    assert not manager._batches


def test_malformed_identity_or_record_conditions_are_unavailable():
    reference = result()
    target = result()
    target['physics']['ground_friction'] = float('nan')
    assert not sim2sim.compare(reference, target)['available']
    target = result()
    target['plan_identity']['components']['physics'] = None
    assert not sim2sim.compare(reference, target)['available']
    target = result()
    target['vehicles'].append(None)
    assert not sim2sim.compare(reference, target)['available']


def test_legal_earliest_wait_beyond_old_engine_deadline_finishes(tmp_path):
    from urp_lab import mujoco_field
    from test_physics import plan_of

    frozen = plan_of(tmp_path)
    old_budget = 2.5 * max(sum(v.get('planned_duration_s') or 0 for v in frozen['vehicles']), 60) + 120
    earliest = old_budget + 10
    frozen['vehicles'][0]['tasks'][0]['earliest_s'] = earliest
    executed = mujoco_field.execute(frozen)
    assert executed['state'] == 'succeeded', executed['errors']
    assert executed['tasks'][0]['service_started_at'] - executed['execution_started_sim_s'] >= earliest - .1
    assert executed['sim_time'] > old_budget


@pytest.mark.parametrize('missing', ['both_null', 'both_omitted', 'invalid_number'])
def test_missing_task_arrivals_never_become_a_complete_arrival_comparison(missing):
    reference, target = result(), result()
    for record in (reference, target):
        record['vehicles'][0]['tasks_total'] = 1
        if missing == 'both_null':
            record['tasks'][0]['t_sim'] = None
        elif missing == 'both_omitted':
            record['tasks'] = []
        else:
            record['tasks'][0]['t_sim'] = 'invalid'
    comparison = sim2sim.compare(reference, target)
    assert comparison['available']  # complete paths can still be compared
    arrival = comparison['vehicles'][0]['task_arrival_s']
    assert not arrival['available'] and arrival['mean_abs'] is None and arrival['reason']


@pytest.mark.parametrize('code', [7, -9, 0])
def test_batch_sample_exit_status_cannot_be_hidden_by_a_success_json(manager, code):
    from urp_lab import physics_stats

    owner, record = manager
    record['batch'] = {'seed': 4}
    original = {'seed': physics.sample_seed(4, 0), 'state': 'succeeded', 'valid': True,
                'reason': None, 'errors': [], 'makespan_s': 12.5, 'held_s': 0.0}
    samples = owner._dir(record['physics_run_id']) / 'samples'
    samples.mkdir()
    path = samples / '000.json'
    frozen = (json.dumps(original, ensure_ascii=False, indent=2) + '\n').encode('utf8')
    path.write_bytes(frozen)

    row = owner._sample_row(record, 0, code)
    assert row['returncode'] == code and row['index'] == 0
    assert path.read_bytes() == frozen, 'classifying the process exit must preserve the original worker output'
    summary = physics_stats.summarize([row], 1)
    assert summary['finished'] == 1
    if code:
        assert row['state'] == 'crashed' and row['valid'] is False and row['reason'] == 'worker_crash'
        assert row['worker_result'] == original
        assert summary['passed'] == 0 and summary['success_rate'] == 0
        assert summary['failures'] == [{'reason': 'worker_crash', 'label': '进程异常退出', 'count': 1}]
    else:
        assert row['state'] == 'succeeded' and row['valid'] is True and row['seed'] == original['seed']
        assert summary['passed'] == 1 and summary['success_rate'] == 1 and summary['failures'] == []


class ClosingStore(Store):
    """A closed execution store must never receive a late worker finalization."""
    def __init__(self):
        super().__init__()
        self.closed = False
        self.late_writes = []

    def put_physics_run(self, record):
        if self.closed:
            self.late_writes.append(record['physics_run_id'])
            raise AssertionError('worker wrote after shutdown returned and store closed')
        super().put_physics_run(record)


@pytest.mark.parametrize('pause_at', ['before_spawn', 'during_spawn'])
def test_shutdown_waits_for_admitted_single_worker_and_finishes_before_store_close(tmp_path, monkeypatch, pause_at):
    import threading
    import time

    store = ClosingStore()
    owner = PhysicsManager(store, tmp_path)
    monkeypatch.setattr(physics, 'available', lambda: {'available': True, 'version': 'test'})
    monkeypatch.setattr(owner, '_frozen_plan', lambda *args: {})
    entered, release, shutdown_done, worker_done = (threading.Event() for _ in range(4))
    process = Process()
    spawned, errors = [], []
    original_run = owner._run

    def run(record, reference):
        try:
            if pause_at == 'before_spawn':
                entered.set()
                assert release.wait(5), 'test did not release worker entry'
            original_run(record, reference)
        finally:
            worker_done.set()

    def spawn(*args, **kwargs):
        spawned.append(True)
        if pause_at == 'during_spawn':
            entered.set()
            assert release.wait(5), 'test did not release Popen'
        return process

    def shutdown():
        try:
            owner.shutdown()
        except BaseException as exc:
            errors.append(exc)
        finally:
            shutdown_done.set()

    monkeypatch.setattr(owner, '_run', run)
    monkeypatch.setattr(physics.subprocess, 'Popen', spawn)
    record = owner.submit(run_id='r')
    stopper = threading.Thread(target=shutdown, daemon=True)
    try:
        assert entered.wait(5)
        stopper.start()
        deadline = time.monotonic() + 5
        while not owner._closing and time.monotonic() < deadline:
            time.sleep(.005)
        assert owner._closing
        assert not shutdown_done.wait(.05), 'shutdown returned before the admitted worker could finish'
        release.set()
        assert shutdown_done.wait(5) and worker_done.wait(5)
        stopper.join(1)
        assert not errors
        store.closed = True
        assert store.records[record['physics_run_id']]['state'] == 'canceled'
        assert not owner._procs and not owner._workers and not owner._canceled
        assert not store.late_writes
        if pause_at == 'before_spawn':
            assert spawned == []
        else:
            assert spawned == [True] and process.killed and process.stdout.closed
    finally:
        release.set()
        if stopper.ident is not None:
            stopper.join(5)


def test_shutdown_cannot_join_a_registered_batch_before_its_atomic_start(tmp_path, monkeypatch):
    import threading

    real_thread = threading.Thread
    store = ClosingStore()
    owner = PhysicsManager(store, tmp_path)
    monkeypatch.setattr(physics, 'available', lambda: {'available': True, 'version': 'test'})
    monkeypatch.setattr(owner, '_frozen_plan', lambda *args: {})
    monkeypatch.setattr(physics, 'batch_cpus', lambda: [0])
    start_entered, release_start, submit_done, shutdown_done, unstarted_join = (threading.Event() for _ in range(5))
    errors, records = [], []

    class StartBarrierThread:
        def __init__(self, **kwargs):
            self.inner = real_thread(**kwargs)
            self.started = False

        def start(self):
            start_entered.set()
            assert release_start.wait(5), 'test did not release Thread.start'
            self.inner.start()
            self.started = True

        def join(self, timeout=None):
            if not self.started:
                unstarted_join.set()
                raise RuntimeError('cannot join thread before it is started')
            self.inner.join(timeout)

        def is_alive(self):
            return self.inner.is_alive()

    def batch(record, stop):
        assert stop.wait(5), 'shutdown did not cancel the admitted batch'
        owner._close_batch(record, {}, {}, stop, None, 0)

    def submit():
        try:
            records.append(owner.submit_batch(run_id='r', runs=1))
        except BaseException as exc:
            errors.append(exc)
        finally:
            submit_done.set()

    def shutdown():
        try:
            owner.shutdown()
        except BaseException as exc:
            errors.append(exc)
        finally:
            shutdown_done.set()

    monkeypatch.setattr(physics.threading, 'Thread', StartBarrierThread)
    monkeypatch.setattr(owner, '_run_batch', batch)
    submitter = real_thread(target=submit, daemon=True)
    stopper = real_thread(target=shutdown, daemon=True)
    submitter.start()
    try:
        assert start_entered.wait(5)
        assert owner._batches and owner._workers
        stopper.start()
        assert not shutdown_done.wait(.05)
        assert not unstarted_join.is_set()
        release_start.set()
        assert submit_done.wait(5) and shutdown_done.wait(5)
        submitter.join(1)
        stopper.join(1)
        assert not errors and not unstarted_join.is_set()
        store.closed = True
        assert records and store.records[records[0]['physics_run_id']]['state'] == 'canceled'
        assert not owner._batches and not owner._workers and not owner._procs
        assert not store.late_writes
    finally:
        release_start.set()
        submitter.join(5)
        if stopper.ident is not None:
            stopper.join(5)


@pytest.mark.parametrize('method', ['submit', 'submit_batch'])
def test_shutdown_rejects_new_submissions_before_planning_or_record_creation(tmp_path, monkeypatch, method):
    store = ClosingStore()
    owner = PhysicsManager(store, tmp_path)
    monkeypatch.setattr(physics, 'available', lambda: {'available': True, 'version': 'test'})
    monkeypatch.setattr(owner, '_frozen_plan', lambda *args: pytest.fail('closed manager must reject before planning'))
    owner.shutdown()
    store.closed = True
    with pytest.raises(PhysicsError) as caught:
        getattr(owner, method)(run_id='r')
    assert (caught.value.status, caught.value.code) == (503, 'manager_shutdown')
    assert store.records == {} and store.late_writes == []
    assert not (tmp_path / 'physics').exists()


def test_shutdown_after_batch_terminal_write_clears_late_cancel_marker(tmp_path, monkeypatch):
    import threading
    import time

    store = ClosingStore()
    owner = PhysicsManager(store, tmp_path)
    monkeypatch.setattr(physics, 'available', lambda: {'available': True, 'version': 'test'})
    monkeypatch.setattr(owner, '_frozen_plan', lambda *args: {})
    monkeypatch.setattr(physics, 'batch_cpus', lambda: [0])
    finalized, release, shutdown_done = (threading.Event() for _ in range(3))
    errors = []

    def batch(record, stop):
        sample = {'index': 0, 'seed': 0, 'state': 'succeeded', 'valid': True, 'makespan_s': 1.0, 'held_s': 0.0}
        owner._close_batch(record, {0: sample}, {}, stop, None, 0)
        finalized.set()
        assert release.wait(5), 'test did not release terminal batch worker'

    def shutdown():
        try:
            owner.shutdown()
        except BaseException as exc:
            errors.append(exc)
        finally:
            shutdown_done.set()

    monkeypatch.setattr(owner, '_run_batch', batch)
    record = owner.submit_batch(run_id='r', runs=1)
    stopper = threading.Thread(target=shutdown, daemon=True)
    try:
        assert finalized.wait(5)
        assert store.records[record['physics_run_id']]['state'] == 'succeeded'
        assert not owner._batches and owner._workers
        stopper.start()
        deadline = time.monotonic() + 5
        while not owner._closing and time.monotonic() < deadline:
            time.sleep(.005)
        assert owner._closing and not shutdown_done.wait(.05)
        release.set()
        assert shutdown_done.wait(5)
        stopper.join(1)
        store.closed = True
        assert not errors and not owner._workers and not owner._canceled and not store.late_writes
        assert store.records[record['physics_run_id']]['state'] == 'succeeded', 'a completed batch must not become canceled retroactively'
    finally:
        release.set()
        if stopper.ident is not None:
            stopper.join(5)


@pytest.mark.parametrize('action', ['cancel', 'shutdown'])
def test_batch_cancel_during_spawn_counts_cutoff_without_a_worker_failure(tmp_path, monkeypatch, action):
    import threading
    import time

    store = ClosingStore()
    owner = PhysicsManager(store, tmp_path)
    monkeypatch.setattr(physics, 'available', lambda: {'available': True, 'version': 'test'})
    monkeypatch.setattr(owner, '_frozen_plan', lambda *args: {})
    monkeypatch.setattr(physics, 'batch_cpus', lambda: [0])
    monkeypatch.setattr(physics.os, 'sched_setaffinity', lambda *args: None, raising=False)
    entered, release, shutdown_done = (threading.Event() for _ in range(3))
    process = Process()
    process.pid = 123
    errors = []

    def spawn(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'test did not release batch Popen'
        return process

    def shutdown():
        try:
            owner.shutdown()
        except BaseException as exc:
            errors.append(exc)
        finally:
            shutdown_done.set()

    monkeypatch.setattr(physics.subprocess, 'Popen', spawn)
    record = owner.submit_batch(run_id='r', runs=1)
    rid = record['physics_run_id']
    stopper = threading.Thread(target=shutdown, daemon=True)
    try:
        assert entered.wait(5)
        worker = owner._workers[rid]
        stop = owner._batches[rid][0]
        if action == 'cancel':
            owner.cancel(rid)
        else:
            stopper.start()
        deadline = time.monotonic() + 5
        while not stop.is_set() and time.monotonic() < deadline:
            time.sleep(.005)
        assert stop.is_set()
        if action == 'shutdown':
            assert not shutdown_done.wait(.05)
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        if action == 'shutdown':
            assert shutdown_done.wait(5)
            stopper.join(1)
        else:
            owner.shutdown()
        store.closed = True
        done = store.records[rid]
        assert done['state'] == 'canceled'
        assert done['summary']['finished'] == done['summary']['passed'] == 0
        assert done['summary']['cut_off'] == 1 and done['summary']['failures'] == []
        samples = json.loads((owner._dir(rid) / 'samples.json').read_text())
        assert len(samples) == 1 and samples[0]['state'] == 'cut_off'
        assert process.killed and not owner._procs and not owner._batches and not owner._workers and not owner._canceled
        assert not errors and not store.late_writes
    finally:
        release.set()
        if stopper.ident is not None:
            stopper.join(5)
