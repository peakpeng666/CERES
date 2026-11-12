"""Non-ideal execution layer: config validation, ground-zone SDF, observation model,
estimator and gate branching. Slip itself is physics, so these tests cover the
plumbing and the declared error model, not physical fidelity."""
import math
from xml.etree import ElementTree as ET

import pytest

from ceres_lab import field_nonideal as ni, fieldworld
from test_backend_integrity import instance, solution


def routes():
    return solution()['routes']


def test_validate_execution_presets_and_determinism():
    inst = instance()
    cfg = ni.validate_execution({'ground_preset': 'wet_patch', 'localization_preset': 'drift_outage', 'seed': 11}, inst, routes())
    assert cfg['schema'] == ni.SCHEMA and cfg['mode'] == 'nonideal'
    assert len(cfg['ground']['zones']) == 1 and cfg['ground']['zones'][0]['mu'] == 0.22
    assert cfg['localization']['gnss']['fix_hz'] == 1.0
    assert ni.validate_execution(None, inst, routes()) is None
    assert ni.validate_execution({'ground_preset': 'none', 'localization_preset': 'truth'}, inst, routes()) is None
    again = ni.validate_execution({'ground_preset': 'wet_patch', 'localization_preset': 'drift_outage', 'seed': 11}, inst, routes())
    assert again == cfg                      # placement is deterministic
    zone = cfg['ground']['zones'][0]['rect']
    x0, y0, x1, y1 = zone
    assert x1 > x0 and y1 > y0
    # zones must stay inside the boundary and clear of the depot disc
    minx, miny, maxx, maxy = ni._bounds(inst['boundary'])
    assert x0 >= minx - 1e-6 and y0 >= miny - 1e-6 and x1 <= maxx + 1e-6 and y1 <= maxy + 1e-6
    dx, dy = inst['depot']
    assert x0 > dx + 2.5 or x1 < dx - 2.5 or y0 > dy + 2.5 or y1 < dy - 2.5


def test_validate_execution_rejects_bad_input():
    inst = instance()
    for bad in ({'ground_preset': 'ice'}, {'localization_preset': 'rtk'}, {'seed': 'x'}, {'slip_protection': 'yes'},
                {'unknown': 1}):
        with pytest.raises(ValueError):
            ni.validate_execution(bad, inst, routes())
    with pytest.raises(ValueError):
        ni.validate_execution({'seed': -1}, inst, routes())


def test_world_sdf_contains_audited_ground_zones(tmp_path):
    inst = instance()
    exec_cfg = ni.validate_execution({'ground_preset': 'wet_patch'}, inst, routes())
    plan = fieldworld.build(inst, routes(), tmp_path, 'zone_world', 4.0, None, exec_cfg)
    assert plan['execution'] is exec_cfg and plan['ground_zones_audit']['valid']
    root = ET.parse(tmp_path / 'world.sdf').getroot()
    model = root.find("world/model[@name='ground_zones']")
    assert model is not None and model.findtext('static') == 'true'
    collision = model.find('link/collision')
    assert collision.findtext('surface/friction/ode/mu') == '0.22'
    # tampering with the zone mu must fail the independent audit
    text = (tmp_path / 'world.sdf').read_text().replace('<mu>0.22</mu>', '<mu>0.9</mu>', 1)
    (tmp_path / 'world.sdf').write_text(text)
    assert not ni.audit_ground_zones(tmp_path / 'world.sdf', exec_cfg)['valid']
    # ideal execution must not produce a zones model
    clean = fieldworld.build(inst, routes(), tmp_path / 'ideal', 'ideal_world')
    assert clean['execution'] is None
    root = ET.parse(tmp_path / 'ideal' / 'world.sdf').getroot()
    assert root.find("world/model[@name='ground_zones']") is None


