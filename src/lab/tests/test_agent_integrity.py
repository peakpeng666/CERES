"""Agent evidence follows the confirmed build, including edits that retain the scenario id."""
import copy
import json
from types import SimpleNamespace

import pytest

from ceres_lab import paths
from ceres_lab.agent import contract as tc
from ceres_lab.agent import tools
from ceres_lab.agent.session import AgentStore, new_session
from ceres_lab.scenarios import get_scenario, save_user_scenario
from ceres_lab.solvers import run_algorithm


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    session = new_session('rule', None)
    session['contract'] = tc.confirm(tc.from_rule_parse('60×40米果园，2台车，8个采样点，每车容量4，1个池塘'))
    session['phase'] = 'execute'
    runs = {}
    lab = SimpleNamespace(store=SimpleNamespace(get_run=runs.get))
    ctx = tools.ToolContext(lab, session)
    tools.build_scenario(ctx, {})
    return ctx, runs


def _edit(ctx, fields):
    contract, accepted, rejected = tc.apply(ctx.session['contract'], fields, 'user_edit')
    assert accepted and not rejected
    ctx.session['contract'] = tc.confirm(contract)


def _run(ctx, runs, rid='r1'):
    scenario = get_scenario(ctx.session['scenario_id'])
    result = run_algorithm(scenario, scenario['instance'], 'panel.savings_fleet', {})
    run = {**result, 'run_id': rid, 'state': 'done', 'problem_class': 'fleet_routing',
           'scenario_id': scenario['id'], 'instance': copy.deepcopy(scenario['instance']), 'params': {}}
    assert run['status'] == 'feasible' and run['check']['valid']
    runs[rid] = run
    ctx.session['run_ids'].append(rid)
    ctx.session['active_run_ids'].append(rid)
    tools.verify_run(ctx, {'run_id': rid})
    ctx.session['solved_since_build'] = True
    return run


def test_changed_capacity_isolates_old_result_without_erasing_history(context):
    ctx, runs = context
    old = _run(ctx, runs)
    old_verdict = copy.deepcopy(ctx.session['verifications']['r1'])
    _edit(ctx, {'vehicle_capacity': 1})
    assert tools.current_run_ids(ctx.session) == []
    rebuilt = tools.build_scenario(ctx, {})
    assert rebuilt['mode'] == 'updated' and rebuilt['scenario_id'] == old['scenario_id']
    assert get_scenario(rebuilt['scenario_id'])['instance']['vehicles']['capacity'] == 1
    assert ctx.session['run_ids'] == ['r1'] and ctx.session['active_run_ids'] == []
    assert ctx.session['verification_history']['r1'] == old_verdict
    assert not tools.run_matches_current(ctx.session, old)
    call = tools.execute(tools.registry(), ctx, 'start_simulation', {'run_id': 'r1'})
    assert call['status'] == 'refused' and call['error']['code'] == 'stale_run'
    assert tools.compare_runs(ctx, {})['groups'] == []


def test_identical_rebuild_preserves_evidence_and_simulation_rejection(context):
    ctx, runs = context
    old = _run(ctx, runs)
    decision = {'contract_revision': ctx.session['contract']['revision'], 'status': 'rejected', 'run_id': 'r1'}
    ctx.session['simulation_decision'] = decision
    tools.build_scenario(ctx, {})
    assert tools.current_run_ids(ctx.session) == ['r1']
    assert tools.run_matches_current(ctx.session, old)
    assert ctx.session['verifications']['r1']['valid']
    assert ctx.session['solved_since_build']
    assert ctx.session['simulation_decision'] == decision


def test_vehicle_width_changes_geometry_and_same_second_rebuild_has_unique_id(context):
    ctx, _ = context
    previous = get_scenario(ctx.session['scenario_id'])
    _edit(ctx, {'vehicle_width_m': 2.4})
    result = tools.build_scenario(ctx, {})
    current = get_scenario(result['scenario_id'])
    assert result['mode'] == 'replaced' and current['id'] != previous['id']
    assert current['instance']['robot_radius'] == 1.2
    assert get_scenario(previous['id']) == previous


def test_default_seed_is_used_for_map_and_cleared_fields_do_not_reappear(context):
    ctx, _ = context
    scenario = get_scenario(ctx.session['scenario_id'])
    assert scenario['provenance']['seed'] == tc.EXECUTION_DEFAULTS['seed']
    _edit(ctx, {'vehicle_capacity': None, 'obstacles': {}})
    tools.build_scenario(ctx, {})
    scenario = get_scenario(ctx.session['scenario_id'])
    assert not scenario['instance']['obstacles']
    assert scenario['instance']['vehicles']['capacity'] == 6
    assert 'capacity' not in scenario['provenance']['params']['spec']


def test_external_map_edit_is_refused_until_rebuilt(context):
    ctx, _ = context
    scenario = get_scenario(ctx.session['scenario_id'])
    scenario['instance']['tasks'][0]['demand'] = 2
    save_user_scenario(scenario, expected_revision=scenario['revision'])
    call = tools.execute(tools.registry(), ctx, 'recommend_algorithms', {})
    assert call['status'] == 'refused' and call['error']['code'] == 'stale_scenario'


def test_recheck_disagreement_is_not_usable_for_execution(context, monkeypatch):
    ctx, runs = context
    _run(ctx, runs)
    monkeypatch.setattr(tools.diagnose, 'recheck', lambda *_: {'performed': True, 'valid': True, 'agrees': False})
    result = tools.verify_run(ctx, {'run_id': 'r1'})
    assert result['diagnosis'] == 'implementation_error'
    assert ctx.session['verifications']['r1']['valid'] is False
    call = tools.execute(tools.registry(), ctx, 'start_simulation', {'run_id': 'r1'})
    assert call['status'] == 'refused' and call['error']['code'] == 'unverified'


