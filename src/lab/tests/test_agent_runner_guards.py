"""Deterministic provider responses exercise execution gates; no model or ROS is used."""
import json
import time

import pytest

from urp_lab import paths
from urp_lab.agent import llm
from urp_lab.agent.runner import AgentRunner, RunnerError, _template_report
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


class FakeField:
    """A session that reports its phases in turn, then leaves a result on disk, as the real one does."""

    def __init__(self, phases, result):
        self.phases, self._result, self.stopped = list(phases), result, False

    def start(self, body):
        return {'phase': 'starting', 'session_id': 'field_test'}

    def state(self):
        phase = self.phases.pop(0) if len(self.phases) > 1 else self.phases[0]
        return {'phase': phase, 'session_id': 'field_test', 'sim_time': 3.0, 'vehicles': [{'tasks_done': 1, 'tasks_total': 2}], 'errors': [] if phase != 'failed' else ['世界加载失败']}

    def result(self, session_id):
        if self._result is None:
            raise RuntimeError('没有结果')
        return self._result

    def stop(self, *, session_id=None):
        self.stopped = True
        return {'phase': 'stopped'}

    def shutdown(self):
        pass


RESULT = {'state': 'succeeded', 'check': {'valid': True}, 'metrics': {'tasks_total': 2, 'tasks_completed': 2, 'total_distance_m': 60.5, 'planned_total_distance_m': 58.0,
                                                                     'makespan_s': 70.0, 'planned_makespan_s': 60.0, 'cross_track_mean_m': .01, 'cross_track_max_m': .12, 'energy_wh': .3}}


def test_the_report_waits_for_the_simulation_and_states_what_happened(lab, monkeypatch):
    lab.fieldsim = FakeField(['running', 'running', 'finished'], RESULT)
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}])
    assert session['state'] == 'awaiting_approval'
    runner.control(sid, 'approve')
    session = wait(runner, sid)
    assert session['state'] == 'done', session.get('error')
    step = next(s for s in session['plan'] if s['kind'] == 'simulate')
    assert step['status'] == 'done' and '2/2' in step['detail']
    assert any(m.get('kind') == 'simulation' for m in session['messages'])
    assert session['report']['evidence']['simulation_result']['metrics']['total_distance_m'] == 60.5
    assert lab.fieldsim.stopped, 'the finished session is closed so the next approval can start'


def test_a_simulation_that_fails_is_reported_as_failed(lab):
    lab.fieldsim = FakeField(['running', 'failed'], None)
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}])
    runner.control(sid, 'approve')
    session = wait(runner, sid)
    assert session['state'] == 'done', session.get('error')
    step = next(s for s in session['plan'] if s['kind'] == 'simulate')
    assert step['status'] == 'failed' and '世界加载失败' in step['detail']
    assert '没有完成' in session['report']['narrative'] or session['report']['source'] == 'llm'


def test_cancelling_during_a_simulation_stops_it(lab):
    lab.fieldsim = FakeField(['running'], None)
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}])
    runner.control(sid, 'approve')
    end = time.monotonic() + 10
    while time.monotonic() < end and next(s for s in runner.store.get(sid)['plan'] if s['kind'] == 'simulate')['status'] != 'running':
        time.sleep(.02)
    runner.control(sid, 'cancel')
    session = wait(runner, sid)
    assert session['state'] == 'cancelled' and lab.fieldsim.stopped


def test_verified_plans_meet_the_physics_engine_before_the_report(lab):
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}], simulate=False)
    assert session['state'] == 'done', session.get('error')
    rows = session['report']['evidence']['physics']
    assert rows and all(r['state'] == 'succeeded' and r['check_valid'] for r in rows)
    assert all(r['metrics']['total_distance_m'] > 0 for r in rows)
    assert next(s for s in session['plan'] if s['kind'] == 'physics')['status'] == 'done'
    calls = [c for c in session['tool_calls'] if c['name'] == 'physics_check']
    assert len(calls) == len(rows) and all(c['actor'] == 'system' for c in calls)
    assert any(m.get('kind') == 'physics' for m in session['messages'])


def test_the_user_can_send_a_plan_to_simulation_after_the_report(lab):
    lab.fieldsim = FakeField(['running', 'finished'], RESULT)
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}], simulate=False)
    assert session['state'] == 'done', session.get('error')
    runner.control(sid, 'simulate', {})
    session = wait(runner, sid)
    assert session['state'] == 'done', session.get('error')
    call = next(c for c in session['tool_calls'] if c['name'] == 'start_simulation')
    assert call['actor'] == 'user_approved'
    assert session['report']['evidence']['simulation_result']['state'] == 'succeeded'


