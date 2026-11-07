"""Field operations: catalog integrity, turn geometry, solvers, the independent checker and the execution bridge."""
import copy
import json
import math
import re
from pathlib import Path

import pytest

from urp_lab import recommend
from urp_lab.agri import api, catalog as cat, check as checker, execution, mission, rig as rigs, solve, turns, worklines
from urp_lab.scenarios.validate import PROBLEM_CLASSES, validate_instance
from urp_lab.solvers import catalog as algorithm_catalog
from urp_lab.solvers.fleet import check as check_fleet

BOOM = dict(environment='open_field', crop='wheat', operation='spraying_blanket', platform='row_tractor_120', implement='boom_mounted',
            field={'shape': 'rect', 'width_m': 300, 'height_m': 200}, overrides={'rate_per_ha': 150})
ORCHARD = dict(environment='orchard', crop='apple', operation='spraying_blanket', platform='jd_5075gn', implement='air_blast',
               field={'shape': 'trapezoid', 'width_m': 200, 'height_m': 100}, overrides={'rate_per_ha': 500}, fleet=2)
ROBOT = dict(environment='open_field', crop='wheat', operation='spraying_blanket', platform='field_diffdrive', implement='spray_module',
             field={'shape': 'rect', 'width_m': 24, 'height_m': 14}, overrides={'rate_per_ha': 500, 'capacity': 3}, fleet=2)


def compiled(m):
    out = mission.compile_mission(m)
    return out, validate_instance('field_operation', out['instance'])


def plan(m, algorithm='agri.split_anneal', **params):
    _, inst = compiled(m)
    routes, chk, metrics, raw, status, trace = solve.solve({}, inst, algorithm, {'time_limit_s': 1, 'seed': 7, **params})
    return inst, routes, chk, metrics, raw, status


# catalog ---------------------------------------------------------------------------------------------------------

def test_every_parameter_cites_a_registered_source():
    data = cat.load()
    assert data['schema'] == cat.SCHEMA and len(data['environments']) == 14 and len(data['operations']) == 30
    for group in ('platforms', 'implements', 'operations'):
        for item in data[group]:
            for source in cat._param_sources(item):
                assert cat.by_id('sources', source)


def test_catalog_ids_match_the_frontend_taxonomy():
    text = (Path(__file__).parents[2] / 'frontend/src/studio/agri/taxonomy.ts').read_text(encoding='utf-8')
    block = lambda name: text[text.index(f'export const {name}'):text.index('];', text.index(f'export const {name}'))]
    ids = lambda name: re.findall(r"\{ id: '([a-z_]+)'", block(name))
    assert ids('ENVIRONMENTS') == [e['id'] for e in cat.load()['environments']]
    assert ids('OPERATIONS') == [o['id'] for o in cat.load()['operations']]


def test_crop_spacing_is_the_lane_model_not_a_second_copy():
    from urp_lab import lanes
    for crop in cat.load()['crops']:
        if crop['lane']:
            assert crop['row_spacing_m']['v'] == lanes.CROPS[crop['lane']][0]


def test_d497_values_are_carried_verbatim():
    op = {o['id']: o for o in cat.load()['operations']}
    assert op['spraying_blanket']['speed']['v'] == 10.5 and op['spraying_blanket']['efficiency']['v'] == .65
    assert op['combine_harvest']['speed']['r'] == [3.0, 6.5]
    assert op['fertilizing']['efficiency']['r'] == [.6, .8]


def test_draft_power_follows_d497_equation():
    plow = cat.by_id('implements', 'moldboard_plow')
    # Fine soil: D = 1.0 · (652 + 5.1·7²) · 1.6 m · 20 cm = 28 860 N, at 7 km/h → 56.1 kW; medium soil F = 0.70
    assert rigs.draft_kw(plow, 7, 1.6, None, soil=0) == pytest.approx(56.1, abs=.2)
    assert rigs.draft_kw(plow, 7, 1.6, None) == pytest.approx(39.3, abs=.2)


# geometry --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('pitch,radius', [(12, 5.5), (3, 3), (4, 4.4), (1.5, 7), (20, 3), (6, 3)])
def test_closed_form_turn_equals_dubins_geometry(pitch, radius):
    q0 = (0, 0, math.pi / 2)
    q1 = (pitch, 0, -math.pi / 2)
    pts = turns.dubins(q0, q1, radius, step=.01)
    length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
    assert length == pytest.approx(turns.forward_length(pitch, radius), rel=2e-3)
    assert max(p[1] for p in pts) == pytest.approx(turns.forward_depth(pitch, radius), abs=.02)


