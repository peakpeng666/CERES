"""The agent's robustness step: the two best plans under the same random fields, and what the report says about them."""
import threading
import time

import pytest

import test_agent_runner_guards as guards
from test_agent_runner_guards import FIELDS, PROVIDERS, Scripted, create, lab  # noqa: F401 - the fixture is reused as it stands
from ceres_lab.agent import prompts, tools
from ceres_lab.agent.runner import AgentRunner, _robustness_text, _template_report
from ceres_lab.agent.tools import ToolContext, ToolError
from ceres_lab.physics import PhysicsError


def wait(runner, sid, limit_s=240):
    """Several solvers and two batches take longer than the session the shared helper waits for."""
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        session = runner.store.get(sid)
        if session['state'] != 'running':
            return session
        time.sleep(.03)
    raise AssertionError(f"session stuck in {session['state']}")


@pytest.fixture(autouse=True)
def patient(monkeypatch):
    monkeypatch.setattr(guards, 'wait', wait)


@pytest.fixture
def batches(monkeypatch):
    monkeypatch.setattr(tools, 'ROBUSTNESS_RUNS', 3)


def compare_three(lab):
    return create(lab, [{'calls': [('solve', {'algorithms': ['pyvrp.fleet', 'ortools.fleet', 'panel.sweep']})]}, {'content': '规划阶段结束。'}], simulate=False)


def test_the_two_best_plans_meet_the_same_draws_before_the_report(lab, batches):
    runner, script, sid, session = compare_three(lab)
    assert session['state'] == 'done', session.get('error')
    evidence = session['report']['evidence']['robustness']
    rows = evidence['rows']
    assert len(rows) == 2 and all(r['finished'] == r['requested'] == 3 for r in rows)
    assert len({r['seed'] for r in rows}) == 1 and all(r['intensity'] == 1. for r in rows)
    assert all(r['makespan_s']['n'] == r['passed'] and r['failures'] is not None and '仿真' in r['scope'] for r in rows)
    assert all('samples' not in r for r in rows), 'the report reads the summary, not every run'
    assert rows[0]['run_id'] == session['report']['evidence']['best_executable']['run_id'], 'the first plan is the one the report calls best'
    assert evidence['difference']['pairs'] == 3 and evidence['difference']['a'] == rows[0]['algorithm']
    calls = [c for c in session['tool_calls'] if c['name'] == 'physics_robustness']
    assert len(calls) == len(rows) and all(c['actor'] == 'system' for c in calls)
    assert next(s for s in session['plan'] if s['kind'] == 'robustness')['status'] == 'done'
    message = next(m for m in session['messages'] if m.get('kind') == 'robustness')
    assert message['data']['rows'] == rows
    assert '不是实车可靠性' in message['text']
    assert all(rec['kind'] == 'batch' for rec in lab.store.list_physics_runs() if rec.get('physics_run_id') in {r['physics_run_id'] for r in rows})


def test_the_rule_report_says_what_the_evaluation_found(lab, batches):
    runner = AgentRunner(lab, chat=Scripted([]), providers=PROVIDERS)
    lab.agent = runner
    sid = runner.create({'mode': 'rule', 'auto_confirm': True, 'text': '在 60×40 米的菜地里，2 台车访问 6 个点，单车容量 4'})['session_id']
    session = wait(runner, sid)
    assert session['state'] == 'done', session.get('error')
    assert session['report']['source'] == 'template'
    assert '鲁棒性评估（' in session['report']['narrative'] and '不是实车可靠性' in session['report']['narrative']


def test_the_evaluation_runs_once_however_often_the_guard_comes_by(lab, batches):
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}], simulate=False)
    before = len(session['tool_calls'])
    cancel = threading.Event()
    session = runner.store.get(sid)
    runner._guard_robustness(session, cancel, threading.Event())
    assert len(session['tool_calls']) == before
    assert len([m for m in session['messages'] if m.get('kind') == 'robustness']) == 1