def test_simulation_cannot_jump_ahead_of_a_pending_decision(lab):
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}])
    assert session['state'] == 'awaiting_approval'
    with pytest.raises(RunnerError) as e:
        runner.control(sid, 'simulate', {})
    assert e.value.code == 'invalid_state'


def test_describe_algorithm_reports_the_record_on_this_machine(lab):
    runner, script, sid, session = create(lab, [{'calls': [('describe_algorithm', {'algorithm_id': 'pyvrp.fleet'})]}], simulate=False)
    call = next(c for c in session['tool_calls'] if c['name'] == 'describe_algorithm')
    assert call['status'] == 'ok' and call['result']['name'] and 'record' in call['result']


def test_capabilities_state_their_status_and_the_job_list_is_empty_when_idle(lab):
    from urp_lab.agent.capabilities import capabilities, jobs
    found = capabilities(lab)
    assert {s['id'] for s in found['simulators']} == {'gazebo.field', 'gazebo.tb4', 'mujoco.field'}
    assert next(s for s in found['simulators'] if s['id'] == 'mujoco.field')['status'] == 'ready'
    assert all(x['status'] in ('ready', 'unavailable') for group in found.values() for x in group)
    assert jobs(lab) == {'jobs': []}


def test_a_paused_watch_leaves_the_simulation_running_and_resumes_it(lab):
    lab.fieldsim = FakeField(['running'], RESULT)
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}])
    runner.control(sid, 'approve')
    end = time.monotonic() + 10
    while time.monotonic() < end and next(s for s in runner.store.get(sid)['plan'] if s['kind'] == 'simulate')['status'] != 'running':
        time.sleep(.02)
    runner.control(sid, 'pause')
    session = wait(runner, sid)
    assert session['state'] == 'paused' and not lab.fieldsim.stopped
    lab.fieldsim.phases = ['finished']
    runner.control(sid, 'resume')
    session = wait(runner, sid)
    assert session['state'] == 'done', session.get('error')
    assert session['report']['evidence']['simulation_result']['state'] == 'succeeded'


def test_plans_the_model_already_checked_are_still_shown_once(lab):
    def check(messages):
        result = next(json.loads(m['content'])['result'] for m in reversed(messages) if m['role'] == 'tool' and '"runs"' in m['content'])
        return {'calls': [('verify_run', {'run_id': result['runs'][0]['run_id']}), ('physics_check', {'run_id': result['runs'][0]['run_id']})]}

    runner, script, sid, session = create(lab, [check, {'content': '规划阶段结束。'}], simulate=False)
    assert session['state'] == 'done', session.get('error')
    assert len([c for c in session['tool_calls'] if c['name'] == 'physics_check']) == 1
    assert len([m for m in session['messages'] if m.get('kind') == 'physics']) == 1


def physics_verdicts(monkeypatch, lab, fail_first=True):
    """The first plan the engine sees fails with the protection Gazebo would trigger; the rest pass."""
    from urp_lab.agent import tools
    seen = []
    original = tools.registry

    def check(ctx, args):
        seen.append(args['run_id'])
        bad = fail_first and len(seen) == 1
        row = {'run_id': args['run_id'], 'algorithm': '方案', 'engine': 'mujoco', 'engine_version': '3.17.0', 'state': 'failed' if bad else 'succeeded', 'check_valid': not bad,
               'errors': ['车间距离保护：vehicle_2/vehicle_3 间距 1.117 m；全部停车'] if bad else [], 'wall_s': 1., 'metrics': {'total_distance_m': 10., 'makespan_s': 9.}}
        ctx.session.setdefault('physics', {})[args['run_id']] = row
        return row

    def registry():
        found = original()
        found['physics_check'].handler = check
        return found

    monkeypatch.setattr(tools, 'registry', registry)
    return seen


def compare_three(lab):
    return create(lab, [{'calls': [('solve', {'algorithms': ['pyvrp.fleet', 'ortools.fleet', 'panel.sweep']})]}, {'content': '规划阶段结束。'}])


def test_a_plan_that_fails_the_physics_engine_is_not_sent_to_gazebo(lab, monkeypatch):
    physics_verdicts(monkeypatch, lab)
    runner, script, sid, session = compare_three(lab)
    assert session['state'] == 'awaiting_approval', session.get('error')
    chosen = session['pending']['call']['arguments']['run_id']
    assert session['physics'][chosen]['state'] == 'succeeded'
    failed = next(r for r, row in session['physics'].items() if row['state'] == 'failed')
    assert chosen != failed


