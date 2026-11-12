"""Field missions use confirmed contracts, real jobs and the same mandatory verification gates."""
import copy
import threading

import pytest

from ceres_lab.agent import contract as tc, diagnose, tools
from ceres_lab.agent.runner import AgentRunner, RunnerError
from ceres_lab.agent.session import new_session
from ceres_lab.agri import api as agri_api
from ceres_lab.scenarios import save_user_scenario
from ceres_lab.solvers import run_algorithm
from test_agent_runner_guards import lab, Scripted, PROVIDERS, wait
from test_field_operation import ROBOT


def contract():
    c, _, rejected = tc.apply(tc.empty('田间全覆盖'), {'field_mission': ROBOT, 'compare_count': 1, 'time_limit_s': .2}, 'user_edit')
    assert not rejected
    return tc.confirm(c)


def test_mission_schema_rejects_bad_values_and_requires_explicit_field():
    schema = tools.registry()['solve_field_operation'].parameters
    assert not tools.validate(schema, {'mission': ROBOT, 'algorithms': ['agri.sequential']})
    for mission in ({'environment': 'open_field'}, {**ROBOT, 'fleet': 0}, {**ROBOT, 'unrecognised': True},
                    {**ROBOT, 'overrides': {'speed_kmh': float('nan')}}, {**ROBOT, 'platform': 'invented'}):
        assert tools.validate(schema, {'mission': mission})
    with pytest.raises(ValueError, match='完整长宽'):
        tc.normalize('field_mission', {**ROBOT, 'field': {}})
    assert tc.missing(contract()) == []
    assert tc.cards(contract())[0]['details']


def test_field_contract_does_not_silently_drop_point_constraints():
    c = contract()
    c, _, _ = tc.apply(c, {'vehicle_count': 3}, 'user_edit')
    with pytest.raises(ValueError, match='vehicle_count'):
        tc.confirm(c)
    c, _, _ = tc.apply(c, {'vehicle_count': 2}, 'user_edit')
    assert tc.confirm(c)['status'] == 'confirmed'
    # Hybrid mode populates consistent shared fields via rule parsing before the LLM sets field_mission.
    hybrid = tc.from_rule_parse('24×14米矩形麦田，2台机器人从西南角出发喷施')
    hybrid, _, rejected = tc.apply(hybrid, {'field_mission': ROBOT}, 'llm')
    assert not rejected and tc.confirm(hybrid)['status'] == 'confirmed'


def test_field_tool_requires_confirmation_and_cannot_replace_confirmed_mission(lab):
    s = new_session('rule', None)
    ctx = tools.ToolContext(lab, s)
    denied = tools.execute(tools.registry(), ctx, 'solve_field_operation', {'mission': ROBOT})
    assert denied['error']['code'] == 'contract_not_confirmed'
    s['contract'] = contract()
    denied = tools.execute(tools.registry(), ctx, 'solve_field_operation', {'mission': {**ROBOT, 'fleet': 3}})
    assert denied['error']['code'] == 'mission_changed'
    assert not s['run_ids'] and not s['scenario_id']


def test_field_recheck_catches_geometry_and_metric_tampering():
    sc = agri_api.scenario(ROBOT)
    result = run_algorithm(sc, sc['instance'], 'agri.sequential', {'seed': 7})
    run = {**result, 'run_id': 'field-test', 'state': 'done', 'problem_class': 'field_operation', 'instance': sc['instance']}
    assert run['status'] == 'feasible', run
    assert diagnose.recheck(sc, run)['agrees'] is True
    tampered = copy.deepcopy(run)
    tampered['metrics']['makespan_s'] += 100
    verdict = diagnose.recheck(sc, tampered)
    assert verdict['valid'] is True and verdict['agrees'] is False
    tampered = copy.deepcopy(run)
    tampered['routes'][0]['segments'] = []
    assert not diagnose.recheck(sc, tampered)['valid']


