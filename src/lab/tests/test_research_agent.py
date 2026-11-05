"""Research agent (CONTRACT §9): tool loop on real solvers and checkers, approvals, cancel, budgets, grounding, HTTP contract.

The model is a scripted stand-in so the tests are offline and deterministic; they exercise the agent
runtime and the workbench tools, not the quality of any real language model.
"""
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from urp_lab import gateway, paths
from urp_lab.agent import ModelError, OpenAICompatibleModel, Reply, TOOLS, grounding, model_from_env
from urp_lab.agent.manager import POLICY
from urp_lab.compose import EXAMPLES
from urp_lab.store import Store

FIELD_SESSION = 'field_20261029T101500_abc123'


@pytest.fixture(autouse=True)
def isolated_provider_selection(monkeypatch):
    # Scripted models and environment-client contracts must never consume a saved personal key.
    monkeypatch.setattr('urp_lab.agent.providers.selected', lambda: None)


class ScriptedModel:
    """Plays replies in order; an item is a Reply or a callable(messages) -> Reply."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def describe(self):
        return {'provider': 'scripted-test', 'endpoint': None, 'model': 'script'}

    def complete(self, messages, tools):
        self.calls.append({'messages': json.loads(json.dumps(messages)), 'tools': [t['function']['name'] for t in tools]})
        if not self.script:
            return Reply('脚本已结束。', [])
        step = self.script.pop(0)
        return step(messages) if callable(step) else step


def call(name, args, cid=''):
    return {'id': cid, 'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}


def last_result(messages):
    return next(json.loads(m['content']) for m in reversed(messages) if m['role'] == 'tool')


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    opened = []

    def make(model, runtime='rt'):
        app = gateway.create_app(gateway.Config(port=4199, runtime=tmp_path / runtime, background=False, agent_model=model))
        client = TestClient(app, base_url='http://127.0.0.1:4199')
        client.__enter__()
        opened.append(client)
        return client

    yield make
    for client in opened:
        try:
            client.__exit__(None, None, None)
        except Exception:          # already closed by the test
            pass


def assert_well_formed(messages):
    """What strict providers require: each tool call answered exactly once before the next turn."""
    unanswered = []
    for m in messages:
        if m['role'] == 'tool':
            assert m['tool_call_id'] in unanswered, f"tool message answers unknown call {m['tool_call_id']}"
            unanswered.remove(m['tool_call_id'])
            continue
        assert not unanswered, f'unanswered tool calls {unanswered} before a {m["role"]} message'
        unanswered = [c['id'] for c in m.get('tool_calls') or []] if m['role'] == 'assistant' else []
    assert not unanswered, f'unanswered tool calls {unanswered} at the end of the turn'


def settle(client, sid, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = client.get(f'/lab/api/research/agent/sessions/{sid}').json()
        if session['state'] != 'running':
            if session['state'] != 'awaiting_approval':
                assert_well_formed(client.get(f'/lab/api/research/agent/sessions/{sid}', params={'messages': 'true'}).json()['messages'])
            return session
        time.sleep(0.05)
    raise AssertionError(f'会话 {sid} 一直在运行')


def test_tool_catalog_is_well_formed():
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))
    assert {t.tier for t in TOOLS} == {'read', 'compute', 'actuate'}
    assert [t.name for t in TOOLS if t.tier == 'actuate'] == ['start_field_simulation']
    for tool in TOOLS:
        schema = tool.schema()['function']['parameters']
        assert schema['type'] == 'object' and set(schema['required']) <= set(schema['properties'])
        json.dumps(tool.schema(), ensure_ascii=False)


def test_agent_composes_solves_verifies_and_grounds_its_answer(workbench):
    state = {}

    def recommend(messages):
        state['scenario'] = last_result(messages)['scenario_id']
        return Reply('地图已生成，先看规则推荐。', [call('recommend', {'scenario_id': state['scenario']})])

    def solve(messages):
        assert last_result(messages)['policy'].startswith('rules-')
        algos = [{'id': 'panel.savings_fleet', 'params': {'time_limit_s': 2}}, {'id': 'pyvrp.fleet', 'params': {'time_limit_s': 2}}]
        return Reply('', [call('solve', {'scenario_id': state['scenario'], 'algorithms': algos})])

    def wait(messages):
        state['runs'] = last_result(messages)['run_ids']
        return Reply('', [call('wait_for_runs', {'run_ids': state['runs'], 'timeout_s': 90})])

    def answer(messages):
        runs = last_result(messages)['runs']
        assert all(r['state'] == 'done' for r in runs)
        lines = [f"{r['run_id']}：{r['algorithm']['name']}，总里程 {r['metrics']['total_distance_m']:.1f} m，"
                 f"独立检查{'通过' if r['check']['valid'] else '未通过'}" for r in runs]
        return Reply('结论：两种算法都给出了通过检查的方案。\n依据：\n' + '\n'.join(lines) + '\n局限：只比较了一个实例。', [])

    model = ScriptedModel(Reply('', [call('compose_scenario', {'text': EXAMPLES[0], 'seed': 7})]), recommend, solve, wait, answer)
    c = workbench(model)
    started = c.post('/lab/api/research/agent/sessions', json={'message': '生成这个果园的采样任务地图，比较两种算法'})
    assert started.status_code == 202 and started.json()['state'] == 'running'
    session = settle(c, started.json()['session_id'])
    assert session['state'] == 'idle', session.get('error')
    kinds = [s['kind'] for s in session['steps']]
    assert kinds[0] == 'user' and kinds[-1] == 'final' and kinds.count('tool') == 4
    assert all(s['ok'] for s in session['steps'] if s['kind'] == 'tool')
    assert [s['tier'] for s in session['steps'] if s['kind'] == 'tool'] == ['compute', 'read', 'compute', 'compute']
    assert session['usage']['model_calls'] == 5 and session['usage']['tool_calls'] == 4
    assert state['scenario'] in session['evidence']['scenarios'] and set(state['runs']) <= set(session['evidence']['runs'])
    for run_id in state['runs']:                      # the runs are ordinary, independently checked workbench runs
        run = c.get(f'/lab/api/runs/{run_id}').json()
        assert run['state'] == 'done' and run['check']['valid'] is True and run['label'].startswith('agent:')
    report = session['grounding']
    assert report['checked'] >= 2 and report['grounded'] == report['checked'] and not report['unknown_ids']
    first = model.calls[0]
    assert first['messages'][0]['role'] == 'system' and set(first['tools']) == {t.name for t in TOOLS}
    full = c.get(f"/lab/api/research/agent/sessions/{session['session_id']}", params={'messages': 'true'}).json()
    replied = {m['tool_call_id'] for m in full['messages'] if m['role'] == 'tool'}
    asked = {tc['id'] for m in full['messages'] if m['role'] == 'assistant' for tc in m.get('tool_calls', [])}
    assert asked == replied and len(asked) == 4
    assert 'messages' not in session and c.get('/lab/api/research/agent/sessions').json()['sessions'][0]['session_id'] == session['session_id']


def test_fabricated_numbers_and_ids_are_flagged(workbench):
    model = ScriptedModel(Reply('', [call('list_algorithms', {'problem_class': 'fleet_routing'})]),
                          Reply('PyVRP 比基线短 37.21%，见 run_20261029T000000_ffffff。', []))
    c = workbench(model)
    session = settle(c, c.post('/lab/api/research/agent/sessions', json={'message': '哪种算法最好？'}).json()['session_id'])
    assert session['state'] == 'idle'
    assert session['grounding']['not_found'] == ['37.21%']
    assert session['grounding']['unknown_ids'] == ['run_20261029T000000_ffffff']


def test_execution_needs_approval_and_denial_is_recorded(workbench):
    started_with = []
    model = ScriptedModel(Reply('', [call('start_field_simulation', {'run_id': 'run_x'})]),
                          Reply('用户拒绝了仿真，本轮只给出规划结果。', []),
                          Reply('', [call('start_field_simulation', {'run_id': 'run_x', 'real_time_factor': 2})]),
                          Reply('已启动田间仿真。', []))
    c = workbench(model)
    c.app.state.lab.fieldsim.start = lambda body: started_with.append(body) or {'session_id': FIELD_SESSION, 'phase': 'starting'}
    sid = c.post('/lab/api/research/agent/sessions', json={'message': '执行这个方案'}).json()['session_id']
    session = settle(c, sid)
    assert session['state'] == 'awaiting_approval' and session['pending']['tool'] == 'start_field_simulation'
    call_id = session['pending']['call_id']
    assert c.post(f'/lab/api/research/agent/sessions/{sid}/messages', json={'message': '再问一句'}).status_code == 409
    assert c.post(f'/lab/api/research/agent/sessions/{sid}/approval', json={'call_id': 'other', 'approve': True}).status_code == 409
    assert c.post(f'/lab/api/research/agent/sessions/{sid}/approval', json={'call_id': call_id, 'approve': False, 'note': '先别跑'}).status_code == 202
    session = settle(c, sid)
    assert session['state'] == 'idle' and started_with == []
    denied = [s for s in session['steps'] if s['kind'] == 'tool'][-1]
    assert denied['ok'] is False and denied['error'] == '用户拒绝执行'
    assert [s['decision'] for s in session['steps'] if s['kind'] == 'approval'] == ['denied']

    c.post(f'/lab/api/research/agent/sessions/{sid}/messages', json={'message': '现在可以执行了'})
    session = settle(c, sid)
    assert session['state'] == 'awaiting_approval'
    assert c.post(f'/lab/api/research/agent/sessions/{sid}/approval', json={'call_id': session['pending']['call_id'], 'approve': True}).status_code == 202
    session = settle(c, sid)
    assert session['state'] == 'idle' and started_with == [{'run_id': 'run_x', 'real_time_factor': 2}]
    assert FIELD_SESSION in session['evidence']['field_sessions']
    assert [s['decision'] for s in session['steps'] if s['kind'] == 'approval'] == ['denied', 'approved']


def test_cancel_while_waiting_for_approval_or_running(workbench):
    gate = threading.Event()

    def slow(messages):
        gate.wait(10)
        return Reply('', [call('list_algorithms', {})])

    model = ScriptedModel(Reply('', [call('start_field_simulation', {'run_id': 'run_x'})]), slow)
    c = workbench(model)
    sid = c.post('/lab/api/research/agent/sessions', json={'message': '执行'}).json()['session_id']
    assert settle(c, sid)['state'] == 'awaiting_approval'
    session = c.post(f'/lab/api/research/agent/sessions/{sid}/cancel', json={}).json()
    assert session['state'] == 'canceled' and session['pending'] is None
    assert [s['decision'] for s in session['steps'] if s['kind'] == 'approval'] == ['canceled']

    c.post(f'/lab/api/research/agent/sessions/{sid}/messages', json={'message': '换个问题'})
    time.sleep(0.2)                                    # the worker is now blocked inside the model call
    c.post(f'/lab/api/research/agent/sessions/{sid}/cancel', json={})
    gate.set()
    session = settle(c, sid)
    assert session['state'] == 'canceled'
    assert not [s for s in session['steps'] if s['kind'] == 'tool']   # the queued call never ran
    full = c.get(f'/lab/api/research/agent/sessions/{sid}', params={'messages': 'true'}).json()
    tool_messages = [m for m in full['messages'] if m['role'] == 'tool']
    assert tool_messages and all(json.loads(m['content']).get('canceled') for m in tool_messages)


def test_turn_budget_stops_a_looping_model(workbench, monkeypatch):
    monkeypatch.setitem(POLICY, 'max_model_calls_per_turn', 3)
    model = ScriptedModel(*[Reply('', [call('list_runs', {'limit': 1})]) for _ in range(10)])
    c = workbench(model)
    session = settle(c, c.post('/lab/api/research/agent/sessions', json={'message': '一直查'}).json()['session_id'])
    assert session['state'] == 'error' and '按预算停止' in session['error']
    assert session['usage']['model_calls'] == 3 and session['usage']['tool_calls'] == 3


def test_bad_tool_calls_come_back_as_errors_the_model_can_fix(workbench):
    model = ScriptedModel(Reply('', [call('drop_database', {}), {'id': '', 'name': 'get_runs', 'arguments': '{oops'},
                                     call('solve', {'scenario_id': 'bench-g3x3_s8000_uturn', 'algorithms': [{'id': 'x'}] * 7}),
                                     call('compose_scenario', {'text': '果园采样', 'overrides': {'teleport': True}}),
                                     call('get_scenario', {'scenario_id': 'no-such-map'}),
                                     {'id': '', 'name': 'list_algorithms', 'arguments': ''}]),
                          Reply('有几个调用失败，已如实记录。', []))
    c = workbench(model)
    session = settle(c, c.post('/lab/api/research/agent/sessions', json={'message': '试试'}).json()['session_id'])
    errors = [s['error'] for s in session['steps'] if s['kind'] == 'tool']
    assert session['state'] == 'idle' and len(errors) == 6
    assert errors[0].startswith('没有工具 drop_database') and errors[1] == '参数不是合法的 JSON 对象'
    assert '最多提交 6 个算法' in errors[2] and 'teleport' in errors[3] and '没有场景 no-such-map' in errors[4]
    assert errors[5] == '参数不是合法的 JSON 对象'
    assert len({s['call_id'] for s in session['steps'] if s['kind'] == 'tool'}) == 6


def test_unconfigured_agent_reports_setup_and_refuses(workbench, monkeypatch):
    for name in ('URP_LLM_API_KEY', 'ZHIPU_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    c = workbench(None)
    status = c.get('/lab/api/research/agent').json()
    assert status['configured'] is False and 'URP_LLM_API_KEY' in status['reason'] and status['tools']
    refused = c.post('/lab/api/research/agent/sessions', json={'message': '你好'})
    assert refused.status_code == 503 and refused.json()['error'] == 'agent_unconfigured'
    assert c.post('/lab/api/research/agent/sessions', json={'message': ''}).status_code == 400
    assert c.get('/lab/api/research/agent/sessions/agent_missing').status_code == 404
    assert '/lab/api/research/agent/sessions' in c.get('/lab/api/openapi.json').json()['paths']


def test_interrupted_turn_is_closed_on_restart(workbench, tmp_path):
    model = ScriptedModel(Reply('', [call('start_field_simulation', {'run_id': 'run_x'})]))
    c = workbench(model, runtime='restart')
    sid = c.post('/lab/api/research/agent/sessions', json={'message': '执行'}).json()['session_id']
    assert settle(c, sid)['state'] == 'awaiting_approval'
    record = c.app.state.lab.store.get_agent_session(sid)
    c.__exit__(None, None, None)
    # The gateway stopped after the approval, before the call ran: running, with an approved call queued.
    store = Store(tmp_path / 'restart' / 'lab.sqlite3')
    record['state'], record['pending'] = 'running', None
    record['queue'][0]['approved'] = True
    store.put_agent_session(record)
    store.close()

    started_with = []
    later = ScriptedModel(Reply('好的，先不执行。', []))
    c = workbench(later, runtime='restart')
    c.app.state.lab.fieldsim.start = lambda body: started_with.append(body) or {'session_id': FIELD_SESSION, 'phase': 'starting'}
    again = c.get(f'/lab/api/research/agent/sessions/{sid}').json()
    assert again['state'] == 'error' and '中断' in again['error']
    c.post(f'/lab/api/research/agent/sessions/{sid}/messages', json={'message': '继续'})
    assert settle(c, sid)['state'] == 'idle'
    assert started_with == []                       # an approval does not carry over into a later turn
    assert_well_formed(later.calls[0]['messages'])  # the history the provider sees is valid
    interrupted = [json.loads(m['content']) for m in later.calls[0]['messages'] if m['role'] == 'tool']
    assert interrupted and interrupted[-1]['interrupted'] is True


def test_grounding_heuristic():
    results = [{'metrics': {'total_distance_m': 512.347, 'gap_mean': 1.5}, 'note': '共 12 个任务点'}]
    report = grounding.check('总里程 512.3 m，差距 1.5%，12 个点，3 台车，PyVRP 0.14.0，2026 年，额外 77.7。',
                             results, ['3 台机器人'], set())
    assert report['not_found'] == ['77.7'] and report['grounded'] == 3
    assert grounding.check('见 run_20261029T000000_abc123。', [], [], {'run_20261029T000000_abc123'})['unknown_ids'] == []


def test_openai_compatible_client_request_and_errors():
    seen = {}

    def handler(request):
        seen['url'], seen['auth'] = str(request.url), request.headers['authorization']
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'content': '', 'reasoning_content': '先查算法',
            'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'name': 'list_algorithms', 'arguments': '{}'}}]}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})

    model = OpenAICompatibleModel('https://example.test/api/v4/', 'glm-test', 'sk-secret', transport=httpx.MockTransport(handler))
    reply = model.complete([{'role': 'user', 'content': '你好'}], [TOOLS[0].schema()])
    assert seen['url'] == 'https://example.test/api/v4/chat/completions' and seen['auth'] == 'Bearer sk-secret'
    assert seen['body']['model'] == 'glm-test' and seen['body']['tool_choice'] == 'auto' and seen['body']['tools']
    assert reply.tool_calls == [{'id': 'c1', 'name': 'list_algorithms', 'arguments': '{}'}]
    assert reply.reasoning == '先查算法' and reply.usage['total_tokens'] == 15
    assert 'sk-secret' not in json.dumps(model.describe())

    failing = OpenAICompatibleModel('https://example.test/v1', 'm', 'k',
                                    transport=httpx.MockTransport(lambda r: httpx.Response(401, text='bad key')))
    with pytest.raises(ModelError, match='HTTP 401'):
        failing.complete([], [])

    assert model_from_env({})[0] is None
    configured, reason = model_from_env({'ZHIPU_API_KEY': 'k', 'URP_LLM_MODEL': 'glm-x'})
    assert reason is None and configured.describe()['endpoint'] == 'open.bigmodel.cn' and configured.model == 'glm-x'
    assert model_from_env({'URP_LLM_API_KEY': 'k', 'URP_LLM_BASE_URL': 'http://example.com/v1'})[0] is None
    assert model_from_env({'URP_LLM_API_KEY': 'k', 'URP_LLM_BASE_URL': 'http://127.0.0.1:11434/v1'})[0] is not None


@pytest.mark.parametrize('failed', [False, True])
def test_cancel_during_model_call_overrides_late_final_or_error(workbench, failed):
    entered, release = threading.Event(), threading.Event()

    def delayed(_messages):
        entered.set()
        assert release.wait(5)
        if failed:
            raise ModelError('late provider failure')
        return Reply('迟到的最终答复', [])

    client = workbench(ScriptedModel(delayed))
    sid = client.post('/lab/api/research/agent/sessions', json={'message': '先分析'}).json()['session_id']
    try:
        assert entered.wait(2)
        client.post(f'/lab/api/research/agent/sessions/{sid}/cancel', json={})
    finally:
        release.set()
    session = settle(client, sid)
    assert session['state'] == 'canceled'
    assert not [step for step in session['steps'] if step['kind'] in ('final', 'error')]
    assert session['usage']['model_calls'] == 1
    full = client.get(f'/lab/api/research/agent/sessions/{sid}', params={'messages': True}).json()
    assert '迟到的最终答复' not in json.dumps(full, ensure_ascii=False)


def test_configured_startup_and_status_reads_do_not_call_model(workbench):
    requests = []
    model = OpenAICompatibleModel('https://example.test/v1', 'configured-test', 'test-only-key',
                                  transport=httpx.MockTransport(lambda request: requests.append(request)))
    client = workbench(model)
    assert client.get('/lab/api/research/agent').json()['configured']
    assert client.get('/lab/api/research/agent/sessions').json() == {'sessions': []}
    client.get('/lab/api/health')
    client.get('/lab/api/openapi.json')
    assert requests == []


@pytest.mark.parametrize('status,body', [(401, 'invalid key test-only-key'), (200, 'test-only-key is not JSON')])
def test_provider_error_cannot_record_api_key(status, body):
    model = OpenAICompatibleModel('https://example.test/v1', 'test', 'test-only-key',
                                  transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body)))
    with pytest.raises(ModelError) as raised:
        model.complete([], [])
    assert 'test-only-key' not in str(raised.value)


def test_clean_shutdown_closes_pending_approval_and_requires_new_turn(workbench, tmp_path):
    model = ScriptedModel(Reply('', [call('start_field_simulation', {'run_id': 'run_x'})]))
    client = workbench(model, runtime='clean-shutdown')
    sid = client.post('/lab/api/research/agent/sessions', json={'message': '申请执行'}).json()['session_id']
    pending = settle(client, sid)['pending']['call_id']
    client.__exit__(None, None, None)

    later = ScriptedModel(Reply('请重新选择要执行的方案。', []))
    restarted = workbench(later, runtime='clean-shutdown')
    session = restarted.get(f'/lab/api/research/agent/sessions/{sid}', params={'messages': True}).json()
    assert session['state'] == 'error' and session['pending'] is None
    assert not later.calls
    assert_well_formed(session['messages'])
    assert restarted.post(f'/lab/api/research/agent/sessions/{sid}/approval', json={'call_id': pending, 'approve': True}).status_code == 409


@pytest.mark.parametrize('kind', ['final', 'error', 'tool'])
def test_model_reply_after_shutdown_cannot_write_or_resume(workbench, tmp_path, caplog, kind):
    entered, release = threading.Event(), threading.Event()

    def delayed(_messages):
        entered.set()
        assert release.wait(5)
        if kind == 'error':
            raise ModelError('late provider failure')
        return Reply('迟到答复', [call('compose_scenario', {'text': EXAMPLES[0]})] if kind == 'tool' else [])

    client = workbench(ScriptedModel(delayed), runtime='late-shutdown')
    manager = client.app.state.lab.research_agent
    sid = client.post('/lab/api/research/agent/sessions', json={'message': '分析'}).json()['session_id']
    try:
        assert entered.wait(2)
        client.__exit__(None, None, None)
    finally:
        release.set()
    for worker in manager._threads._threads:
        worker.join(2)
        assert not worker.is_alive()
    later = ScriptedModel(Reply('收到，请重新提交任务。', []))
    restarted = workbench(later, runtime='late-shutdown')
    session = restarted.get(f'/lab/api/research/agent/sessions/{sid}', params={'messages': True}).json()
    assert session['state'] == 'error' and session['usage']['model_calls'] == 1
    assert not later.calls and not list((tmp_path / 'scenarios').glob('*.json'))
    assert not [entry for entry in caplog.records if 'agent session' in entry.message and 'failed' in entry.message]
    assert_well_formed(session['messages'])


def test_continued_session_records_a_changed_model_without_startup_requests(workbench):
    model = ScriptedModel(Reply('第一轮。', []), Reply('第二轮。', []))
    client = workbench(model)
    sid = client.post('/lab/api/research/agent/sessions', json={'message': '第一问'}).json()['session_id']
    assert settle(client, sid)['model']['model'] == 'script'
    model.describe = lambda: {'provider': 'scripted-test', 'endpoint': None, 'model': 'script-v2'}
    assert client.get('/lab/api/research/agent').json()['model']['model'] == 'script-v2'
    assert len(model.calls) == 1
    client.post(f'/lab/api/research/agent/sessions/{sid}/messages', json={'message': '第二问'})
    session = settle(client, sid)
    assert session['model']['model'] == 'script-v2'
    change = next(step for step in session['steps'] if step['kind'] == 'notice' and step.get('previous_model'))
    assert change['previous_model']['model'] == 'script' and change['model']['model'] == 'script-v2'