def test_the_approval_names_why_the_shortest_plan_was_passed_over(lab, monkeypatch):
    # This is a decision-contract test. Real serial/coordinated execution has separate physics integration coverage.
    physics_verdicts(monkeypatch, lab, fail_first=False)
    runner0, _, sid0, first = compare_three(lab)
    best = runner0._best_verified(first)['run_id']
    fake = dict(first)
    fake['physics'] = {rid: {'run_id': rid, 'state': 'succeeded', 'check_valid': True, 'errors': []} for rid in first['active_run_ids']}
    fake['physics'][best] = {'run_id': best, 'state': 'failed', 'check_valid': False, 'errors': ['车间距离保护']}
    other = runner0._best_executable(fake)
    assert other and other['run_id'] != best
    runner0._queue_simulation(fake, {'run_id': other['run_id']})
    assert '未通过物理预演' in fake['pending']['reason'] and '车间距离保护' in fake['pending']['reason']
    assert runner0._simulation_refusal({**fake, 'simulation_decision': None}, {'run_id': best})[0] == 'physics_failed'


def test_when_no_plan_passes_the_person_still_decides_and_is_told(lab, monkeypatch):
    # This is a decision-contract test. Real serial/coordinated execution has separate physics integration coverage.
    physics_verdicts(monkeypatch, lab, fail_first=False)
    runner, _, sid, first = compare_three(lab)
    fake = dict(first)
    fake['physics'] = {rid: {'run_id': rid, 'state': 'failed', 'check_valid': False, 'errors': ['接触']} for rid in first['active_run_ids']}
    best = runner._best_verified(fake)
    assert runner._best_executable(fake) is None
    runner._queue_simulation(fake, {'run_id': best['run_id']})
    assert '没有通过预演的方案可选' in fake['pending']['reason']


def test_an_unavailable_engine_says_nothing_about_a_plan(lab, monkeypatch):
    # This is a decision-contract test. Real serial/coordinated execution has separate physics integration coverage.
    physics_verdicts(monkeypatch, lab, fail_first=False)
    runner, _, sid, first = compare_three(lab)
    fake = dict(first)
    fake['physics'] = {rid: {'run_id': rid, 'state': 'unavailable', 'errors': ['环境中没有安装 mujoco'], 'metrics': {}} for rid in first['active_run_ids']}
    assert not any(runner._physics_failed(fake, rid) for rid in first['active_run_ids'])


def test_a_failed_session_is_read_even_when_its_result_lands_late(lab):
    class Late(FakeField):
        def __init__(self):
            super().__init__(['running', 'failed'], {'state': 'failed', 'check': {'valid': False}, 'metrics': {'tasks_total': 9, 'tasks_completed': 4, 'total_distance_m': 120.}})
            self.reads = 0

        def result(self, session_id):
            self.reads += 1
            if self.reads < 3:
                raise RuntimeError('结果还没写完')
            return self._result

    lab.fieldsim = Late()
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}])
    runner.control(sid, 'approve')
    session = wait(runner, sid)
    sim = session['report']['evidence']['simulation_result']
    assert sim['state'] == 'failed' and sim['metrics']['tasks_completed'] == 4


def test_without_gazebo_no_approval_is_requested_and_the_report_says_why(lab, monkeypatch):
    monkeypatch.setattr('urp_lab.agent.tools.gazebo_gap', lambda: '这台电脑没有 ROS 2 和 Gazebo')
    runner, script, sid, session = create(lab, [{'content': '规划阶段结束。'}])
    assert session['state'] == 'done', session.get('error')
    assert not session['pending'] and not lab.test_sim_starts
    assert session['report']['evidence']['simulator_gap'] == '这台电脑没有 ROS 2 和 Gazebo'
    assert session['report']['evidence']['physics'], 'the physics preview does not depend on Gazebo'


class Strict(Scripted):
    """Rejects a history that leaves a tool call without a result, as OpenAI and Anthropic endpoints do."""

    def __call__(self, provider, messages, specs, **kwargs):
        for i, message in enumerate(messages):
            if message['role'] != 'assistant' or not message.get('tool_calls'):
                continue
            answered = []
            for following in messages[i + 1:]:
                if following['role'] != 'tool':
                    break
                answered.append(following['tool_call_id'])
            missing = [c['id'] for c in message['tool_calls'] if c['id'] not in answered]
            if missing:
                raise llm.LLMError(f'HTTP 400：tool calls without results {missing}', 'provider_error', 400)
        return super().__call__(provider, messages, specs, **kwargs)


