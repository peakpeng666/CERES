"""Session display state, the harness description and stored evaluations, as the desk surface reads them."""
import json

import pytest

from urp_lab.agent import session as agent_session
from urp_lab.agent import tools
from urp_lab.agent.runner import AgentRunner, MAX_LLM_CALLS, RUNNER_VERSION, RunnerError
from urp_lab.agent.session import AgentStore, new_session, public, set_step, short_title
from urp_lab import paths


def contract_session(text, **fields):
    s = new_session('rule', None, title=text[:18])
    s['contract']['request_text'] = text
    s['contract']['fields'] = {k: {'value': v, 'source': 'user_text', 'evidence': ''} for k, v in fields.items()}
    return s


def test_short_title_names_the_task_from_the_contract():
    s = contract_session('在 200×120 米的果园里，3 台机器人采样', task_kind='sampling', vehicle_count=3, task_count=40)
    assert short_title(s) == '果园采样 · 3 台车 40 点'
    assert public(s)['short_title'] == '果园采样 · 3 台车 40 点'


def test_short_title_prefers_a_real_field_and_falls_back_to_the_title():
    s = contract_session('用 nl_field_12 巡检', real_field_id='nl_field_12', task_kind='inspection', vehicle_count=2, task_count=9)
    assert short_title(s) == '真实田块巡检 · 2 台车 9 点'
    assert short_title(new_session('rule', None, title='新任务标题')) == '新任务标题'


def test_session_meta_survives_a_save_of_the_running_document(tmp_path):
    store = AgentStore(tmp_path / 'agent.sqlite3')
    s = contract_session('果园', task_kind='sampling', vehicle_count=1, task_count=1)
    store.save(s)
    store.set_meta(s['session_id'], {'custom_title': '东区复测', 'pinned': True})
    store.save(s)  # a worker thread saving its own copy must not clear display state
    row = store.list()[0]
    assert row['custom_title'] == '东区复测' and row['pinned'] is True and row['archived'] is False
    assert row['short_title'] == '果园采样 · 1 台车 1 点'
    assert set(row['usage']) == {'input_tokens', 'output_tokens', 'calls', 'latency_s'}
    store.close()


class FakeLab:
    runtime = None

    def __init__(self, path):
        self.runtime = path


@pytest.fixture
def runner(tmp_path):
    r = AgentRunner(FakeLab(tmp_path), path=tmp_path / 'agent.sqlite3', providers=[])
    yield r
    r.store.close()


def test_patch_session_names_pins_and_archives(runner):
    s = new_session('rule', None, title='任务')
    runner.store.save(s)
    sid = s['session_id']
    assert runner.patch_session(sid, {'title': '  北区  ', 'pinned': True})['custom_title'] == '北区'
    assert runner.get(sid)['pinned'] is True
    assert runner.patch_session(sid, {'title': ''})['custom_title'] is None
    assert runner.patch_session(sid, {'archived': True})['archived'] is True
    assert runner.store.list()[0]['archived'] is True
    with pytest.raises(RunnerError):
        runner.patch_session(sid, {})
    with pytest.raises(RunnerError):
        runner.patch_session('agt_missing', {'pinned': True})


def test_harness_lists_every_tool_with_its_gate(runner):
    h = runner.harness()
    by = {t['name']: t for t in h['tools']}
    assert set(by) == set(tools.registry())
    assert by['start_simulation']['needs_approval'] is True and by['start_simulation']['effect'] == 'physical'
    assert by['propose_task_change']['needs_approval'] is True
    assert by['solve']['phases'] == ['execute'] and by['update_task_contract']['phases'] == ['understand']
    assert h['limits']['model_calls_per_turn'] == MAX_LLM_CALLS and h['runner_version'] == RUNNER_VERSION


def test_evals_read_stored_records_without_touching_them(runner, tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    run = root / 'experiments' / 'agent-evals' / '20261030-exec-x'
    run.mkdir(parents=True)
    (run / 'records.json').write_text(json.dumps({'meta': {'mode_requested': 'rule', 'passed': 1, 'scenarios': 2}, 'records': [
        {'scenario': 'X1', 'pass': True, 'state': 'done', 'error': None}, {'scenario': 'X2', 'pass': False, 'state': 'failed', 'error': 'x'}]}), encoding='utf8')
    nlu = root / 'experiments' / 'agent-evals' / '20261030-nlu-x'
    nlu.mkdir()
    (nlu / 'summary.json').write_text(json.dumps({'meta': {'mode_requested': 'rule', 'split': 'dev'}, 'summary': {'all': {'field_f1': 0.9, 'exact_match': 0.8, 'turns': 3}}}), encoding='utf8')
    monkeypatch.setattr(paths, 'REPO', root)
    runs = {r['id']: r for r in runner.evals()['runs']}
    assert runs['20261030-exec-x']['passed'] == 1 and runs['20261030-exec-x']['cases'][1]['passed'] is False
    assert runs['20261030-nlu-x']['metrics']['f1'] == 0.9


def test_plan_step_records_a_decided_repair():
    s = new_session('rule', None)
    set_step(s, 'repair', 'waiting', '等待批准')
    set_step(s, 'repair', 'done', '修改已批准')
    assert next(x for x in s['plan'] if x['kind'] == 'repair')['status'] == 'done'
    assert agent_session.STEP_TITLES['repair'] == '诊断与修复'