def test_observation_model_schedule_latency_outage_and_seed():
    gnss = ni.LOCALIZATION_PRESETS['drift_outage']['gnss']
    model = ni.LocalizationModel(gnss, 42)
    delivered = []
    for k in range(0, 7001):                       # 50 Hz truth for 140 s
        t = k * 0.02
        model.on_truth(3.0, 4.0, t)
        delivered += model.deliver(t)
    model.finalize(140.0)
    obs = model.observations
    assert len(obs) >= 100                          # ~1 Hz over the non-outage span
    assert all(o[1] - o[0] >= gnss['latency_s'] - 1e-9 for o in obs)
    dropped = [o for o in obs if not o[4]]
    outage = [o for o in dropped if o[5] == 'outage']
    assert 8 <= len(outage) <= 12                   # one 10 s window at 1 Hz
    assert all(o[5] for o in dropped)               # nothing silently missing
    assert all(0 <= o[2] and -50 <= o[3] for o in obs)
    twin = ni.LocalizationModel(gnss, 42)
    for k in range(0, 101):
        t = k * 0.02
        twin.on_truth(3.0, 4.0, t)
        twin.deliver(t)
    # reproducibility covers the generated values; delivery naturally differs with horizon
    def sig(rows):
        return [[r[0], r[1], r[2], r[3], r[5]] for r in rows]
    assert sig(twin.observations) == sig(obs[:len(twin.observations)])
    other = ni.LocalizationModel(gnss, 43)
    for k in range(0, 101):
        t = k * 0.02
        other.on_truth(3.0, 4.0, t)
        other.deliver(t)
    assert sig(other.observations) != sig(obs[:len(other.observations)])


def test_estimator_dead_reckoning_fusion_and_smoothing():
    gnss = ni.LOCALIZATION_PRESETS['light']['gnss']
    est = ni.Estimator((0, 0), 0.3, gnss)
    # no fixes: pure odometry composition. The odom frame is body-aligned at spawn, so a
    # straight body-x drive reads as +x in odom while the map heading stays 0.3.
    for i in range(1, 41):
        t = i * 0.05
        est.on_odom(0.4, 0.0, t)
    x, y, yaw = est.pose(2.0)
    # the first sample only initializes the reference (no delta), as in any real chain
    travelled = 0.4 * (2.0 - 0.05)
    assert math.hypot(x - travelled * math.cos(0.3), y - travelled * math.sin(0.3)) < 1e-9 and abs(yaw - 0.3) < 1e-9
    # fixes pull the estimate toward the (biased) observations; error floor ≈ |bias|
    est = ni.Estimator((0, 0), 0.0, gnss)
    for i in range(1, 401):
        t = i * 0.05
        truth_x = t * 0.5
        est.on_odom(0.5, 0.0, t)
        if i % 10 == 0:
            est.on_fix(truth_x + 0.12, -0.08, t)
    x, y, _ = est.pose(20.0)
    err = math.hypot(x - truth_x, y)
    assert 0.02 < err < 0.16                        # converged to the biased fix, not to truth and not lost
    # Delayed measurements must update their own time, not pull a moving state back.
    est = ni.Estimator((0,0), 0, {'noise_std_m': 0})
    for i in range(41): est.on_odom(1,0,i*.05)
    assert est.on_fix(1,0,1,2)
    assert est.pose(2)[0] == pytest.approx(2, abs=1e-8)


def test_slip_experiment_predicate_branches_gates():
    from ceres_lab.field_nonideal import slip_experiment
    from ceres_lab.field_dynamics import verify
    assert slip_experiment(None, None) is False
    assert slip_experiment({}, {'ground_friction': 1.0, 'wheel_friction': 1.0, 'wheel_contact': 'sphere'}) is False
    assert slip_experiment(None, {'ground_friction': 0.3}) is True
    assert slip_experiment(None, {'wheel_friction': 0.05}) is True
    assert slip_experiment(None, {'wheel_contact': 'tyre'}) is True
    assert slip_experiment({'mode': 'nonideal'}, None) is False
    base = {
        'physics': {'schema': 'urp.field-diffdrive.rigid/5',
                    'odometry_contract': __import__('ceres_lab.field_physics', fromlist=['ODOMETRY_CONTRACT']).ODOMETRY_CONTRACT},
        'truth_timing': {'latest_clock_samples': 0, 'tf_header_samples': 10},
        'vehicles': [{'name': 'vehicle_1'}],
        'rigid_body_poses': {'vehicle_1': [[i * 0.1, i * 0.01, 0, 0, 0, 0, 0] for i in range(20)]},
        'wheel_odometry': {'vehicle_1': [[i * 0.1, i * 0.5, 0, 0, 0, 0] for i in range(20)]},
        'trails': {'vehicle_1': [[i * 0.1, i * 0.01, 0, 0] for i in range(20)]},
    }
    drifted_physics = dict(base, physics={**base['physics'], 'ground_friction': 0.3})
    report = verify(drifted_physics)
    assert report['valid'] and report['execution_mode'] == 'nonideal'
    assert report['vehicles'][0]['no_slip_consistent'] is None