@pytest.mark.parametrize('action', ['pause', 'cancel'])
def test_stopping_between_tool_calls_answers_the_rest_so_the_session_can_continue(lab, monkeypatch, action):
    from urp_lab.agent import runner as runner_module
    script = Strict([
        {'calls': [('update_task_contract', {'fields': FIELDS | {'simulate': False}})]},
        {'content': '需求已写入合同。'},
        {'calls': [('build_scenario', {}), ('recommend_algorithms', {})]},
        {'calls': [('solve', {'algorithms': ['panel.savings_fleet']})]},
        {'content': '规划阶段结束。'},
    ])
    runner = AgentRunner(lab, chat=script, providers=PROVIDERS)
    lab.agent = runner
    execute = runner_module.tools.execute

    def stop_after_build(registry, ctx, name, args):
        record = execute(registry, ctx, name, args)
        if name == 'build_scenario':
            runner.control(ctx.session['session_id'], action)   # the user presses the button while this call runs
        return record

    monkeypatch.setattr(runner_module.tools, 'execute', stop_after_build)
    sid = runner.create({'mode': 'llm', 'auto_confirm': True, 'text': '按测试合同进行规划'})['session_id']
    session = wait(runner, sid)
    assert session['state'] == ('paused' if action == 'pause' else 'cancelled'), session.get('error')
    history = session['llm_messages']
    turn = max(i for i, m in enumerate(history) if m['role'] == 'assistant' and m.get('tool_calls'))
    results = {m['tool_call_id']: json.loads(m['content']) for m in history[turn + 1:] if m['role'] == 'tool'}
    assert set(results) == {c['id'] for c in history[turn]['tool_calls']}
    assert results['c1']['status'] == ('interrupted' if action == 'pause' else 'cancelled')
    assert [c['name'] for c in session['tool_calls'] if c['name'] == 'recommend_algorithms'] == []    # never ran

    monkeypatch.setattr(runner_module.tools, 'execute', execute)
    runner.control(sid, 'resume' if action == 'pause' else 'retry')
    session = wait(runner, sid)
    assert session['state'] == 'done', session.get('error')
    assert not any('tool calls without results' in str(c.get('error')) for c in session['llm_calls'])


@pytest.fixture
def traffic_agent(monkeypatch):
    """Exercise production tool/control/watch methods without starting either engine."""
    import threading
    from types import SimpleNamespace
    from urp_lab.agent import tools
    from urp_lab.agent.session import new_session

    session = new_session('rule', None)
    session.update(state='done', scenario_id='scene', run_ids=['run'], active_run_ids=['run'],
                   verifications={'run': {'valid': True, 'agrees': True}})
    starts = []

    def start(body):
        starts.append(body)
        return {'phase': 'starting', 'session_id': 'field_test', 'traffic_mode': body.get('traffic', 'serial')}

    run = {'run_id': 'run', 'scenario_id': 'scene'}
    field = SimpleNamespace(start=start)
    lab = SimpleNamespace(fieldsim=field, store=SimpleNamespace(get_run=lambda rid: run))
    monkeypatch.setattr(tools, '_run', lambda ctx, rid: run)
    monkeypatch.setattr(tools, '_scenario', lambda ctx: {})
    monkeypatch.setattr(tools, 'run_matches_current', lambda s, r: True)
    runner = SimpleNamespace(lab=lab, tools=tools.registry(), _mutex=threading.RLock(), _cancel={},
                             _load=lambda sid: session, _require_writable=lambda s: None,
                             _best_verified=lambda s: {'run_id': 'run'}, _event=lambda events, sid: threading.Event(),
                             _publish=lambda *args: None, _record=lambda s, rec, actor: rec,
                             _save=lambda s: None, _view=lambda s: s, _start_locked=lambda s: s,
                             _sim2sim=lambda *args: None,
                             _release_simulation=AgentRunner._release_simulation)
    runner._simulate_locked = lambda s, payload: AgentRunner._simulate_locked(runner, s, payload)
    return runner, session, starts


@pytest.mark.parametrize('traffic', ['serial', 'coordinated'])
def test_gazebo_traffic_tool_passes_the_requested_mode(traffic_agent, traffic):
    from urp_lab.agent import tools
    runner, session, starts = traffic_agent
    rec = tools.execute(runner.tools, tools.ToolContext(runner.lab, session), 'start_simulation',
                        {'run_id': 'run', 'traffic': traffic})
    assert rec['status'] == 'ok', rec
    assert starts[0]['traffic'] == traffic
    assert rec['result']['traffic_mode'] == traffic