def test_switched_off_there_is_no_step_and_no_claim(lab):
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}], simulate=False)
    assert session['state'] == 'done', session.get('error')
    assert session['report']['evidence']['robustness'] is None
    assert not any(s['kind'] == 'robustness' for s in session['plan'])
    assert '鲁棒性' not in session['report']['narrative']


def test_a_plan_that_failed_the_preview_is_not_evaluated_and_all_at_once_is_preferred(lab):
    runner, script, sid, first = compare_three(lab)
    ids = first['active_run_ids']
    assert len(ids) >= 2
    ok = {'state': 'succeeded', 'check_valid': True, 'errors': []}
    bad = {'state': 'failed', 'check_valid': False, 'errors': ['接触']}
    fake = runner.store.get(sid)
    fake['physics'] = {rid: {**ok, 'run_id': rid, 'coordinated': {**ok}} for rid in ids}
    fake['physics'][ids[0]] = {**bad, 'run_id': ids[0], 'coordinated': {**bad}}
    chosen, traffic = runner._robustness_choice(fake)
    assert ids[0] not in chosen and traffic == 'coordinated' and len(chosen) <= 2
    fake['physics'] = {rid: {**ok, 'run_id': rid} for rid in ids}
    assert runner._robustness_choice(fake)[1] == 'serial', 'plans that never ran together are compared one at a time'
    fake['physics'] = {rid: {**bad, 'run_id': rid} for rid in ids}
    assert runner._robustness_choice(fake) == ([], 'serial')


def test_the_order_of_the_plans_is_the_order_of_the_report(lab):
    runner, script, sid, first = compare_three(lab)
    fake = runner.store.get(sid)
    ranked = [r['run_id'] for r in runner._ranked(fake)]
    assert ranked[0] == runner._best_verified(fake)['run_id']
    fake['physics'] = {rid: {'run_id': rid, 'state': 'succeeded', 'check_valid': True} for rid in first['active_run_ids']}
    assert runner._robustness_choice(fake)[0] == [r for r in ranked if r in fake['physics']][:2]


def test_without_the_engine_the_report_says_nothing_about_robustness(lab, batches, monkeypatch):
    def refuse(**kwargs):
        raise PhysicsError(503, 'engine_unavailable', '环境中没有安装 mujoco')

    monkeypatch.setattr(lab.physics, 'submit_batch', refuse)
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}], simulate=False)
    assert session['state'] == 'done', session.get('error')
    rows = session['report']['evidence']['robustness']['rows']
    assert rows and all(r['state'] == 'unavailable' and r['errors'] == ['环境中没有安装 mujoco'] for r in rows)
    assert next(s for s in session['plan'] if s['kind'] == 'robustness')['status'] == 'failed'
    assert '鲁棒性评估（' not in session['report']['narrative']


def verified_context(lab, run):
    session = {'verifications': {run['run_id']: {'valid': True, 'agrees': True}}, 'contract': {'fields': {}}}
    return ToolContext(lab=lab, session=session)


def test_an_unverified_plan_is_refused(lab, monkeypatch):
    run = {'run_id': 'r1', 'routes': [{}, {}]}
    monkeypatch.setattr(tools, '_run', lambda ctx, rid: run)
    ctx = verified_context(lab, run)
    ctx.session['verifications'] = {}
    with pytest.raises(ToolError) as e:
        tools.physics_robustness(ctx, {'run_id': 'r1'})
    assert e.value.code == 'unverified'


def test_cancelling_the_session_cancels_the_batch(lab, monkeypatch):
    run = {'run_id': 'r1', 'routes': [{}, {}]}
    monkeypatch.setattr(tools, '_run', lambda ctx, rid: run)
    monkeypatch.setattr(lab.physics, 'submit_batch', lambda **kw: {'physics_run_id': 'b'})
    monkeypatch.setattr(lab.physics, 'wait', lambda rid, cancel, timeout: {'physics_run_id': rid, 'state': 'canceled'})
    with pytest.raises(ToolError) as e:
        tools.physics_robustness(verified_context(lab, run), {'run_id': 'r1'})
    assert e.value.code == 'cancelled'


