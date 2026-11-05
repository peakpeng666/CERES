"""Refresh regressions use synthetic records only, never research evidence."""
import copy
import json

from urp_lab import analysis
from urp_lab.planning import fingerprint


def run(i=0, a='a', cost=10, budget=1, *, pc='turn_routing'):
    inst = ({'arcs': [[0, 1, 1 + i], [1, 0, 1]], 'customers': [{'node': 1, 'demand': 1}],
             'forbidden_turns': [], 'turn_penalties': [], 'capacity': 2}
            if pc == 'turn_routing' else
            {'coordinates': [[0, 0], [1 + i, 0]], 'demands': [0, 1], 'capacity': 2})
    return {'run_id': f'{pc}-{i}-{a}-{budget}', 'scenario_id': f'{pc}-{i}',
            'instance_sha256': fingerprint(inst), 'instance': inst, 'problem_class': pc,
            'algorithm': {'id': a}, 'params': {'seed': 1, 'time_limit_s': budget},
            'state': 'done', 'status': 'feasible', 'check': {'valid': True},
            'objective': {'value': cost, 'unit': 'unitless'}, 'metrics': {'wall_s': .1},
            'scenario_snapshot': {'problem_class': pc, 'provenance': {'params': {
                'analysis_unit': {'schema': 'urp.analysis-unit/1', 'group_id': f'{pc}-{i}', 'independent': True}}}}}


def test_aliases_do_not_multiply_blocks_and_inputs_are_untouched():
    rows = []
    for i in range(11):
        for a in range(7):
            r = run(0, f'a{a}', 10 + a)
            r.update(scenario_id=f'alias-{i}', run_id=f'{i}-{a}')
            rows.append(r)
    before = copy.deepcopy(rows)
    out = analysis.analyze(rows)
    assert out['n_instances'] == 1
    assert out['tests']['friedman']['testable'] is False
    assert len(out['block_contract']['blocks'][0]['scenario_ids']) == 11
    assert out['block_contract']['alias_blocks_collapsed'] == 10
    assert rows == before


def test_legacy_independence_is_unknown_and_declared_groups_are_gated():
    rows = [run(i, f'a{a}', 10 + a) for i in range(11) for a in range(7)]
    assert analysis.analyze(rows)['tests']['friedman']['testable'] is True
    for r in rows:
        r['scenario_snapshot']['provenance']['params'].pop('analysis_unit')
    out = analysis.analyze(rows)
    assert out['tests']['friedman']['reason_code'] == 'independence_not_established'
    assert all(not p['testable'] for p in out['tests']['wilcoxon_holm'])
    for r in rows:
        r['scenario_snapshot']['provenance']['params']['analysis_unit'] = {'schema': 'urp.analysis-unit/1', 'group_id': 'same-base', 'independent': True}
    out = analysis.analyze(rows)
    assert out['block_contract']['n_declared_groups'] == 1
    assert out['tests']['friedman']['reason_code'] == 'dependent_variants_require_design'


def test_anytime_reasons_conserve_total_and_unknown_is_not_infinity():
    r = run()
    r.update(trace=[[.1, 10]], trace_contract={'semantic': 'reported_objective'})
    failed = [dict(copy.deepcopy(r), run_id=f'fail-{i}', state='error', trace=[]) for i in range(9)]
    out = analysis.analyze([r, *failed])['anytime']
    c = out['counts']['a']
    assert c['terminal_runs'] == 10 and c['valid_trace_runs'] == 1
    assert c['reasons']['failed_or_interrupted'] == 9
    assert out['excluded'] == 9
    assert out['observed_counts']['a'][0] == 0
    assert out['median']['a'][0] is None
    assert out['median']['a'][-1] == 0
    assert c['valid_trace_runs'] + sum(c['reasons'].values()) == c['terminal_runs']
    json.dumps(out, allow_nan=False)