@pytest.mark.parametrize('traffic', [None, True, [], '', 'parallel'])
def test_gazebo_traffic_tool_rejects_invalid_mode_before_start(traffic_agent, traffic):
    from urp_lab.agent import tools
    runner, session, starts = traffic_agent
    rec = tools.execute(runner.tools, tools.ToolContext(runner.lab, session), 'start_simulation',
                        {'run_id': 'run', 'traffic': traffic})
    assert rec['status'] == 'refused' and rec['error']['code'] == 'invalid_arguments'
    assert starts == []


@pytest.mark.parametrize('traffic', ['serial', 'coordinated'])
def test_gazebo_traffic_user_control_preserves_mode_in_watch(traffic_agent, traffic):
    runner, session, starts = traffic_agent
    result = AgentRunner.control(runner, session['session_id'], 'simulate', {'run_id': 'run', 'traffic': traffic})
    assert starts[0]['traffic'] == traffic
    assert result['simulation_watch']['traffic_mode'] == traffic


@pytest.mark.parametrize('traffic', ['serial', 'coordinated'])
def test_gazebo_traffic_approval_warns_about_serial_lateness_only(traffic_agent, traffic):
    runner, session, _ = traffic_agent
    runner._physics_failed = lambda *args: False
    session['physics'] = {'run': {'time_windows': {'total': 1, 'on_time': 0,
                                                'late': [{'label': 'T1', 'vehicle': '车 1', 'arrival_s': 60, 'slack_s': -10}]}}}
    AgentRunner._queue_simulation(runner, session, {'run_id': 'run', 'traffic': traffic})
    reason = session['pending']['reason']
    assert ('晚于截止' in reason) is (traffic == 'serial')
    assert session['pending']['call']['arguments']['traffic'] == traffic


@pytest.mark.parametrize('traffic', ['serial', 'coordinated'])
def test_gazebo_traffic_watch_retains_result_windows_and_failures(traffic_agent, traffic):
    import threading
    from urp_lab import time_windows
    from urp_lab.agent.session import ensure_step
    runner, session, _ = traffic_agent
    plan = {'instance': {'tasks': [{'id': 1, 'label': 'T1', 'time_window': [20, 50]}]},
            'vehicles': [{'name': 'vehicle_1', 'label': '车 1'}],
            **({'traffic': {'gates': {}}} if traffic == 'coordinated' else {})}
    result = {'state': 'failed', 'traffic_mode': traffic, 'traffic': plan.get('traffic', {}),
              'check': {'valid': False}, 'metrics': {}, 'errors': ['任务 T1 晚于截止时间'],
              'execution_plan': plan, 'execution_started_sim_s': 10,
              'tasks': [{'id': 1, 'vehicle': 'vehicle_1', 't_sim': 70}]}
    result['time_windows'] = time_windows.execution(plan, result)
    runner.lab.fieldsim = FakeField(['failed'], result)
    session['simulation_watch'] = {'run_id': 'run', 'session_id': 'field_test', 'traffic_mode': traffic}
    ensure_step(session, 'simulate')
    AgentRunner._watch_simulation(runner, session, threading.Event(), threading.Event())
    actual = session['simulation_result']
    assert actual['traffic_mode'] == traffic and actual['state'] == 'failed' and actual['check_valid'] is False
    assert actual['time_windows']['all_on_time'] is False and actual['time_windows']['model'] == traffic
    assert actual['time_windows']['late'][0]['task'] == 1
    assert '任务 T1 晚于截止时间' in actual['errors']
    assert session['simulation_watch']['done'] is True


def test_gazebo_traffic_defaults_remain_serial_for_tools_and_user_control(traffic_agent):
    from urp_lab.agent import tools
    runner, session, starts = traffic_agent
    rec = tools.execute(runner.tools, tools.ToolContext(runner.lab, session), 'start_simulation', {'run_id': 'run'})
    assert rec['status'] == 'ok' and starts[-1]['traffic'] == 'serial'
    assert rec['result']['traffic_mode'] == 'serial'
    AgentRunner.control(runner, session['session_id'], 'simulate', {'run_id': 'run'})
    assert starts[-1]['traffic'] == 'serial' and session['simulation_watch']['traffic_mode'] == 'serial'