def test_a_multi_vehicle_plan_defaults_to_all_at_once_and_the_seed_is_the_sessions(lab, monkeypatch):
    run = {'run_id': 'r1', 'routes': [{}, {}], 'algorithm': {'name': 'A'}}
    seen = {}
    monkeypatch.setattr(tools, '_run', lambda ctx, rid: run)
    monkeypatch.setattr(tools, 'ROBUSTNESS_RUNS', 16)
    monkeypatch.setattr(lab.physics, 'submit_batch', lambda **kw: seen.update(kw) or {'physics_run_id': 'b'})
    monkeypatch.setattr(lab.physics, 'wait', lambda rid, cancel, timeout: {'physics_run_id': rid, 'state': 'succeeded', 'traffic_mode': 'coordinated', 'batch': {'seed': 42}, 'summary': {'requested': 16}, 'samples': [{'seed': 1}]})
    ctx = verified_context(lab, run)
    ctx.session['contract'] = {'fields': {'seed': {'value': 42}}}
    row = tools.physics_robustness(ctx, {'run_id': 'r1'})
    assert seen['traffic'] == 'coordinated' and seen['runs'] == 16 and seen['seed'] == 42 and seen['intensity'] == 1.
    assert seen['wall_limit_s'] == tools.ROBUSTNESS_WALL_S
    assert 'samples' not in row and ctx.session['robustness']['r1']['samples'] == [{'seed': 1}]


SPREAD = {'n': 16, 'min': 100., 'p10': 101., 'median': 104.26, 'p90': 110.44, 'max': 120.}
GAP = {'n': 16, 'min': .712, 'p10': .72, 'median': .8, 'p90': .9, 'max': 1.}


def evidence(**first):
    a = {'run_id': 'a', 'algorithm': '甲', 'traffic': 'coordinated', 'requested': 16, 'finished': 16, 'cut_off': 0, 'passed': 16, 'intensity': 1., 'makespan_s': SPREAD, 'min_gap_m': GAP, 'failures': [], **first}
    b = {**a, 'run_id': 'b', 'algorithm': '乙', 'passed': 14, 'failures': [{'reason': 'vehicle_guard', 'label': '车距保护停车', 'count': 2}]}
    return {'rows': [a, b], 'difference': {'a': '甲', 'b': '乙', 'pairs': 16, 'both_passed': 14, 'only_a_passed': 2, 'only_b_passed': 0, 'a_faster': 10, 'b_faster': 4, 'median_delta_s': -3.2}}


def test_the_report_states_both_plans_their_difference_and_the_scope():
    text = _robustness_text(evidence())
    for part in ('全部车辆同时运行', '同一组 16 次随机扰动', '强度 1', '甲 通过 16/16 次', '乙 通过 14/16 次', '工期中位数 104.3 s、90 分位 110.4 s', '最小车距中位数 0.80 m、最小 0.71 m',
                 '车距保护停车 2 次', '甲 更快 10 次', '乙 更快 4 次', '中位数 -3.2 s', '只有 甲 通过 2 次', '不是实车可靠性'):
        assert part in text, part


def test_a_batch_cut_off_by_its_time_limit_says_so():
    assert '到时限，完成 9/16 次' in _robustness_text(evidence(finished=9, cut_off=7, passed=9))


def test_nothing_measured_means_nothing_said():
    assert _robustness_text({'rows': [{'run_id': 'a', 'state': 'unavailable', 'errors': ['x']}], 'difference': None}) == ''


def test_the_model_is_told_to_explain_the_difference_within_its_scope():
    from ceres_lab.agent.session import new_session
    text = prompts.SYSTEM
    assert 'robustness' in text and '不是实车可靠性' in text and '车距保护停车' in text
    assert 'robustness' in prompts.state(new_session('llm', None), 'report', evidence={'robustness': evidence()})
    assert prompts.VERSION == 'agent-prompts/8'


def test_the_template_report_carries_the_paragraph():
    e = {'contract': {}, 'runs': [], 'best_verified': None, 'best_executable': None, 'precheck': [], 'unsupported': [], 'physics': [], 'robustness': evidence()}
    assert '鲁棒性评估（' in _template_report(e)