def test_field_tool_creates_real_runs_and_enters_verification_and_physics(lab):
    script = Scripted([
        {'calls': [('update_task_contract', {'fields': {'field_mission': ROBOT, 'compare_count': 1}})]},
        {'content': '任务待确认。'},
        {'calls': [('solve_field_operation', {'algorithms': ['agri.sequential']})]},
        {'content': '规划已结束。'},
        {'content': '方案依据独立检查及物理预演记录，农艺效果尚未验证。'},
    ])
    runner = AgentRunner(lab, chat=script, providers=PROVIDERS)
    lab.agent = runner
    sid = runner.create({'mode': 'llm', 'text': '按测试田间任务规划'})['session_id']
    s = wait(runner, sid)
    assert s['state'] == 'awaiting_confirmation' and not s['run_ids']
    runner.control(sid, 'confirm')
    s = wait(runner, sid)
    assert s['state'] == 'done', s.get('error')
    assert len(s['active_run_ids']) == 1 and not runner._unmet(s)
    rid = s['active_run_ids'][0]
    assert lab.store.get_run(rid)['problem_class'] == 'field_operation'
    assert s['verifications'][rid]['valid'] and s['verifications'][rid]['agrees']
    assert rid in s['physics']
    assert {'solve_field_operation', 'verify_run', 'physics_check'} <= {c['name'] for c in s['tool_calls']}
    assert s['report']['evidence']['best_verified']['run_id'] == rid
    assert s['report']['evidence']['problem_class'] == 'field_operation'
    assert not lab.test_sim_starts


def test_rule_agent_can_bind_field_map_and_complete_current_mission(lab):
    sc = save_user_scenario(agri_api.scenario(ROBOT))
    r = AgentRunner(lab, providers=[])
    lab.agent = r
    sid = r.create({'mode': 'rule', 'scenario_id': sc['id']})['session_id']
    r.edit_contract(sid, {'compare_count': 1, 'time_limit_s': .2})
    r.control(sid, 'confirm')
    s = wait(r, sid)
    assert s['state'] == 'done', s.get('error')
    assert s['report']['evidence']['best_verified'] and s['problem_class'] == 'field_operation'
    assert '不验证机具' in s['report']['narrative']
    assert all('None' not in st['detail'] for st in s['plan'])
    assert not r._unmet(s)


def test_semantic_clarification_is_allowed_after_counts_are_known(lab):
    s = new_session('rule', None)
    s['contract'] = tc.from_rule_parse('60×40米果园，2台车，8个采样点')
    ctx = tools.ToolContext(lab, s)
    args = {'questions': [{'question': '两个同名补给站指哪一个？', 'options': ['东侧', '西侧']}],
            'ambiguity': '地图有两个同名补给站，无法确定用户所指的位置'}
    for phase in ('understand', 'execute'):
        s['phase'] = phase
        rec = tools.execute(tools.registry(), ctx, 'ask_user', args)
        assert rec['status'] == 'ok' and s['pending']['type'] == 'questions'
    rec = tools.execute(tools.registry(), ctx, 'ask_user', {'questions': [{'question': '要什么默认车速？'}]})
    assert rec['error']['code'] == 'question_not_needed'


def test_field_map_with_stale_mission_is_rejected_before_confirmation(lab):
    sc = agri_api.scenario(ROBOT)
    sc['instance']['machines'] = 3
    sc = save_user_scenario(sc)
    runner = AgentRunner(lab, providers=[])
    lab.agent = runner
    with pytest.raises(RunnerError, match='实例与作业定义不一致') as exc:
        runner.create({'mode': 'rule', 'scenario_id': sc['id']})
    assert exc.value.code == 'invalid_mission'
    assert not runner.list()


@pytest.mark.parametrize('changed', [False, True])
def test_field_takeover_cannot_reuse_old_progress_or_skip_confirmation(lab, changed):
    sc = save_user_scenario(agri_api.scenario({**ROBOT, 'fleet': 3} if changed else ROBOT))
    runner = AgentRunner(lab, providers=[])
    lab.agent = runner
    s = new_session('rule', None)
    s.update(contract=contract(), state='done', phase='report', solved_since_build=True,
             rule_progress={'built_revision': contract()['revision'], 'solved': True})
    runner.store.save(s)
    sid = s['session_id']
    runner.control(sid, 'takeover', {'step': 'scenario', 'scenario_id': sc['id']})
    runner.control(sid, 'resume')
    result = wait(runner, sid)
    if changed:
        assert result['state'] == 'awaiting_confirmation' and not result['run_ids']
        runner.control(sid, 'confirm')
        result = wait(runner, sid)
    assert result['state'] == 'done', result.get('error')
    assert result['active_run_ids'] and not runner._unmet(result)
    assert result['contract']['status'] == 'confirmed'
    assert next(st for st in result['plan'] if st['kind'] == 'scenario')['status'] in ('done', 'taken_over')
    assert all('None' not in st['detail'] for st in result['plan'])