def test_gazebo_traffic_coordinated_preview_does_not_replace_serial_admission():
    from urp_lab.agent.runner import _executed
    row = {'state': 'succeeded', 'check_valid': True,
           'time_windows': {'all_on_time': False, 'early_starts': 0},
           'coordinated': {'state': 'succeeded', 'check_valid': True,
                           'time_windows': {'all_on_time': True, 'early_starts': 0}}}
    assert not _executed(row, 'serial') and _executed(row, 'coordinated')
    session = {'physics': {'run': row}}
    assert AgentRunner._physics_failed(session, 'run', 'serial')
    assert not AgentRunner._physics_failed(session, 'run', 'coordinated')


@pytest.mark.parametrize('traffic', ['serial', 'coordinated'])
def test_gazebo_traffic_early_work_fails_the_selected_preview(traffic):
    from urp_lab.agent.runner import _executed
    early = {'state': 'succeeded', 'check_valid': True, 'time_windows': {'all_on_time': True, 'early_starts': 1}}
    row = {**early, 'coordinated': early}
    assert not _executed(row, traffic)
    assert AgentRunner._physics_failed({'physics': {'run': row}}, 'run', traffic)


def test_gazebo_traffic_missing_coordinated_preview_is_not_serial_success():
    from urp_lab.agent.runner import _executed
    row = {'state': 'succeeded', 'check_valid': True}
    assert _executed(row, 'serial') and not _executed(row, 'coordinated')
    assert not AgentRunner._physics_failed({'physics': {'run': row}}, 'run', 'coordinated'), 'an absent run is not a measured failure'


@pytest.mark.parametrize('replay, reason', [
    ({'state': 'failed', 'errors': ['MuJoCo worker exited 7']}, 'MuJoCo worker exited 7'),
    ({'state': 'failed', 'errors': []}, 'failed'),
    ({'state': 'succeeded', 'comparison': {'available': False, 'errors': ['车辆记录缺失']}}, '车辆记录缺失'),
])
def test_gazebo_traffic_sim2sim_unavailable_keeps_reason_in_result_and_notice(traffic_agent, replay, reason):
    import threading
    from types import SimpleNamespace
    runner, session, _ = traffic_agent
    runner.lab.physics = SimpleNamespace(submit=lambda **kwargs: {'physics_run_id': 'phys_test'},
                                        wait=lambda *args: {'physics_run_id': 'phys_test', **replay})
    result = AgentRunner._sim2sim(runner, session, 'field_test', threading.Event())
    assert result['available'] is False and result['physics_run_id'] == 'phys_test'
    assert any(reason in error for error in result['errors'])
    assert any(message['kind'] == 'notice' and reason in message['text'] for message in session['messages'])


def test_gazebo_traffic_template_report_explains_unavailable_comparison():
    evidence = {'contract': {}, 'runs': [], 'precheck': [], 'unsupported': [],
                'simulation_result': {'state': 'succeeded', 'metrics': {},
                                      'comparison': {'available': False, 'errors': ['冻结计划身份不一致']}}}
    report = _template_report(evidence)
    assert '引擎对比不可用' in report and '冻结计划身份不一致' in report
    assert '轨迹差：平均' not in report


@pytest.mark.parametrize('check', [None, {'valid': False}])
def test_gazebo_traffic_watch_requires_passed_actual_check(traffic_agent, check):
    import threading
    from urp_lab.agent.session import ensure_step
    runner, session, _ = traffic_agent
    result = {'state': 'succeeded', 'check': check, 'metrics': {}, 'errors': []}
    runner.lab.fieldsim = FakeField(['finished'], result)
    runner._sim2sim = lambda *args: pytest.fail('a result without a passed check must not enter sim2sim')
    session['simulation_watch'] = {'run_id': 'run', 'session_id': 'field_test', 'traffic_mode': 'serial'}
    ensure_step(session, 'simulate')
    AgentRunner._watch_simulation(runner, session, threading.Event(), threading.Event())
    assert session['simulation_result']['state'] == 'failed'
    assert any('独立检查' in error for error in session['simulation_result']['errors'])