def test_instance_space_splits_budget_and_feature_schema_without_order_dependence():
    rows = [run(i, a, cost, budget, pc=pc) for pc in ['turn_routing', 'euclidean_cvrp']
            for i in range(3) for a, cost, budget in [('a', 100, 1), ('a', 1, 10), ('b', 2, 1)]]
    out = analysis.instance_space(rows)
    assert len(out['panels']) == 4
    assert out == analysis.instance_space(list(reversed(rows)))
    for p in out['panels']:
        assert len(p['points']) == 3
        assert p['feature_schema'].startswith(p['problem_class'])
        assert all(pt['best'] == ('b @1s' if p['budget_s'] == 1 else None) for pt in p['points'])
        assert all(pt['run_ids'] and pt['comparison_key'] for pt in p['points'])


def test_instance_space_incomplete_candidates_never_label_a_winner():
    rows = [run(i, a, 10) for i in range(3) for a in ['a', 'b'] if not (i == 1 and a == 'b')]
    out = analysis.instance_space(rows)
    points = out['panels'][0]['points']
    pt = next(p for p in points if p['scenario_id'] == 'turn_routing-1')
    assert pt['best'] is None and pt['winner_status'] == 'incomplete_candidates'


def test_nonparticipating_unknown_block_does_not_veto_complete_inference():
    rows = [run(i, f'a{a}', 10 + a) for i in range(11) for a in range(7)]
    unknown = run(50, 'a0')
    unknown['scenario_snapshot'].pop('provenance')
    out = analysis.analyze([*rows, unknown])
    assert out['tests']['friedman']['testable'] is True
    assert len(out['block_contract']['unknown_blocks']) == 1
    assert out['block_contract']['inference_unknown_blocks'] == []


def test_import_declaration_survives_real_submission_snapshot(monkeypatch):
    from urp_lab import jobs, recommend
    from urp_lab.scenarios import prepare_import
    monkeypatch.setattr(recommend, 'recommend', lambda *a, **kw: {})
    source = dict(python='synthetic', packages={}, native_dependencies={}, python_executable='synthetic',
                  python_prefix='synthetic', node_executable=None, node_version=None, files={}, sha256='synthetic')
    original = run(pc='euclidean_cvrp')
    obj = {'id': 'synthetic-import', 'problem_class': original['problem_class'], 'instance': original['instance'],
           'provenance': original['scenario_snapshot']['provenance']}
    sc = prepare_import(obj)
    record = jobs.skeleton('synthetic-job', sc, sc['instance'], {'id': 'a'}, {'time_limit_s': 1}, None, source)
    record.update(state='done', status='feasible', check={'valid': True}, objective={'value': 10, 'unit': 'euclidean'})
    out = analysis.analyze([record])
    assert out['block_contract']['blocks'][0]['group_id'] == 'declared:euclidean_cvrp-0'
    assert out['block_contract']['inference_eligible'] is True
    assert sc['provenance']['params']['analysis_unit'] == obj['provenance']['params']['analysis_unit']


def test_generator_protocol_groups_same_seed_variants_and_legacy_hash_aliases():
    rows = [run(i) for i in range(2)]
    for r in rows:
        r['scenario_snapshot']['provenance'] = {'generator': 'urp_lab.scenarios.benchmark:cvrp_synthetic', 'seed': 3}
    out = analysis.analyze(rows)
    assert out['block_contract']['n_declared_groups'] == 1
    assert out['block_contract']['reason_code'] == 'dependent_variants_require_design'
    a = run()
    from urp_lab.planning import context
    a['planning'] = context(a['scenario_snapshot'], a['instance'], a, resolve_map=False)
    a.pop('instance')
    b = dict(copy.deepcopy(a), run_id='alias', scenario_id='alias')
    assert analysis.analyze([a, b])['n_instances'] == 1


def test_aliases_cannot_be_split_by_conflicting_user_declarations():
    a, b = run(), run()
    b['scenario_id'] = 'alias'
    b['scenario_snapshot']['provenance']['params']['analysis_unit']['group_id'] = 'forged-new-base'
    out = analysis.analyze([a, b])
    assert out['n_instances'] == 1
    assert out['block_contract']['inference_eligible'] is False


