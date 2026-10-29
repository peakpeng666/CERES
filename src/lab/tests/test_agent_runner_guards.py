"""Deterministic provider responses exercise execution gates; no model or ROS is used."""
import json
import time

import pytest

from urp_lab import paths
from urp_lab.agent import llm
from urp_lab.agent.runner import AgentRunner, _template_report
from urp_lab.gateway import Config, Lab


PROVIDERS = [llm.Provider(id='scripted', kind='openai', model='scripted', base_url='http://127.0.0.1:9')]
FIELDS = {'task_count': 8, 'vehicle_count': 2, 'field_size_m': [60, 40], 'vehicle_capacity': 4, 'seed': 7}


class Scripted:
    def __init__(self, responses):
        self.responses = list(responses)
        self.offered = []

    def __call__(self, provider, messages, specs, **kwargs):
        self.offered.append([spec['name'] for spec in specs or []])
        item = self.responses.pop(0) if self.responses else {'content': '已按工具记录结束当前阶段。'}
        if callable(item):
            item = item(messages)
        return llm.Response(content=item.get('content', ''),
                            tool_calls=[{'id': f'c{i}', 'name': name, 'arguments': args, 'parse_error': None}
                                        for i, (name, args) in enumerate(item.get('calls', []))],
                            usage={}, latency_s=0, attempts=1, finish_reason='stop',
                            model='scripted', provider='scripted', cost=0, currency='CNY')


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    lab = Lab(Config(runtime=tmp_path, background=False))
    lab.start()
    # Any accidental invocation before approval fails the test through its call log.
    starts = []

    def start(body):
        starts.append(body)
        raise RuntimeError('test simulator is intentionally unavailable')

    monkeypatch.setattr(lab.fieldsim, 'start', start)
    lab.test_sim_starts = starts
    yield lab
    lab.shutdown()


def wait(runner, sid):
    end = time.monotonic() + 30
    while time.monotonic() < end:
        session = runner.store.get(sid)
        if session['state'] != 'running':
            return session
        time.sleep(0.03)
    raise AssertionError(f"session stuck in {session['state']}")


def create(lab, responses, simulate=True):
    script = Scripted([
        {'calls': [('update_task_contract', {'fields': FIELDS | {'simulate': simulate}})]},
        {'content': '需求已写入合同。'},
        {'calls': [('build_scenario', {})]},
        {'calls': [('solve', {'algorithms': ['panel.savings_fleet']})]},
        *responses,
    ])
    runner = AgentRunner(lab, chat=script, providers=PROVIDERS)
    lab.agent = runner
    sid = runner.create({'mode': 'llm', 'auto_confirm': True, 'text': '按测试合同进行规划'})['session_id']
    return runner, script, sid, wait(runner, sid)


def sim_call(messages):
    result = next(json.loads(m['content'])['result'] for m in reversed(messages)
                  if m['role'] == 'tool' and '"runs"' in m['content'])
    return {'calls': [('start_simulation', {'run_id': result['runs'][0]['run_id']})]}


@pytest.mark.parametrize('ending', [
    {'content': '已经完成规划。'},
    {'calls': [('ask_user', {'questions': [{'question': '需要现在启动仿真吗？'}]})]},
])
def test_simulation_requirement_reaches_approval_despite_model_omission(lab, ending):
    runner, script, sid, session = create(lab, [ending, sim_call, {'content': '用户已拒绝仿真。'}])
    assert session['state'] == 'awaiting_approval', session.get('error')
    pending = session['pending']
    assert pending['call']['name'] == 'start_simulation'
    rid = pending['call']['arguments']['run_id']
    assert session['verifications'][rid]['valid'] and session['verifications'][rid]['agrees']
    assert not lab.test_sim_starts and not session.get('sim_runs')
    assert not any(c['name'] == 'start_simulation' for c in session['tool_calls'])
    assert all('ask_user' not in offered for offered in script.offered[2:])
    if ending.get('calls'):
        refused = next(c for c in session['tool_calls'] if c['name'] == 'ask_user')
        assert refused['status'] == 'refused' and refused['error']['code'] == 'wrong_phase'
    runner.message(sid, '不要')
    session = wait(runner, sid)
    assert session['state'] == 'done', session.get('error')
    assert session['pending'] is None and not lab.test_sim_starts
    assert session['simulation_decision']['status'] == 'rejected'
    assert session['report']['evidence']['simulation_started'] is False
    assert next(s for s in session['plan'] if s['kind'] == 'simulate')['status'] == 'skipped'
    repeat = next(c for c in session['tool_calls'] if c['name'] == 'start_simulation')
    assert repeat['error']['code'] == 'simulation_already_decided'
    runner.control(sid, 'retry')
    session = wait(runner, sid)
    assert session['state'] == 'awaiting_approval'
    assert session['simulation_decision_history'][-1]['status'] == 'rejected'
    assert not lab.test_sim_starts


def test_model_cannot_request_simulation_outside_confirmed_contract(lab):
    runner, script, sid, session = create(lab, [sim_call], simulate=False)
    assert session['state'] == 'done', session.get('error')
    rec = next(c for c in session['tool_calls'] if c['name'] == 'start_simulation')
    assert rec['status'] == 'refused' and rec['error']['code'] == 'simulation_not_requested'
    assert not session['pending'] and not lab.test_sim_starts