@pytest.mark.parametrize('violation', ['late', 'missing_task', 'early_service'])
def test_gazebo_traffic_watch_rechecks_legacy_windows_before_claiming_success(traffic_agent, violation):
    import threading
    from urp_lab.agent.session import ensure_step
    runner, session, _ = traffic_agent
    plan = {'instance': {'tasks': [{'id': 1, 'label': 'T1', 'time_window': [20, 50]}]},
            'vehicles': [{'name': 'vehicle_1', 'label': '车 1'}], 'traffic': {'gates': {}}}
    task = {'id': 1, 'vehicle': 'vehicle_1', 't_sim': 30, 'service_started_at': 30}
    if violation == 'late':
        task['t_sim'] = task['service_started_at'] = 70
    elif violation == 'early_service':
        task['t_sim'] = task['service_started_at'] = 20
    result = {'state': 'succeeded', 'traffic_mode': 'coordinated', 'check': {'valid': True},
              'metrics': {}, 'errors': [], 'execution_plan': plan, 'execution_started_sim_s': 10,
              'tasks': [] if violation == 'missing_task' else [task]}
    runner.lab.fieldsim = FakeField(['finished'], result)
    runner._sim2sim = lambda *args: pytest.fail('a failed time window must not enter sim2sim')
    session['simulation_watch'] = {'run_id': 'run', 'session_id': 'field_test', 'traffic_mode': 'coordinated'}
    ensure_step(session, 'simulate')
    AgentRunner._watch_simulation(runner, session, threading.Event(), threading.Event())
    actual = session['simulation_result']
    assert actual['state'] == 'failed' and actual['check_valid'] is False
    assert actual['time_windows']['early_starts'] == (violation == 'early_service')
    assert actual['errors'] and actual['traffic_mode'] == 'coordinated'


class SessionOwnedField:
    """Track the session addressed by reads/stops; replacement is an unrelated live world."""
    def __init__(self, current, old_result=None):
        self.current = dict(current)
        self.old_result = old_result
        self.reads = []
        self.stop_requests = []
        self.stopped = []
        self.replace_on_stop = False

    def state(self):
        return dict(self.current)

    def result(self, session_id):
        self.reads.append(session_id)
        if self.old_result is None:
            raise RuntimeError('old session has no saved result')
        assert session_id == 'field_old'
        return dict(self.old_result)

    def stop(self, *, session_id=None):
        self.stop_requests.append(session_id)
        if self.replace_on_stop:
            self.current = {'session_id': 'field_new', 'phase': 'running'}
        if session_id is None or session_id == self.current.get('session_id'):
            self.stopped.append(self.current.get('session_id'))
        return self.state()


@pytest.mark.parametrize('saved', ['complete', 'sparse', 'missing'])
def test_session_ownership_resumed_old_watch_never_stops_or_reports_the_replacement(traffic_agent, saved):
    import threading
    from types import SimpleNamespace
    from urp_lab.agent.runner import Paused
    from urp_lab.agent.session import ensure_step

    runner, session, _ = traffic_agent
    old = None if saved == 'missing' else {
        'session_id': 'field_old', 'state': 'succeeded', 'check': {'valid': True}, 'errors': [],
        'metrics': {'tasks_total': 1, 'tasks_completed': 1, 'total_distance_m': 12.0} if saved == 'complete' else {},
        'traffic_mode': 'serial'}
    field = SessionOwnedField({'session_id': 'field_old', 'phase': 'running'}, old)
    runner.lab.fieldsim = field
    session['simulation_watch'] = {'run_id': 'run', 'session_id': 'field_old', 'traffic_mode': 'serial'}
    ensure_step(session, 'simulate')
    pause = threading.Event()
    pause.set()
    with pytest.raises(Paused):
        AgentRunner._watch_simulation(runner, session, threading.Event(), pause)
    assert field.stop_requests == [] and not session['simulation_watch'].get('done')

    field.current = {'session_id': 'field_new', 'phase': 'running', 'traffic_mode': 'coordinated',
                     'metrics': {'tasks_total': 999, 'tasks_completed': 999, 'total_distance_m': 9999.0},
                     'errors': ['FOREIGN_SESSION_ERROR'], 'vehicles': []}
    pause.clear()
    immediate = SimpleNamespace(is_set=lambda: False, wait=lambda seconds: False)
    AgentRunner._watch_simulation(runner, session, immediate, pause)
    report = session['simulation_result']
    assert field.stop_requests == [] and field.stopped == []
    assert field.reads and set(field.reads) == {'field_old'}
    assert report['session_id'] == 'field_old' and report['traffic_mode'] == 'serial'
    assert 'FOREIGN_SESSION_ERROR' not in json.dumps(report)
    assert report['metrics']['total_distance_m'] == (12.0 if saved == 'complete' else None)
    assert report['metrics']['tasks_completed'] == (1 if saved == 'complete' else None)
    if saved == 'missing':
        assert report['state'] == 'failed' and report['errors']
    else:
        assert report['state'] == 'succeeded' and report['check_valid'] is True
    assert session['simulation_watch']['done']