def test_bochtis_turn_types_meet_at_twice_the_radius():
    r = 4.
    assert turns.turn_kind(2 * r, r) == 'P' and turns.turn_kind(2 * r - .01, r) == 'U'
    assert turns.forward_length(2 * r, r) == pytest.approx(math.pi * r)
    assert turns.forward_length(2 * r - 1e-9, r) == pytest.approx(math.pi * r, abs=1e-3)


def test_reverse_turn_legs_join_and_land_on_the_next_line():
    r, d = 5., 3.
    legs = turns.reverse_turn((0, 0, math.pi / 2), (d, 0, -math.pi / 2), r)
    assert math.dist(legs[0][-1], legs[1][0]) < 1e-9 and math.dist(legs[1][-1], legs[2][0]) < 1e-9
    assert math.dist(legs[2][-1], (d, 0)) < 1e-6
    total = sum(math.dist(a, b) for leg in legs for a, b in zip(leg, leg[1:]))
    assert total == pytest.approx(turns.reverse_length(d, r), rel=1e-3)


def test_fillet_respects_radius_and_refuses_short_segments():
    pts = turns.fillet([[0, 0], [20, 0], [20, 20]], 5)
    assert checker._max_curvature(pts) <= 1 / 5 * 1.02
    assert turns.fillet([[0, 0], [2, 0], [2, 2]], 5) is None


def test_lines_cover_the_working_area_and_split_around_obstacles():
    field = mission.shape_ring('rect', 100, 60)
    plain = worklines.generate(field, [], 5, 0, 5)
    assert len(plain) == 10 and all(x['length_m'] == pytest.approx(90) for x in plain)
    hole = [[40, 25], [60, 25], [60, 35], [40, 35]]
    split = worklines.generate(field, [hole], 5, 0, 5)
    rows = {}
    for x in split:
        rows.setdefault(x['row'], []).append(x)
    assert max(len(v) for v in rows.values()) == 2


def test_line_ends_pull_back_from_sharp_corners():
    wedge = [[0, 0], [120, 0], [0, 60]]
    lines = worklines.generate(wedge, [], 6, 0, 6)
    trimmed = worklines.trim_for_turning(lines, wedge, [], 1.5, 5)
    assert sum(x['length_m'] for x in trimmed) < sum(x['length_m'] for x in lines)
    assert [x['id'] for x in trimmed] == list(range(1, len(trimmed) + 1))
    assert worklines.trim_for_turning(lines, wedge, [], 1.5, 0) == lines


def test_alley_pitch_follows_planting_and_row_pitch_rounds_to_rows():
    out, _ = compiled(ORCHARD)
    assert out['pitch_m'] == 4.0 and out['pattern'] == 'alley'
    row = dict(BOOM, crop='corn')
    out, _ = compiled(row)
    assert out['pitch_m'] == 12.0 and out['pattern'] == 'rows'


# compatibility ---------------------------------------------------------------------------------------------------

def checks_of(m):
    return {c['id']: c for c in mission.compile_mission(m)['checks']}


def test_wheeled_rig_is_refused_in_paddy_and_narrow_vineyard_blocks_guss():
    assert checks_of(dict(BOOM, environment='paddy', crop='rice'))['environment']['level'] == 'block'
    assert checks_of(dict(ORCHARD, environment='vineyard', crop='grape', platform='guss_orchard', implement='guss_sprayer'))['alley']['level'] == 'block'


def test_operation_must_belong_to_the_environment():
    barn = dict(BOOM, environment='livestock_barn', crop=None, operation='tillage', platform='robotti_150d', implement='disk_harrow')
    assert checks_of(barn)['fit']['level'] == 'block'
    assert 'fit' not in checks_of(BOOM)
    for env in cat.load()['environments']:
        assert set(env['line_operations']) <= {o['id'] for o in cat.load()['operations'] if o['family'] == 'line'}


def test_power_block_names_the_figures():
    c = checks_of(dict(environment='open_field', crop=None, operation='tillage', platform='jd_5075gn', implement='moldboard_plow',
                       field={'shape': 'rect', 'width_m': 200, 'height_m': 100}, overrides={'work_width_m': 3.0}))['power']
    assert c['level'] == 'block' and 'kW' in c['detail'] and c['fix']


def test_point_operation_says_where_to_go():
    out = mission.compile_mission(dict(BOOM, operation='sampling'))
    assert out['instance'] is None and out['checks'][0]['id'] == 'family' and out['checks'][0]['fix']