def test_comparison_does_not_rank_a_run_rejected_by_independent_recheck(context):
    ctx, runs = context
    rejected = _run(ctx, runs, 'rejected')
    _run(ctx, runs, 'good1')
    _run(ctx, runs, 'good2')
    rejected['metrics']['total_distance_m'] = 0.01
    rejected['metrics']['makespan_s'] = 0.01
    rejected['metrics']['wall_s'] = 0.01
    ctx.session['verifications']['rejected'] = {'valid': False, 'agrees': False, 'category': 'implementation_error'}
    groups = tools.compare_runs(ctx, {})['groups']
    assert len(groups) == 1 and groups[0]['best']
    row = next(row for row in groups[0]['rows'] if row['run_id'] == 'rejected')
    assert row['check_valid'] is False and row['independent_recheck'] is False
    assert row['diagnosis'] == 'implementation_error' and row['diagnosis_label'] == '软件实现错误'
    assert 'rejected' not in groups[0]['best'].values()


def test_summary_requires_recheck_agreement_even_with_legacy_valid_flag(context):
    ctx, runs = context
    run = _run(ctx, runs)
    summary = tools.run_summary(run, verification={'valid': True, 'agrees': False, 'category': 'implementation_error'})
    assert not summary['check_valid'] and not summary['independent_recheck']
    assert summary['diagnosis'] == 'implementation_error'


@pytest.mark.parametrize('number', [float('nan'), float('inf'), -float('inf'), 10 ** 400])
def test_nonfinite_tool_arguments_are_refused_and_the_refusal_can_be_saved(context, tmp_path, number):
    ctx, _ = context
    call = tools.execute(tools.registry(), ctx, 'solve', {'algorithms': ['panel.savings_fleet'], 'time_limit_s': number})
    assert call['status'] == 'refused' and call['error']['code'] == 'invalid_arguments'
    ctx.session['tool_calls'].append(call)
    store = AgentStore(tmp_path / 'agent.sqlite3')
    try:
        store.save(ctx.session)
        assert store.get(ctx.session['session_id'])['tool_calls'][0]['status'] == 'refused'
    finally:
        store.close()


def test_validation_checks_array_items_after_the_first_two_hundred():
    schema = {'type': 'array', 'items': {'type': 'integer'}, 'maxItems': 300}
    assert tools.validate(schema, [1] * 200 + ['invalid'])


def test_malformed_contract_enum_is_rejected_as_input_error():
    contract, accepted, rejected = tc.apply(tc.empty(), {'objective': [], 'unknown': None}, 'user_edit')
    assert not accepted and len(rejected) == 2 and contract['revision'] == 0


def test_model_cannot_clear_stated_values_or_forge_new_user_evidence(context):
    ctx, _ = context
    contract, accepted, rejected = tc.apply(ctx.session['contract'], {'vehicle_count': None}, 'llm')
    assert not accepted and rejected and tc.value(contract, 'vehicle_count') == 2
    ctx.session['messages'].append({'role': 'user', 'text': '继续执行'})
    result = tools.update_task_contract(ctx, {'fields': {'vehicle_count': 5}, 'evidence': {'vehicle_count': '改成5台车'}})
    assert not result['accepted'] and result['rejected'] and tc.value(ctx.session['contract'], 'vehicle_count') == 2
    ctx.session['messages'].append({'role': 'user', 'text': '改成5台车'})
    result = tools.update_task_contract(ctx, {'fields': {'vehicle_count': 5}, 'evidence': {'vehicle_count': '改成5台车'}})
    assert result['accepted'] and tc.value(ctx.session['contract'], 'vehicle_count') == 5


def test_new_unsupported_requirement_invalidates_confirmation(context):
    ctx, _ = context
    previous = ctx.session['contract']
    current = tc.add_unsupported(previous, ['上午9点前完成'])
    assert current['status'] == 'draft' and current['revision'] == previous['revision'] + 1
    assert tc.add_unsupported(current, ['上午9点前完成']) == current


def test_study_reports_actual_count_and_preserves_run_ids_on_cancel(context, monkeypatch):
    ctx, runs = context
    run = _run(ctx, runs)
    ctx.session['run_ids'] = []
    ctx.session['active_run_ids'] = []
    studies = SimpleNamespace(
        create=lambda *_: {'study_id': 'study1', 'design': [{'run_id': run['run_id']}], 'skipped': ['确定性算法只运行一个种子']},
        get=lambda *_: {'progress': {'state': 'done'}},
        runs=lambda *_: [run],
    )
    ctx.lab.studies = studies
    ctx.lab.catalog = lambda: []
    result = tools.run_study(ctx, {'algorithms': ['panel.savings_fleet'], 'budgets': [1], 'seeds': [1, 2, 3]})
    assert result['runs'] == 1 and result['requested_runs'] == 3 and result['skipped']
    assert ctx.session['run_ids'] == ['r1'] and ctx.session['active_run_ids'] == ['r1']
    assert ctx.session['solved_since_build']
    ctx.session['run_ids'] = []
    ctx.session['active_run_ids'] = []
    studies.get = lambda *_: {'progress': {'state': 'running'}}
    cancelled = []
    studies.cancel = cancelled.append
    ctx.cancel.set()
    with pytest.raises(tools.ToolError, match='取消'):
        tools.run_study(ctx, {'algorithms': ['panel.savings_fleet'], 'budgets': [1], 'seeds': [1]})
    assert cancelled == ['study1'] and ctx.session['run_ids'] == ['r1']
    assert json.dumps(ctx.session, allow_nan=False)
