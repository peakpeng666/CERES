"""Adversarial input, analytic cost and actual execution-binding regressions."""
import copy

import pytest

from ceres_lab import fieldworld, ground_execution
from ceres_lab.ground_map import SCHEMA, normalize_ground
from ceres_lab.ground_risk import exposure_line, route_ground_stats, speed_profile
from ceres_lab.solvers import fleet
from test_ground_risk import ground_inst


@pytest.mark.parametrize('mutate', [
    lambda g: g['zones'][0].update(mu=-99),
    lambda g: g['zones'][0].update(rect=[True, 8, 24, 18]),
    lambda g: g['zones'][0].update(risk_rect=[0, 0, 1, 1]),
    lambda g: g['speed_policy'].update(v_limit_mps=100),
    lambda g: g.update(mu_reference=True),
    lambda g: g.update(knowledge={'looks': 'trusted'}),
])
def test_normalized_marker_does_not_bypass_validation(mutate):
    inst = ground_inst()
    mutate(inst['ground'])
    with pytest.raises(ValueError):
        normalize_ground(inst)


def test_all_depots_actual_boundary_and_declared_footprint():
    inst = ground_inst()
    inst['boundary'] = [[0,0], [34,0], [34,26], [26,26], [26,7], [7,7], [7,26], [0,26]]
    with pytest.raises(ValueError, match='多边形'):
        normalize_ground(inst)
    inst = ground_inst()
    inst['depots'] = [[3,3], [10,10]]
    with pytest.raises(ValueError, match='所有车库'):
        normalize_ground(inst)
    inst = ground_inst(ground={'zones': [{'rect': [8,8,24,18], 'mu': .12}]}, robot_radius=.8)
    assert inst['ground']['zones'][0]['risk_rect'] == [7.2, 7.2, 24.8, 18.8]
    assert normalize_ground(inst) == inst['ground']


def test_reference_and_weight_analytic_integrals_and_no_overlap_double_count():
    zones = [{'rect': [0,0,2,2], 'mu': .5}]
    assert route_ground_stats([[-1,1],[3,1]], zones, .5, 2)['risk_adjusted_length_m'] == 4.
    zones[0]['mu'] = .25
    assert route_ground_stats([[-1,1],[3,1]], zones, .5, 2)['risk_adjusted_length_m'] == 6.
    zones = [{'rect': [0,0,2,2], 'risk_rect': [-.5,-.5,2.5,2.5], 'mu': .2},
             {'rect': [2,0,4,2], 'risk_rect': [1.5,-.5,4.5,2.5], 'mu': .5}]
    inside, deficit = exposure_line([[-1,1],[5,1]], zones)
    assert inside == pytest.approx(4.)
    assert deficit == pytest.approx(3*.8 + 2*.5)


@pytest.mark.parametrize('weight', [.2, 2.])
def test_checker_binds_weight_policy_caps_and_frozen_request(weight):
    inst = ground_inst()
    params = {'ground_policy': 'risk_adjust', 'ground_risk_weight': weight}
    routes, checked, *_ = fleet.solve({}, inst, 'panel.sweep', params)
    assert checked['valid'], checked
    assert routes[0]['ground_risk']['weight'] == weight
    for key in ['ground_risk', 'ground_stats', 'speed_profile', 'risk_cost', 'geo_length_m']:
        broken = copy.deepcopy(routes)
        del broken[0][key]
        assert not fleet.check(inst, broken, params)['valid'], key
    # Internally coherent metadata is still invalid if it contradicts the request.
    ignored, *_ = fleet.solve({}, inst, 'panel.sweep', {})
    assert not fleet.check(inst, ignored, params)['valid']


def test_localization_preserves_map_explicit_override_is_recorded(tmp_path):
    inst = ground_inst()
    routes, *_ = fleet.solve({}, inst, 'panel.sweep', {'ground_policy': 'risk_adjust', 'ground_risk_weight': .2})
    execution = ground_execution.resolve({'localization_preset': 'survey'}, inst, routes)
    assert execution['ground']['origin'] == 'instance_ground'
    assert ground_execution.knowledge(inst, execution)['maps_match']
    uniform = ground_execution.resolve({'ground_preset': 'none'}, inst, routes)
    plan = fieldworld.build(inst, routes, tmp_path/'uniform', 'uniform', execution=uniform, inherit_ground=False)
    assert plan['execution'] is None and not plan['ground_knowledge']['maps_match']
    assert not plan['ground_knowledge']['world']
    plan = fieldworld.build(inst, routes, tmp_path/'declared', 'declared')
    vehicle = plan['vehicles'][0]
    assert plan['ground_knowledge']['maps_match']
    expected = speed_profile(vehicle['path'], inst['ground']['zones'], inst['ground']['speed_policy'], 1.)
    assert vehicle['speed_profile'] == expected
    assert vehicle['speed_profile'] != routes[0]['speed_profile']  # parking arcs are now included
    # A centre line outside the zone can still expose the footprint: check segments.
    result = {'rigid_body_poses': {'vehicle_1': [[1,7.6,9], [1.1,7.6,9.08]]}}
    checked = ground_execution.verify(plan, result)
    assert not checked['valid'] and checked['vehicles'][0]['violations'] == 1
    plan['vehicles'][0]['speed_profile'] = []
    assert not ground_execution.verify(plan, result)['data_valid']


def test_risk_policy_and_weight_do_not_share_automatic_comparison_group():
    from ceres_lab.planning import context, comparison_key
    inst = ground_inst()
    scenario = {'problem_class':'fleet_routing', 'frame':{'kind':'field','units':'m'}, 'cost_unit':'m'}
    keys = []
    for policy, weight in [('ignore',1.), ('risk_adjust',.2), ('risk_adjust',1.)]:
        result = {'metrics':{'ground_policy':policy, 'ground_risk_weight':weight}}
        descriptor = context(scenario, inst, result)
        keys.append(descriptor['comparison_key'])
    assert len(set(keys)) == 3
    # Historical cached context cannot bypass the new policy grouping.
    a = {**scenario, 'instance':inst, 'params':{'ground_policy':'risk_adjust','ground_risk_weight':.2},
         'objective':{'unit':'m'}, 'planning':context(scenario, inst)}
    b = copy.deepcopy(a)
    b['params']['ground_risk_weight'] = 1.
    assert comparison_key(a) != comparison_key(b)