def test_blocked_mission_cannot_become_a_scenario():
    with pytest.raises(ValueError):
        api.scenario(dict(BOOM, environment='paddy', crop='rice'))


def test_narrow_headland_is_flagged_and_too_narrow_blocks():
    assert checks_of(dict(ORCHARD, headland_m=7))['headland']['level'] == 'warn'
    assert checks_of(dict(ORCHARD, headland_m=3))['headland']['level'] == 'block'


def test_sealed_fields_are_refused_before_any_geometry_is_read():
    with pytest.raises(ValueError, match='封存'):
        mission.compile_mission(dict(BOOM, field={'field_id': 'lt_field_3'}))


def test_angle_follows_the_long_side_when_left_open():
    out = mission.compile_mission(dict(BOOM, angle_deg=None, field={'ring': [[0, 0], [100, 100], [80, 120], [-20, 20]]}))
    assert out['instance']['angle_deg'] == pytest.approx(45, abs=.1)


def test_only_the_reference_robot_reaches_simulation():
    tiers = {p['id']: p['tier'] for p in cat.load()['platforms']}
    assert [k for k, v in tiers.items() if v == 'simulation'] == ['field_diffdrive']


# solving ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('m', [BOOM, ORCHARD, ROBOT])
@pytest.mark.parametrize('algorithm', ['agri.sequential', 'agri.skip', 'agri.split_anneal'])
def test_plans_pass_the_independent_checker(m, algorithm):
    inst, routes, chk, metrics, raw, status = plan(m, algorithm)
    assert status == 'feasible' and chk['valid'], chk
    assert metrics['lines_served'] == metrics['lines_total'] == len(inst['lines'])


def test_ortools_is_an_independent_feasible_sequencer():
    _, _, chk, metrics, _, status = plan(BOOM, 'ortools.field')
    assert status == 'feasible' and chk['valid'] and metrics['nonworking_distance_m'] > 0


def test_annealing_never_loses_to_the_baselines_on_its_objective():
    base = [plan(BOOM, a)[3]['nonworking_distance_m'] for a in ('agri.sequential', 'agri.skip')]
    assert plan(BOOM)[3]['nonworking_distance_m'] <= min(base) + 1e-6


def test_fixed_seed_and_iterations_reproduce_the_plan():
    a = plan(ORCHARD, iterations=2000)
    b = plan(ORCHARD, iterations=2000)
    assert a[1] == b[1]


def test_refills_appear_when_the_tank_runs_out():
    m = dict(BOOM, overrides={'rate_per_ha': 150, 'capacity': 200})
    inst, routes, chk, metrics, _, status = plan(m)
    assert status == 'feasible' and metrics['refills'] >= 4
    for route in routes:
        for trip in route['trips']:
            used = sum(next(x for x in inst['lines'] if x['id'] == t)['demand'] for t in trip)
            assert used <= 200 + 1e-6


def test_exact_small_instance_bounds_the_heuristic():
    m = dict(BOOM, field={'shape': 'rect', 'width_m': 110, 'height_m': 80}, overrides={'rate_per_ha': 150, 'capacity': 40})
    _, inst = compiled(m)
    assert len(inst['lines']) <= 12
    _, chk, exact, raw, status, _ = solve.solve({}, inst, 'agri.exact', {'time_limit_s': 20})
    assert status == 'feasible' and chk['valid']
    heuristic = plan(m)[3]
    assert exact['nonworking_distance_m'] <= heuristic['nonworking_distance_m'] * 1.03


def test_or_tools_never_drops_a_line():
    irregular = dict(BOOM, field={'shape': 'pentagon', 'width_m': 300, 'height_m': 200}, angle_deg=20)
    inst, routes, chk, metrics, _, status = plan(irregular, 'ortools.field')
    assert metrics.get('lines_served', len(inst['lines'])) == len(inst['lines']) or status == 'no_solution'
    assert not any('只含' in e for e in chk.get('errors', []))


def test_thin_headland_fails_with_a_reason_not_a_crash():
    m = dict(ROBOT, headland_m=.4)
    _, inst = compiled(m)
    routes, chk, _, _, status, _ = solve.solve({}, inst, 'agri.sequential', {'time_limit_s': 1})
    assert status in ('feasible', 'no_solution')
    assert status == 'feasible' or chk['errors']