def test_dynamics_gate_branches_on_execution_mode():
    from ceres_lab.field_dynamics import verify
    base = {
        'physics': {'schema': 'urp.field-diffdrive.rigid/5',
                    'odometry_contract': __import__('ceres_lab.field_physics', fromlist=['ODOMETRY_CONTRACT']).ODOMETRY_CONTRACT},
        'truth_timing': {'latest_clock_samples': 0, 'tf_header_samples': 10},
        'vehicles': [{'name': 'vehicle_1'}],
        'rigid_body_poses': {'vehicle_1': [[i * 0.1, i * 0.01, 0, 0, 0, 0, 0] for i in range(20)]},
        'wheel_odometry': {'vehicle_1': [[i * 0.1, i * 0.5, 0, 0, 0, 0] for i in range(20)]},
        'trails': {'vehicle_1': [[i * 0.1, i * 0.01, 0, 0] for i in range(20)]},
    }
    ideal = verify(base)
    assert not ideal['valid'] and any('不一致' in e for e in ideal['errors'])
    assert not verify(dict(base, execution={'mode':'nonideal'}))['valid']
    drifted = dict(base, execution={'mode': 'nonideal', 'ground': {'zones': [{'mu':.2}]}})
    nonideal = verify(drifted)
    assert nonideal['valid'] and nonideal['execution_mode'] == 'nonideal'
    row = nonideal['vehicles'][0]
    assert row['odom_position_max_m'] > 0.1 and row['no_slip_consistent'] is None
    # data-validity gates still bite in non-ideal mode
    broken = dict(drifted, wheel_odometry={'vehicle_1': [[i * 0.1, i * 0.5, 0, 0, 0, 0] for i in range(20)][::-1]})
    assert not verify(broken)['valid']


def test_fieldcheck_nonideal_observation_gates():
    import numpy as np
    from ceres_lab.fieldcheck import verify_nonideal_observations
    gnss = ni.LOCALIZATION_PRESETS['light']['gnss']
    plan = {'execution': {'mode': 'nonideal', 'localization': {'gnss': gnss}},
            'vehicles': [{'name': 'vehicle_1', 'label': '车1'}]}
    t = np.arange(0, 30, 0.05)
    truth = [[float(x), float(x), 0.0, 0.0, 0.0, 0.0, 0.0] for x in t]
    est = [[float(x), float(x) + 0.05, 0.0, 0.0, 'fused'] for x in t]
    obs = [[float(x), float(x) + 0.1, x + 1.0, 2.0, 1, None] for x in np.arange(0.5, 30, 0.5)]
    result = {'pose_estimates': {'vehicle_1': est}, 'localization_observations': {'vehicle_1': obs},
              'rigid_body_poses': {'vehicle_1': truth},
              'execution_report': {'localization': {'preset': 'light'}, 'slip': {'note': 'x'}, 'interventions': 0},
              'execution_events': []}
    ok = verify_nonideal_observations(plan, result)
    assert ok['valid'], ok['errors']
    # fix rate far below the declared model over a long span must fail
    slow = dict(result, localization_observations={'vehicle_1': obs[::10]})
    assert not verify_nonideal_observations(plan, slow)['valid']
    # estimates identical to truth → suspected truth leak
    leak = dict(result, pose_estimates={'vehicle_1': [[float(x), float(x), 0.0, 0.0, 'fused'] for x in t]})
    assert not verify_nonideal_observations(plan, leak)['valid']
    # unexplained drop
    dropped = [list(r) for r in obs]
    dropped[3][4], dropped[3][5] = 0, None
    unexplained = dict(result, localization_observations={'vehicle_1': dropped})
    assert not verify_nonideal_observations(plan, unexplained)['valid']
    # intervention counting must match the events
    mismatch = dict(result, execution_report={'localization': {'preset': 'light'}, 'slip': {'note': 'x'}, 'interventions': 2},
                    execution_events=[{'kind': 'slip_creep', 'intervention': True}])
    assert not verify_nonideal_observations(plan, mismatch)['valid']
