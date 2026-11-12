"""Known-map ground risk in planning: declaration validation, risk-adjusted edge
weights, detour representability, exposure accounting and executed speed caps."""
import math
import pytest

from ceres_lab.ground_risk import (SCHEMA, normalize_ground, exposure_line, risk_factor,
                                 speed_profile, route_ground_stats)
from ceres_lab.scenarios.validate import validate_instance
from ceres_lab.solvers import fleet
from test_backend_integrity import instance


def ground_inst(**over):
    base = {
        'boundary': [[0, 0], [34, 0], [34, 26], [0, 26]], 'depot': [3, 3], 'robot_radius': 0.5,
        'tasks': [{'id': 1, 'xy': [30, 22], 'demand': 1}, {'id': 2, 'xy': [28, 24], 'demand': 1}],
        'vehicles': {'count': 1, 'capacity': 2, 'travel': 'free', 'speed_mps': 1.0},
        'objective': 'total_distance',
        'ground': {'zones': [{'rect': [8, 8, 24, 18], 'mu': 0.12, 'label': '低附着带'}]}}
    base.update(over)
    return validate_instance('fleet_routing', base)


def test_ground_declaration_validation_and_idempotence():
    inst = ground_inst()
    g = inst['ground']
    assert g['schema'] == SCHEMA and g['zones'][0]['mu'] == 0.12
    assert g['mu_reference'] == 1.0 and g['knowledge'] == 'known_map_v1'
    assert 'speed_policy' in g and 'declared_as' in g
    assert normalize_ground(inst) == g                    # idempotent pass-through
    assert normalize_ground({'ground': None}) is None
    with pytest.raises(ValueError):
        ground_inst(ground={'zones': [{'rect': [8, 8, 24, 18], 'mu': 0.12}], 'bogus': 1})
    for bad_mu in (0.01, 1.5, 'x'):
        with pytest.raises(ValueError):
            ground_inst(ground={'zones': [{'rect': [8, 8, 24, 18], 'mu': bad_mu}]})
    with pytest.raises(ValueError):   # overlapping zones
        ground_inst(ground={'zones': [{'rect': [8, 8, 24, 18], 'mu': 0.2}, {'rect': [10, 10, 20, 16], 'mu': 0.3}]})
    with pytest.raises(ValueError):   # must clear the depot
        ground_inst(ground={'zones': [{'rect': [1, 1, 6, 6], 'mu': 0.2}]})
    with pytest.raises(ValueError):   # outside the boundary
        ground_inst(ground={'zones': [{'rect': [30, 20, 40, 26], 'mu': 0.2}]})


def test_exposure_and_factor_geometry():
    zones = [{'rect': [0, 0, 2, 2], 'mu': 0.1}]
    inside, deficit = exposure_line([(1, 1), (5, 1)], zones)
    assert inside == pytest.approx(1.0) and deficit == pytest.approx(0.9)
    f_cross = risk_factor(((1, 1), (5, 1)), zones, 1.0)
    f_clear = risk_factor(((-3, 1), (-1, 1)), zones, 1.0)
    assert f_cross == pytest.approx(0.9 / 4) and f_clear == 0.0


def test_policy_flips_route_choice_and_records_consistent_costs():
    inst = ground_inst()
    out = {}
    for policy, weight in (('ignore', 1.0), ('risk_adjust', 1.0), ('risk_adjust', 0.2)):
        routes, chk, metrics, raw, status, trace = fleet.solve(
            {}, inst, 'pyvrp.fleet', {'time_limit_s': 2, 'seed': 42,
                                      'ground_policy': policy, 'ground_risk_weight': weight})
        assert status == 'feasible' and chk['valid'], chk['errors']
        out[(policy, weight)] = routes[0]
    cross = out[('ignore', 1.0)]
    detour = out[('risk_adjust', 1.0)]
    capped = out[('risk_adjust', 0.2)]
    assert cross['ground_stats']['in_zone_length_m'] > 30
    assert detour['ground_stats']['in_zone_length_m'] == 0.0
    assert detour['cost'] > cross['cost']                       # detour is geometrically longer
    assert detour['risk_cost'] < cross['risk_cost']             # but risk-cheaper
    assert capped['ground_stats']['in_zone_length_m'] > 30      # small weight keeps the crossing
    assert capped['speed_profile'] and all(v <= 0.35 + 1e-9 for _s0, _s1, v in capped['speed_profile'])
    assert not cross.get('speed_profile')                        # ignore policy executes uncapped
    assert not detour['speed_profile']                           # detour never enters the zone
    for r in (cross, detour, capped):
        assert r['ground_risk']['knowledge'] == 'known_map_v1'
        assert math.isclose(r['geo_length_m'], r['cost'], abs_tol=5e-3)