def test_turn_radius_changes_the_plan():
    wide = plan(dict(ORCHARD, fleet=1, objective='nonworking'), 'agri.sequential')[3]
    tight = plan(dict(ORCHARD, fleet=1, objective='nonworking', overrides={'rate_per_ha': 500, 'min_turn_radius_m': 1.5}), 'agri.sequential')[3]
    assert tight['nonworking_distance_m'] < wide['nonworking_distance_m']
    assert 'P' in tight['turn_counts']


def test_makespan_objective_uses_every_machine():
    inst, routes, chk, metrics, _, status = plan(dict(ORCHARD, fleet=2))
    assert inst['objective'] == 'makespan' and len(routes) == 2


# checker ---------------------------------------------------------------------------------------------------------

def tampered(mutate, m=BOOM):
    inst, routes, chk, metrics, _, _ = plan(m)
    assert chk['valid']
    routes = copy.deepcopy(routes)
    mutate(inst, routes)
    return checker.check(inst, routes)[0]


def test_checker_finds_a_skipped_line():
    def drop(inst, routes):
        segs = routes[0]['segments']
        i = next(k for k, s in enumerate(segs) if s['kind'] == 'work')
        del segs[i]
    out = tampered(drop)
    assert not out['valid'] and any('未作业' in e or '不连续' in e for e in out['errors'])


def test_checker_finds_a_smaller_tank():
    def shrink(inst, routes):
        inst['rig']['resource']['capacity'] = 10
    out = tampered(shrink, dict(BOOM, overrides={'rate_per_ha': 150, 'capacity': 200}))
    assert not out['valid'] and any('超出容量' in e for e in out['errors'])


def test_checker_finds_a_sharper_radius():
    def tighten(inst, routes):
        inst['rig']['min_turn_radius_m'] = 50
    out = tampered(tighten)
    assert not out['valid'] and any('曲率' in e for e in out['errors'])


def test_checker_finds_an_understated_time():
    def rush(inst, routes):
        for s in routes[0]['segments']:
            if s['kind'] == 'work':
                s['duration_s'] = 1
        routes[0]['duration_s'] = sum(s['duration_s'] for s in routes[0]['segments'])
    out = tampered(rush)
    assert not out['valid'] and any('几何下限' in e for e in out['errors'])


def test_checker_finds_a_body_outside_the_field():
    def widen(inst, routes):
        inst['rig']['clearance_width_m'] = 40
    assert not tampered(widen)['valid']


def test_field_efficiency_is_inside_the_d497_band_for_a_boom_sprayer():
    metrics = plan(BOOM)[3]
    assert .5 <= metrics['field_efficiency'] <= .8


# integration -----------------------------------------------------------------------------------------------------

def test_problem_class_and_algorithms_are_registered():
    assert 'field_operation' in PROBLEM_CLASSES
    ids = {a['id'] for a in algorithm_catalog() if 'field_operation' in a['problem_classes']}
    assert ids == {'agri.split_anneal', 'ortools.field', 'agri.skip', 'agri.sequential', 'agri.exact'}


def test_instance_validation_rejects_a_line_larger_than_the_tank():
    _, inst = compiled(BOOM)
    inst['rig']['resource']['capacity'] = .1
    with pytest.raises(ValueError, match='容量'):
        validate_instance('field_operation', inst)


def test_recommendation_reads_turn_ratio_and_tank():
    sc = api.scenario(ORCHARD)
    rec = recommend.recommend(sc, validate_instance('field_operation', sc['instance']))
    assert rec['recommended'] == 'agri.split_anneal'
    assert any(f['key'] == 'turn_ratio' and f['value'] > 1 for f in rec['features'])


def test_planning_rig_cannot_enter_simulation():
    inst, routes, chk, *_ = plan(BOOM)
    run = {'state': 'done', 'status': 'feasible', 'check': chk, 'routes': routes, 'instance': inst}
    with pytest.raises(execution.NotExecutable, match='没有执行模型'):
        execution.fleet_view(run)


def test_reference_robot_plan_passes_the_fleet_checker_unchanged():
    inst, routes, chk, *_ = plan(ROBOT)
    run = {'state': 'done', 'status': 'feasible', 'check': chk, 'routes': routes, 'instance': inst}
    view = execution.fleet_view(run)
    fleet = validate_instance('fleet_routing', view['instance'])
    result = check_fleet(fleet, view['routes'], {})
    assert result['valid'], result['errors']
    assert [p for r in view['routes'] for p in r['polyline']] == [p for r in routes for p in r['polyline']]


def test_record_survives_json_round_trip():
    inst, routes, *_ = plan(ROBOT)
    assert json.loads(json.dumps(routes)) == routes