def test_anytime_reason_buckets_and_partial_observation():
    good = run()
    good.update(trace=[[.1, 20], [.5, 10]], trace_contract={'semantic': 'reported_objective'})
    rows = [good]
    variants = [dict(trace=[]), dict(trace_contract={'semantic': 'solver_search_proxy'}),
                dict(trace=[[.5, 10], [.1, 10]]), dict(state='timeout'),
                dict(state='timeout', trace=[], trace_contract={'observation': 'budget_complete_no_solution'}),
                dict(objective={'value': 0, 'unit': 'different'}, trace=[[.1, 0]])]
    rows += [dict(copy.deepcopy(good), run_id=f'reason-{i}', **change) for i, change in enumerate(variants)]
    out = analysis.analyze(rows)['anytime']
    c = out['counts']['a']
    assert c['valid_trace_runs'] == 1 and c['terminal_runs'] == 7
    assert set(c['reasons'].values()) == {1}
    assert c['terminal_runs'] == c['valid_trace_runs'] + sum(c['reasons'].values())
    assert any(n == 0 for n in out['observed_counts']['a'])
    assert all(n in (0, 1) for n in out['observed_counts']['a'])
    assert out['median']['a'][-1] == 0


def test_anytime_malformed_trace_is_reported_not_raised():
    for tr in ([[.1]], [['bad', 10]], [[float('nan'), 10]], [[.1, True]], 'not-a-trace'):
        r = run()
        r.update(trace=tr, trace_contract={'semantic': 'reported_objective'})
        out = analysis.analyze([r])['anytime']
        assert out['counts']['a']['reasons']['invalid_trace'] == 1
        json.dumps(out, allow_nan=False)


def test_space_keeps_versions_parameters_units_and_main_table_winners():
    rows = [run(i, a, v) for i in range(3) for a, v in [('a', 10), ('b', 20)]]
    for i in range(3):
        newer = run(i, 'a', 30)
        newer['algorithm']['version'] = '2'
        rows.append(newer)
        units = run(i, 'b', 1)
        units['objective']['unit'] = 'm'
        rows.append(units)
    space = analysis.instance_space(rows)
    assert len(space['panels']) == 2
    panel = next(p for p in space['panels'] if p['comparison_contract']['objective_unit'] == 'unitless')
    assert panel['algorithms'] == ['a v2', 'a vunknown', 'b']
    table = analysis.analyze([r for r in rows if r['objective']['unit'] == 'unitless'])
    by_key = {p['instance']: p for p in table['per_instance']}
    for pt in panel['points']:
        cells = by_key[pt['comparison_key']]['cells']
        assert pt['best'] == min(cells, key=lambda a: cells[a]['mean']) == 'a vunknown'


def test_legacy_hash_without_full_semantics_stays_isolated_and_frozen_environment_separates():
    a = run()
    a.pop('instance')
    b = dict(copy.deepcopy(a), scenario_id='alias', run_id='alias')
    assert analysis.analyze([a, b])['n_instances'] == 2
    for r, env in [(a, 'map-a'), (b, 'map-b')]:
        r['planning'] = {'schema': 'urp.planning/1', 'instance_sha256': r['instance_sha256'],
                         'comparison_key': 'stale-same-key', 'objective_unit': 'unitless',
                         'environment_sha256': env, 'objective_rule': 'distance'}
    assert analysis.analyze([a, b])['n_instances'] == 2


def test_nonmonotone_reported_trace_is_not_an_incumbent_curve():
    r = run()
    r.update(trace=[[.1, 1], [.2, 10]], trace_contract={'semantic': 'reported_objective'})
    assert not analysis.trace_matches(r)
    assert analysis.analyze([r])['anytime']['counts']['a']['reasons']['invalid_trace'] == 1
