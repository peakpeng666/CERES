"""Runtime regressions: process loss, control races, immutable context and evidence handling."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from ceres_lab.agent import llm
from ceres_lab.agent.runner import AgentRunner, _close_open_calls
from ceres_lab.agent.session import claims_check, new_session
from ceres_lab.agent.research_llm import OpenAICompatibleModel


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('condition did not settle')


def test_killed_tool_is_closed_on_restart_and_history_can_resume(tmp_path):
    script = '''
import json, sys, time
from pathlib import Path
from types import SimpleNamespace
from ceres_lab.agent.runner import AgentRunner
from ceres_lab.agent.tools import Tool
from ceres_lab.agent import contract as tc
root = Path(sys.argv[1])
r = AgentRunner(SimpleNamespace(runtime=root), providers=[])
def slow(ctx, args):
    (root / 'inside-tool').write_text(ctx.session['session_id'])
    time.sleep(60)
def understand(s, cancel, pause):
    s['contract'] = tc.from_rule_parse('60×40米果园，2台车，8个采样点')
    s['llm_messages'].append({'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'already', 'name': 'slow', 'arguments': {}},
        {'id': 'lost', 'name': 'slow', 'arguments': {}}]})
    s['llm_messages'].append({'role': 'tool', 'tool_call_id': 'already', 'name': 'slow', 'content': '{"status":"ok"}'})
    r._tool(s, {'id': 'lost', 'name': 'slow', 'arguments': {}}, 'llm', cancel)
r.tools['slow'] = Tool('slow', 'slow', '', {}, slow, step='understand')
r._understand = understand
r.create({'mode': 'rule', 'text': '开始'})
time.sleep(60)
'''
    proc = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)])
    try:
        wait_for(lambda: (tmp_path / 'inside-tool').exists())
    finally:
        proc.kill()
        proc.wait(timeout=5)
    sid = (tmp_path / 'inside-tool').read_text()
    provider = llm.Provider('test', 'openai', 'script', 'http://127.0.0.1:9')
    seen = []
    def chat(_p, messages, _tools, **kwargs):
        seen.append(messages)
        results = [m for m in messages if m['role'] == 'tool']
        assert [m['tool_call_id'] for m in results] == ['already', 'lost']
        assert json.loads(results[-1]['content'])['status'] == 'interrupted'
        return llm.Response('已恢复', [], {}, 0, 1, 'stop', 'script', 'test', None, None)
    runner = AgentRunner(SimpleNamespace(runtime=tmp_path), chat=chat, providers=[provider])
    try:
        saved = runner.store.get(sid)
        assert saved['state'] == 'interrupted'
        before = json.dumps(saved['llm_messages'])
        _close_open_calls(saved, 'interrupted', 'again')
        assert json.dumps(saved['llm_messages']) == before
        saved.update(mode='llm', provider={'id': 'test'})
        runner.store.save(saved)
        runner.control(sid, 'resume')
        wait_for(lambda: runner.get(sid)['state'] != 'running')
        assert runner.get(sid)['state'] == 'awaiting_confirmation'
        assert len(seen) == 1
        wait_for(lambda: not runner._threads)
        assert not runner._cancel and not runner._pause
    finally:
        runner.shutdown()


@pytest.mark.parametrize('action', ['pause', 'cancel'])
def test_control_does_not_overwrite_worker_progress_or_block_on_tool(tmp_path, action):
    entered, release = threading.Event(), threading.Event()
    runner = AgentRunner(SimpleNamespace(runtime=tmp_path), providers=[])
    def understand(s, cancel, pause):
        s['worker_progress'] = {'run_id': 'owned-result'}
        entered.set()
        assert release.wait(5)
        runner._checkpoint(s, cancel, pause)
    runner._understand = understand
    try:
        sid = runner.create({'mode': 'rule', 'text': '开始'})['session_id']
        assert entered.wait(2)
        start = time.monotonic()
        runner.control(sid, action)
        assert any('已请求' in m['text'] for m in runner.get(sid)['messages'])
        assert time.monotonic() - start < .5
        runner.patch_session(sid, {'title': '用户保留的标题', 'pinned': True})
        release.set()
        wait_for(lambda: runner.get(sid)['state'] != 'running')
        session = runner.get(sid)
        assert session['worker_progress']['run_id'] == 'owned-result'
        assert session['state'] == ('paused' if action == 'pause' else 'cancelled')
        assert session['custom_title'] == '用户保留的标题' and session['pinned']
        assert sum('已请求' in m['text'] for m in session['messages']) == 1
        wait_for(lambda: not runner._threads)
    finally:
        release.set()
        runner.shutdown()


def test_environment_client_uses_shared_retry_and_does_not_record_key(monkeypatch):
    attempts = []
    def handle(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503, text='temporarily unavailable')
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}],
                                        'usage': {'prompt_tokens': 10, 'completion_tokens': 2}})
    shared_chat = llm.chat
    def fast_chat(*args, **kwargs):
        return shared_chat(*args, **kwargs, sleep=lambda _: None)
    monkeypatch.setattr(llm, 'chat', fast_chat)
    model = OpenAICompatibleModel('https://example.test/v1', 'test', 'test-secret-key', transport=httpx.MockTransport(handle))
    reply = model.complete([{'role': 'user', 'content': 'hello'}], [])
    assert reply.content == 'ok' and len(attempts) == 2
    assert reply.usage['total_tokens'] == 12
    assert 'test-secret-key' not in json.dumps(model.describe())


def test_numeric_claims_cannot_round_the_claim_down_to_hide_wrong_precision():
    assert claims_check('工期 80.4 s', {'makespan_s': 80}) == ['80.4']
    assert claims_check('工期 80.4 s', {'makespan_s': 80.38}) == []
    assert claims_check('2026 年试验：工期中位数 104.3 s、90 分位 110.4 s', {'median': 104.26, 'p90': 110.44}) == []


def test_static_prefix_full_tools_and_history_are_stable(tmp_path):
    from ceres_lab.agent import contract as tc
    sent = []
    provider = llm.Provider('test', 'openai', 'script', 'http://127.0.0.1:9')
    def chat(_p, messages, specs, **kwargs):
        sent.append((messages, specs))
        return llm.Response('ok', [], {}, 0, 1, 'stop', 'script', 'test', None, None)
    r = AgentRunner(SimpleNamespace(runtime=tmp_path), chat=chat, providers=[provider])
    s = new_session('llm', {'id': 'test'})
    s['llm_messages'] = [{'role': 'user', 'content': 'hello'}]
    try:
        r._llm(s, 'understand', threading.Event())
        s['contract'] = tc.from_rule_parse('60×40米果园，2台车，8个采样点')
        s['llm_messages'].append({'role': 'assistant', 'content': 'understood'})
        r._llm(s, 'execute', threading.Event())
        assert sent[0][0][0] == sent[1][0][0]
        assert sent[0][1] == sent[1][1] and len(sent[0][1]) == len(r.tools)
        assert sent[0][0][1] == sent[1][0][1]
        assert sent[0][0][-1] != sent[1][0][-1]
        assert s['llm_messages'][0]['content'] == 'hello'
    finally:
        r.shutdown()


def test_anthropic_merges_trailing_state_and_report_disables_inline_tool_calls():
    _, conv = llm._anthropic_messages([
        {'role': 'user', 'content': '规划任务'},
        {'role': 'user', 'content': '[运行状态]\n{}'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'name': 'solve', 'arguments': {}}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': '{"status":"ok"}'},
        {'role': 'user', 'content': '[运行状态]\n{"phase":"execute"}'},
    ])
    assert [m['role'] for m in conv] == ['user', 'assistant', 'user']
    assert conv[2]['content'][0]['type'] == 'tool_result' and conv[2]['content'][1]['type'] == 'text'
    provider = llm.Provider('test', 'openai', 'script', 'https://example.test/v1')
    def handle(_req):
        return httpx.Response(200, json={'choices': [{'message': {'content': '正文 <tool_call>{"name":"solve","arguments":{}}</tool_call>'},
                                                      'finish_reason': 'stop'}], 'usage': {}})
    resp = llm.chat(provider, [{'role': 'user', 'content': 'report'}],
                    [{'name': 'solve', 'description': '', 'parameters': {'type': 'object'}}],
                    tool_choice='none', transport=httpx.MockTransport(handle))
    assert not resp.tool_calls


def test_context_archives_at_boundary_without_sliding_or_changing_history(tmp_path):
    import copy
    r = AgentRunner(SimpleNamespace(runtime=tmp_path), providers=[])
    s = new_session('rule', None)
    s['llm_messages'] = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'old-{i} ' + 'a' * 900} for i in range(82)]
    original = copy.deepcopy(s['llm_messages'])
    try:
        first = r._context(s)
        start = s['context_start']
        s['llm_messages'].append({'role': 'user', 'content': 'one more'})
        second = r._context(s)
        assert second[:-1] == first and s['context_start'] == start
        assert s['llm_messages'][:-1] == original
        ref = json.loads(first[0]['content'].split('] ', 1)[1])
        assert json.loads(Path(ref['path']).read_text()) == original[:start]
    finally:
        r.shutdown()


def test_large_results_are_complete_readable_and_scoped_to_session(tmp_path):
    from ceres_lab.agent.artifacts import ArtifactStore, LIMIT_BYTES
    store = ArtifactStore(tmp_path, 'session-a')
    value = {'status': 'ok', 'result': {'runs': [{'run_id': f'r{i}', 'metrics': {'cost': i + .25}, 'log': '田块' * 3000} for i in range(9)]}}
    payload = store.payload(value)
    assert len(payload.encode()) <= LIMIT_BYTES
    ref = json.loads(payload)['_artifact']
    assert ref['partial'] is True and json.loads(Path(ref['path']).read_text()) == value
    chunk = store.read(ref['artifact_id'], '/result/runs/8/metrics')
    assert json.loads(chunk['content']) == {'cost': 8.25}
    read = store.read(ref['artifact_id'], '/result/runs/8/log', 5000, 800)
    assert read['next_offset'] == 5800
    with pytest.raises(FileNotFoundError):
        ArtifactStore(tmp_path, 'session-b').read(ref['artifact_id'])
    for bad in ('../session-a/' + ref['artifact_id'], '/etc/passwd'):
        with pytest.raises(ValueError):
            store.read(bad)
    Path(ref['path']).write_text('{}')
    with pytest.raises(ValueError, match='完整性'):
        store.read(ref['artifact_id'])


@pytest.mark.parametrize('second,source', [('真实里程 123.5 m', 'llm'), ('新的里程 999.8 m', 'template')])
def test_report_repairs_once_then_falls_back_without_publishing_bad_claim(tmp_path, second, source):
    replies, sent = iter(['虚构里程 999.9 m', second]), []
    provider = llm.Provider('test', 'openai', 'script', 'http://127.0.0.1:9')
    def chat(_p, messages, _tools, **kwargs):
        assert kwargs['tool_choice'] == 'none'
        sent.append(messages)
        return llm.Response(next(replies), [], {}, 0, 1, 'stop', 'script', 'test', None, None)
    r = AgentRunner(SimpleNamespace(runtime=tmp_path), chat=chat, providers=[provider])
    s = new_session('llm', {'id': 'test'})
    r._evidence = lambda _: {'contract': {}, 'runs': [], 'precheck': [], 'unsupported': [], 'distance_m': 123.456}
    try:
        r._report(s, threading.Event())
        assert len(sent) == 2 and '999.9' in sent[-1][-1]['content']
        assert s['report']['source'] == source and s['report']['unverified_numbers'] == []
        assert s['report']['validation_attempts'][0]['unverified_numbers'] == ['999.9']
        assert all('999.' not in m['text'] for m in s['messages'])
    finally:
        r.shutdown()


def test_late_model_reply_after_shutdown_cannot_execute_or_write(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    ran = []
    provider = llm.Provider('test', 'openai', 'script', 'http://127.0.0.1:9')
    def chat(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return llm.Response('', [{'id': 'late', 'name': 'update_task_contract', 'arguments': {'fields': {}}}],
                            {}, 0, 1, 'tool_calls', 'script', 'test', None, None)
    r = AgentRunner(SimpleNamespace(runtime=tmp_path), chat=chat, providers=[provider])
    r.tools['update_task_contract'].handler = lambda ctx, args: ran.append(args) or {}
    sid = r.create({'mode': 'llm', 'text': '开始'})['session_id']
    assert entered.wait(2)
    worker = r._threads[sid]
    join = worker.join
    # Exercise an uncooperative provider without spending five seconds waiting.
    monkeypatch.setattr(worker, 'join', lambda timeout=None: None)
    try:
        r.shutdown()
    finally:
        release.set()
        join(3)
    assert not worker.is_alive() and not ran
    resumed = AgentRunner(SimpleNamespace(runtime=tmp_path), providers=[])
    try:
        s = resumed.store.get(sid)
        assert s['state'] == 'interrupted' and not s['tool_calls']
    finally:
        resumed.shutdown()


def test_research_large_tool_reply_uses_the_same_readable_artifact(tmp_path):
    from ceres_lab.agent.manager import AgentManager
    from ceres_lab.agent.artifacts import ArtifactStore
    manager = object.__new__(AgentManager)
    manager.lab = SimpleNamespace(runtime=tmp_path)
    s = {'session_id': 'research-test', 'messages': []}
    result = {'metrics': [{'value': i / 3} for i in range(1000)]}
    manager._reply(s, {'id': 'one'}, result)
    ref = json.loads(s['messages'][0]['content'])['_artifact']
    restored = ArtifactStore(tmp_path, s['session_id']).read(ref['artifact_id'], '/metrics/999')
    assert json.loads(restored['content']) == result['metrics'][999]


def test_research_status_does_not_occupy_task_agent_root():
    from fastapi import FastAPI
    from ceres_lab.agent.routes import install
    app = FastAPI()
    install(app, SimpleNamespace(research_agent=SimpleNamespace(status=lambda: {})))
    paths = {route.path for route in app.routes}
    assert '/lab/api/research/agent' in paths and '/lab/api/agent' not in paths


def test_old_interrupted_groups_close_before_later_user_messages():
    s = new_session('rule', None)
    s['llm_messages'] = [
        {'role': 'assistant', 'tool_calls': [{'id': 'a', 'name': 'solve', 'arguments': {}}]},
        {'role': 'user', 'content': '继续'},
        {'role': 'assistant', 'content': 'hello'}]
    _close_open_calls(s, 'interrupted', 'unknown result')
    assert [m['role'] for m in s['llm_messages']] == ['assistant', 'tool', 'user', 'assistant']
    assert s['llm_messages'][1]['tool_call_id'] == 'a'
    before = json.dumps(s['llm_messages'])
    _close_open_calls(s, 'interrupted', 'second repair')
    assert json.dumps(s['llm_messages']) == before


def test_recovery_rebuilds_shifted_archive_boundary():
    s = new_session('rule', None)
    s['llm_messages'] = [
        {'role': 'assistant', 'tool_calls': [{'id': 'a', 'name': 'solve', 'arguments': {}}]},
        {'role': 'user', 'content': '继续'},
        {'role': 'assistant', 'tool_calls': [{'id': 'b', 'name': 'solve', 'arguments': {}}]},
        {'role': 'tool', 'tool_call_id': 'b', 'name': 'solve', 'content': '{}'}]
    s['context_start'], s['context_archive'] = 2, {'role': 'user', 'content': 'old archive'}
    _close_open_calls(s, 'interrupted', 'unknown result')
    assert s['context_start'] == 0 and 'context_archive' not in s
    assert s['llm_messages'][1]['tool_call_id'] == 'a'


def test_artifact_preview_budget_also_bounds_very_long_keys(tmp_path):
    from ceres_lab.agent.artifacts import ArtifactStore, LIMIT_BYTES
    store = ArtifactStore(tmp_path, 'long-keys')
    value = {'田' * 2000: 'payload'}
    payload = store.payload(value)
    assert len(payload.encode('utf-8')) <= LIMIT_BYTES
    ref = json.loads(payload)['_artifact']
    assert json.loads(store.read(ref['artifact_id'], '/' + '田' * 2000)['content']) == 'payload'