def test_rejected_policies_and_unsupported_conditions():
    inst = ground_inst(objective='makespan')
    with pytest.raises(ValueError, match='total_distance'):
        fleet.solve({}, inst, 'pyvrp.fleet', {'time_limit_s': 1, 'seed': 1, 'ground_policy': 'risk_adjust'})
    rows = ground_inst()
    rows['vehicles']['travel'] = 'rows'
    rows['plots'] = [{'id': 'P1', 'name': 'P1', 'polygon': [[6, 6], [26, 6], [26, 20], [6, 20]]}]
    with pytest.raises(ValueError, match='自由通行'):
        fleet.solve({}, rows, 'pyvrp.fleet', {'time_limit_s': 1, 'seed': 1, 'ground_policy': 'risk_adjust'})
    no_ground = instance()
    with pytest.raises(ValueError, match='ground'):
        fleet.solve({}, no_ground, 'pyvrp.fleet', {'time_limit_s': 1, 'seed': 1, 'ground_policy': 'risk_adjust'})
    with pytest.raises(ValueError, match='ground_policy'):
        fleet.solve({}, inst, 'pyvrp.fleet', {'time_limit_s': 1, 'seed': 1, 'ground_policy': 'magic'})


def test_checker_rejects_tampered_risk_costs():
    inst = ground_inst()
    routes, *_meta, status, _trace = fleet.solve({}, inst, 'pyvrp.fleet',
                                                 {'time_limit_s': 2, 'seed': 42, 'ground_policy': 'risk_adjust'})
    assert status == 'feasible'
    good = fleet.check(inst, routes)
    assert good['valid'], good['errors']
    tampered = [dict(r) for r in routes]
    tampered[0]['risk_cost'] = tampered[0]['risk_cost'] - 5
    bad = fleet.check(inst, tampered)
    assert not bad['valid'] and any('风险口径' in e for e in bad['errors'])
    intact = [dict(r) for r in routes]
    assert fleet.check(inst, intact)['valid']   # meta presence alone doesn't bypass the recompute


def test_speed_profile_intervals():
    zones = [{'rect': [0, 0, 2, 2], 'mu': 0.1}]
    path = [(-2, 1), (4, 1)]
    prof = speed_profile(path, zones, {'mu_threshold': 0.5, 'v_limit_mps': 0.3}, 1.0)
    assert len(prof) == 1 and math.isclose(prof[0][0], 0.0) and math.isclose(prof[0][1], 6.0)
    assert prof[0][2] == 0.3
    assert speed_profile(path, [{'rect': [0, 0, 2, 2], 'mu': 0.9}],
                         {'mu_threshold': 0.5, 'v_limit_mps': 0.3}, 1.0) == []


def test_route_ground_stats_totals():
    zones = [{'rect': [0, 0, 2, 2], 'mu': 0.2}]
    stats = route_ground_stats([(-2, 1), (4, 1)], zones, 1.0)
    assert stats['geo_length_m'] == 6.0 and stats['in_zone_length_m'] == 2.0
    assert stats['mean_mu_deficit'] == pytest.approx(1.6 / 6, abs=1e-4)
    assert stats['risk_adjusted_length_m'] == pytest.approx(6 + 1.6)


def test_actuator_profile_and_sdf_audit_branch(tmp_path):
    from ceres_lab import field_physics, fieldworld
    from test_backend_integrity import instance as base_inst, solution
    base = field_physics.profile()
    assert base['actuator_mode'] == 'velocity_servo' and base['wheel_torque_limit_nm'] == 8.0
    torque = field_physics.profile({'actuator_mode': 'torque_limited', 'wheel_torque_limit_nm': 2.0})
    assert torque['actuator_mode'] == 'torque_limited' and torque['sha256'] != base['sha256']
    assert 'bounded wheel torque experiment' in torque['assumptions']['actuator']
    for bad in ({'actuator_mode': 'magic'}, {'wheel_torque_limit_nm': 0.01}, {'wheel_torque_kp': 99}):
        with pytest.raises(ValueError):
            field_physics.profile(bad)
    inst = base_inst()
    routes = solution()['routes']
    for mode, physics in (('servo', None), ('torque', {'actuator_mode': 'torque_limited', 'wheel_torque_limit_nm': 4.0})):
        world = fieldworld.build(inst, routes, tmp_path / mode, 'w', 4, physics)
        assert world['physics_audit']['valid'], world['physics_audit']['errors']
    # torque world must not contain any velocity-servo plugin on the driven wheels
    text = (tmp_path / 'torque' / 'world.sdf').read_text()
    assert 'DiffDrive' not in text and 'JointController' in text and 'JointStatePublisher' in text