@pytest.mark.parametrize('current, should_stop', [
    ({'session_id': 'field_old', 'phase': 'running'}, True),
    ({'session_id': 'field_new', 'phase': 'running'}, False),
    ({'phase': 'running'}, False),
    ({'phase': 'idle'}, False),
])
def test_session_ownership_cancel_stops_only_the_current_owned_session(traffic_agent, current, should_stop):
    import threading
    from urp_lab.agent.runner import Cancelled
    from urp_lab.agent.session import ensure_step
    runner, session, _ = traffic_agent
    field = SessionOwnedField(current)
    runner.lab.fieldsim = field
    session['simulation_watch'] = {'run_id': 'run', 'session_id': 'field_old'}
    ensure_step(session, 'simulate')
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        AgentRunner._watch_simulation(runner, session, cancel, threading.Event())
    assert field.stop_requests == (['field_old'] if should_stop else [])
    assert field.stopped == (['field_old'] if should_stop else [])


@pytest.mark.parametrize('watch_id,current', [
    (None, {'session_id': 'field_new', 'phase': 'running'}),
    ('field_old', {}),
    ('field_old', {'phase': 'idle'}),
    ('field_old', {'session_id': 'field_old', 'phase': 'idle'}),
])
def test_session_ownership_release_without_live_identity_never_stops(traffic_agent, watch_id, current):
    runner, session, _ = traffic_agent
    session['simulation_watch'] = {'run_id': 'run', 'session_id': watch_id}
    field = SessionOwnedField(current)
    AgentRunner._release_simulation(session, field)
    assert field.stop_requests == [] and field.stopped == []


def test_session_ownership_release_sends_expected_identity_to_close_the_race(traffic_agent):
    _, session, _ = traffic_agent
    session['simulation_watch'] = {'run_id': 'run', 'session_id': 'field_old'}
    field = SessionOwnedField({'session_id': 'field_old', 'phase': 'finished'})
    field.replace_on_stop = True
    AgentRunner._release_simulation(session, field)
    assert field.stop_requests == ['field_old']
    assert field.current['session_id'] == 'field_new' and field.stopped == []


@pytest.mark.parametrize('requested, should_stop', [('field_old', False), ('field_new', True)])
def test_session_ownership_manager_checks_identity_inside_lifecycle_lock(requested, should_stop):
    import threading
    from types import SimpleNamespace
    from urp_lab.fieldsim import FieldSimManager
    calls = []

    def stop():
        calls.append('stopped')
        return {'phase': 'stopped', 'session_id': 'field_new'}

    class SwitchOnLock:
        def __enter__(self):
            manager.meta = {'session_id': 'field_new'}

        def __exit__(self, *exc):
            return False

    manager = SimpleNamespace(_lock=SwitchOnLock(), meta={'session_id': 'field_old'}, _stop=stop,
                              state=lambda: {'phase': 'running', 'session_id': 'field_new'})
    result = FieldSimManager.stop(manager, session_id=requested)
    assert calls == (['stopped'] if should_stop else [])
    assert result['session_id'] == 'field_new'
    assert result['phase'] == ('stopped' if should_stop else 'running')


@pytest.mark.parametrize('identity', [{'session_id': 'field_new', 'run_id': 'run'},
                                     {'session_id': 'field_old', 'run_id': 'foreign_run'}])
def test_session_ownership_mismatched_saved_result_cannot_enter_old_report(traffic_agent, identity):
    import threading
    from types import SimpleNamespace
    from urp_lab.agent.session import ensure_step
    runner, session, _ = traffic_agent
    foreign = {**identity, 'state': 'succeeded', 'check': {'valid': True},
               'metrics': {'total_distance_m': 5555.0}, 'errors': ['FOREIGN_RESULT_ERROR']}
    field = SessionOwnedField({'session_id': 'field_new', 'phase': 'running'}, foreign)
    runner.lab.fieldsim = field
    session['simulation_watch'] = {'run_id': 'run', 'session_id': 'field_old', 'traffic_mode': 'serial'}
    ensure_step(session, 'simulate')
    immediate = SimpleNamespace(is_set=lambda: False, wait=lambda seconds: False)
    AgentRunner._watch_simulation(runner, session, immediate, threading.Event())
    actual = session['simulation_result']
    assert actual['session_id'] == 'field_old' and actual['run_id'] == 'run'
    assert actual['state'] == 'failed' and actual['errors']
    assert actual['metrics']['total_distance_m'] is None
    assert 'FOREIGN_RESULT_ERROR' not in json.dumps(actual) and field.stopped == []