def test_rebuilding_same_contract_cannot_bypass_simulation_rejection(lab):
    runner, script, sid, session = create(lab, [
        {'content': '规划阶段结束。'},
        {'calls': [('build_scenario', {}), ('solve', {'algorithms': ['panel.savings_fleet']})]},
        sim_call,
    ])
    assert session['state'] == 'awaiting_approval'
    old_rid = session['pending']['call']['arguments']['run_id']
    runner.control(sid, 'reject')
    session = wait(runner, sid)
    assert session['state'] == 'done', session.get('error')
    assert old_rid in session['run_ids'] and len(session['active_run_ids']) >= 2
    assert session['simulation_decision']['status'] == 'rejected'
    repeat = next(c for c in session['tool_calls'] if c['name'] == 'start_simulation')
    assert repeat['error']['code'] == 'simulation_already_decided'
    assert not session['pending'] and not lab.test_sim_starts


def test_invented_run_is_refused_before_approval_and_current_run_is_selected(lab):
    runner, script, sid, session = create(lab, [
        {'calls': [('start_simulation', {'run_id': 'invented-run'})]},
        {'content': '规划阶段结束。'},
    ])
    assert session['state'] == 'awaiting_approval', session.get('error')
    rec = next(c for c in session['tool_calls'] if c['name'] == 'start_simulation')
    assert rec['status'] == 'refused' and rec['error']['code'] == 'invalid_run'
    assert session['pending']['call']['arguments']['run_id'] in session['active_run_ids']
    assert not lab.test_sim_starts


def test_failed_approved_start_is_recorded_and_not_repeated(lab):
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}, sim_call])
    assert session['state'] == 'awaiting_approval'
    assert not lab.test_sim_starts
    runner.control(sid, 'approve')
    session = wait(runner, sid)
    assert session['state'] == 'done', session.get('error')
    assert len(lab.test_sim_starts) == 1
    assert session['simulation_decision']['status'] == 'attempted'
    assert session['simulation_decision']['outcome'] == 'error'
    assert session['report']['evidence']['simulation_started'] is False
    assert session['report']['evidence']['simulation_decision']['error']['code'] == 'sim_failed'
    assert not session['pending']
    runner.control(sid, 'retry')
    session = wait(runner, sid)
    assert session['state'] == 'awaiting_approval'
    assert session['simulation_decision_history'][-1]['outcome'] == 'error'
    assert len(lab.test_sim_starts) == 1


def test_retry_does_not_restart_successfully_started_simulation(lab, monkeypatch):
    def start(body):
        lab.test_sim_starts.append(body)
        return {'phase': 'starting', 'session_id': 'test-simulation'}

    monkeypatch.setattr(lab.fieldsim, 'start', start)
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}])
    assert session['state'] == 'awaiting_approval'
    runner.control(sid, 'approve')
    session = wait(runner, sid)
    assert session['state'] == 'done' and len(lab.test_sim_starts) == 1
    runner.control(sid, 'retry')
    session = wait(runner, sid)
    assert session['state'] == 'done' and len(lab.test_sim_starts) == 1
    assert session['simulation_decision']['outcome'] == 'ok'
    assert not session['pending'] and not session.get('simulation_decision_history')


def test_report_preserves_per_run_budget_seed_and_independent_rejection(lab):
    runner, script, sid, session = create(lab, [
        {'calls': [('solve', {'algorithms': ['pyvrp.fleet'], 'time_limit_s': 0.2, 'seed': 11})]},
        {'calls': [('solve', {'algorithms': ['pyvrp.fleet'], 'time_limit_s': 0.3, 'seed': 13})]},
    ], simulate=False)
    assert session['state'] == 'done', session.get('error')
    evidence = session['report']['evidence']
    rows = [r for r in evidence['runs'] if r['time_limit_s'] is not None]
    # The initial deterministic savings run still records its 2 s budget and
    # explicitly has no random seed; preserve that row alongside both retries.
    assert {(r['time_limit_s'], r['seed']) for r in rows} == {(2.0, None), (0.2, 11), (0.3, 13)}
    assert all(r['params'].get('seed') == r['seed'] for r in rows)
    assert '同一输入、预算与随机种子' not in _template_report(evidence)
    rid = session['active_run_ids'][0]
    session['verifications'][rid] = {'valid': False, 'agrees': False, 'category': 'implementation_error'}
    evidence = runner._evidence(session)
    row = next(r for r in evidence['runs'] if r['run_id'] == rid)
    assert row['independent_recheck'] is False and row['diagnosis_label'] == '软件实现错误'
    assert not evidence['best_verified'] or evidence['best_verified']['run_id'] != rid


def test_nonfinite_model_tool_arguments_are_refused_without_corrupting_session(lab):
    runner, script, sid, session = create(lab, [
        {'calls': [('solve', {'algorithms': ['panel.savings_fleet'], 'time_limit_s': float('nan')})]},
    ], simulate=False)
    assert session['state'] == 'done', session.get('error')
    rec = next(c for c in session['tool_calls'] if c['name'] == 'solve' and c['status'] == 'refused')
    assert rec['error']['code'] == 'invalid_arguments'
    json.dumps(runner.store.get(sid), allow_nan=False)


def test_verification_step_needs_agreement_with_the_archived_result():
    from urp_lab.agent.runner import _detail
    record = {'name': 'verify_run', 'result': {'recheck': {'valid': True, 'agrees': False}}}
    assert _detail(record) == '复核未通过'
    record['result']['recheck']['agrees'] = True
    assert _detail(record) == '复核通过'
