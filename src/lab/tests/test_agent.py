"""Task agent: contract provenance, argument validation, diagnosis (checker of the checker), runner flows.

The scripted chat below is a test double for the provider interface; it is never registered as a provider and
the product never presents its output as a model result.
"""
import copy
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from urp_lab import paths
from urp_lab.agent import contract as tc
from urp_lab.agent import diagnose, llm, tools
from urp_lab.agent.runner import AgentRunner
from urp_lab.agent.session import claims_check
from urp_lab.compose import EXAMPLES, compose
from urp_lab.gateway import Config, create_app
from urp_lab.scenarios.validate import validate_instance
from urp_lab.solvers import run_algorithm

SMALL = '60×40米果园，2台车，8个采样点，每车容量4，1个池塘'


# ---------------------------------------------------------------- contract --
def test_rule_parse_maps_onto_contract_with_evidence():
    c = tc.from_rule_parse(EXAMPLES[0])
    assert tc.value(c, 'field_size_m') == [200.0, 120.0] and tc.value(c, 'vehicle_count') == 3
    assert tc.value(c, 'task_count') == 40 and tc.value(c, 'vehicle_capacity') == 10
    assert tc.value(c, 'obstacles') == {'pond': 2, 'building': 1} and tc.value(c, 'depot_location') == 'sw'
    assert all(item['source'] == 'user_text' and item['evidence'] for item in c['fields'].values())
    assert tc.missing(c) == []


def test_model_cannot_overwrite_stated_values_without_new_evidence():
    c = tc.from_rule_parse(SMALL)
    c2, accepted, rejected = tc.apply(c, {'vehicle_count': 5}, 'llm')
    assert not accepted and rejected[0]['key'] == 'vehicle_count' and tc.value(c2, 'vehicle_count') == 2
    c3, accepted, _ = tc.apply(c, {'vehicle_count': 5}, 'llm', {'vehicle_count': '改成5台车'})
    assert accepted and tc.value(c3, 'vehicle_count') == 5 and c3['fields']['vehicle_count']['source'] == 'llm'
    c4, accepted, _ = tc.apply(c, {'vehicle_count': 4}, 'user_edit')
    assert accepted and tc.value(c4, 'vehicle_count') == 4


def test_contract_values_are_validated_in_si_units():
    c = tc.empty()
    c, accepted, rejected = tc.apply(c, {'task_count': 2.5, 'vehicle_speed_mps': 40, 'field_size_m': [100, 50],
                                         'objective': 'fastest', 'obstacles': {'pond': 1, 'volcano': 1}}, 'llm')
    keys = {r['key'] for r in rejected}
    assert keys == {'task_count', 'vehicle_speed_mps', 'objective', 'obstacles'}
    assert [a['key'] for a in accepted] == ['field_size_m']


def test_confirmation_requires_key_fields_and_changes_reopen_the_draft():
    c = tc.empty('采样')
    with pytest.raises(ValueError, match='任务点数'):
        tc.confirm(c)
    c, _, _ = tc.apply(c, {'task_count': 10, 'vehicle_count': 2, 'field_area_m2': 20000}, 'user_edit')
    c = tc.confirm(c)
    assert c['status'] == 'confirmed' and c['confirmed_revision'] == c['revision']
    c, _, _ = tc.apply(c, {'vehicle_count': 3}, 'user_edit')
    assert c['status'] == 'draft' and c['history'][-1]['changes'][0]['old'] == 2


def test_compose_overrides_follow_the_composer_spec():
    c = tc.from_rule_parse(SMALL)
    spec = tc.compose_overrides(c)
    assert spec['size'] == [60.0, 40.0] and spec['vehicles'] == 2 and spec['tasks'] == 8 and spec['capacity'] == 4
    sc = compose(SMALL, 42, spec)
    assert len(sc['instance']['tasks']) == 8 and sc['instance']['vehicles']['count'] == 2


def test_tool_schema_accepts_a_valid_update_and_rejects_unknown_fields():
    schema = tc.tool_schema()
    assert tools.validate(schema, {'fields': {'task_count': 12, 'obstacles': {'pond': 1}}}) == []
    assert tools.validate(schema, {'fields': {'task_cnt': 12}})
    assert tools.validate(schema, {'fields': {'task_count': '12'}})


def test_validator_subset():
    schema = {'type': 'object', 'properties': {'a': {'type': 'integer', 'minimum': 1}, 'b': {'type': 'string', 'enum': ['x']},
                                               'c': {'type': 'array', 'items': {'type': 'number'}, 'maxItems': 2}},
              'required': ['a'], 'additionalProperties': False}
    assert tools.validate(schema, {'a': 1, 'b': 'x', 'c': [1.5]}) == []
    errors = tools.validate(schema, {'a': True, 'b': 'y', 'c': [1, 2, 3], 'd': 1})
    assert len(errors) == 4


# ---------------------------------------------------------------- diagnosis --
def _scenario(text, **vehicles):
    sc = compose(text, 7)
    sc['instance']['vehicles'].update(vehicles)
    sc['instance'] = validate_instance('fleet_routing', sc['instance'])
    return sc


def test_precheck_finds_infeasible_tasks():
    sc = _scenario(SMALL, trips='single', capacity=2)
    pre = diagnose.precheck(sc['instance'])
    assert not pre['feasible_by_precheck'] and pre['findings'][0]['code'] == 'single_trip_capacity'
    inst = copy.deepcopy(sc['instance'])
    pond = inst['obstacles'][0]['polygon']
    cx = sum(p[0] for p in pond) / len(pond)
    cy = sum(p[1] for p in pond) / len(pond)
    inst['tasks'][0]['xy'] = [cx, cy]
    inst['tasks'][1]['demand'] = 99
    codes = {f['code'] for f in diagnose.precheck(inst)['findings']}
    assert {'task_inside_obstacle', 'demand_above_capacity'} <= codes


def test_classification_separates_causes():
    base = {'run_id': 'r', 'algorithm': {'id': 'pyvrp.fleet'}, 'problem_class': 'fleet_routing'}
    assert diagnose.classify({**base, 'state': 'timeout'})['category'] == 'budget_insufficient'
    assert diagnose.classify({**base, 'state': 'error', 'error': 'ValueError: 有任务点与所有车库不连通（被障碍隔断）：[3]'})['category'] == 'task_infeasible'
    assert diagnose.classify({**base, 'state': 'error', 'error': 'KeyError: x'})['category'] == 'implementation_error'
    bad = {**base, 'state': 'done', 'status': 'feasible', 'check': {'valid': False, 'errors': ['route 1 collides']}}
    assert diagnose.classify(bad)['category'] == 'implementation_error'
    infeasible = {**base, 'state': 'done', 'status': 'infeasible', 'raw_result': {'reason': '总需求 8 超过车队单趟总容量 4'}}
    assert diagnose.classify(infeasible, {'feasible_by_precheck': False, 'findings': []})['category'] == 'task_infeasible'
    assert diagnose.classify({**base, 'state': 'done', 'status': 'no_solution'})['category'] == 'budget_insufficient'


def test_recheck_confirms_a_real_plan_and_catches_tampering():
    sc = _scenario(SMALL)
    result = run_algorithm(sc, sc['instance'], 'panel.savings_fleet', {})
    run = {**result, 'run_id': 'r1', 'state': 'done', 'problem_class': 'fleet_routing', 'instance': sc['instance'], 'params': {}}
    good = diagnose.recheck(sc, run)
    assert good['performed'] and good['valid'] and good['agrees']
    tampered = copy.deepcopy(run)
    tampered['routes'][0]['sequence'] = tampered['routes'][0]['sequence'][1:]
    bad = diagnose.recheck(sc, tampered)
    assert not bad['valid'] and not bad['agrees']
    assert next(i for i in bad['items'] if i['id'] == 'coverage')['ok'] is False


def test_repairs_need_the_user_only_for_task_changes():
    catalog = [{'id': 'pyvrp.fleet', 'problem_classes': ['fleet_routing'], 'available': True},
               {'id': 'ortools.fleet', 'problem_classes': ['fleet_routing'], 'available': True}]
    run = {'algorithm': {'id': 'pyvrp.fleet'}, 'raw_result': {'reason': '总需求 8 超过车队单趟总容量 4'}}
    actions = diagnose.repairs('budget_insufficient', run, None, catalog, {'pyvrp.fleet'}, 2.0)
    assert [a['action'] for a in actions] == ['raise_budget', 'switch_method'] and not any(a['needs_user'] for a in actions)
    actions = diagnose.repairs('task_infeasible', run, {'findings': [{'code': 'single_trip_capacity'}]}, catalog, set(), 2.0)
    assert actions and all(a['needs_user'] for a in actions)


def test_claims_check_flags_numbers_absent_from_evidence():
    evidence = {'best': {'total_distance_m': 123.456, 'makespan_s': 80.0}, 'runs': 3}
    assert claims_check('最好方案总里程 123.5 m，工期 80 s，共 3 个方案。', evidence) == []
    assert claims_check('最好方案总里程 118.2 m。', evidence) == ['118.2']


# ---------------------------------------------------------------- providers --
def test_provider_config_refuses_inline_secrets(tmp_path, monkeypatch):
    cfg = tmp_path / 'llm.json'
    cfg.write_text(json.dumps({'providers': [
        {'id': 'leaky', 'kind': 'openai', 'model': 'm', 'base_url': 'http://x', 'api_key': 'sk-should-not-be-here'},
        {'id': 'ok', 'kind': 'openai', 'model': 'm', 'base_url': 'http://x', 'credential': 'env:URP_TEST_KEY'}]}))
    monkeypatch.setattr(llm, 'CONFIG_PATH', cfg)
    ids = [p.id for p in llm.load_providers()]
    assert ids == ['ok']
    monkeypatch.delenv('URP_TEST_KEY', raising=False)
    assert llm.load_providers()[0].public()['has_credential'] is False


def test_credentials_file_must_be_private(tmp_path, monkeypatch):
    creds = tmp_path / 'credentials.json'
    creds.write_text(json.dumps({'k': {'api_key': 'abcdefghijkl'}}))
    creds.chmod(0o644)
    monkeypatch.setattr(llm, 'CREDENTIALS_PATH', creds)
    with pytest.raises(llm.LLMError, match='权限'):
        llm._credentials()
    creds.chmod(0o600)
    assert llm._credentials()['k']['api_key'] == 'abcdefghijkl'


def test_redaction_removes_keys_and_tokens():
    text = 'Authorization: Bearer abc.def-123456 key 12065d4ecbd84028a7e08311f435a9f7.6VnVBGrhH5mkMdHn sk-abcdefghijklmnop'
    out = llm.redact(text, ['abc.def-123456'])
    assert 'abc.def' not in out and '6VnVBG' not in out and 'sk-abcdef' not in out


def test_chat_retries_transient_errors_only(monkeypatch):
    p = llm.Provider(id='t', kind='openai', model='m', base_url='http://x', max_retries=2, price={'input': 1.0, 'output': 2.0, 'currency': 'CNY'})
    calls = []

    def flaky(provider, messages, tools_, secret, choice):
        calls.append(1)
        if len(calls) == 1:
            raise llm.LLMError('HTTP 503', status=503)
        return {'content': 'ok', 'tool_calls': [], 'finish_reason': 'stop', 'model': 'm'}, {'input_tokens': 1000, 'output_tokens': 500}
    monkeypatch.setattr(llm, '_call', flaky)
    r = llm.chat(p, [{'role': 'user', 'content': 'hi'}], sleep=lambda s: None)
    assert r.attempts == 2 and r.cost == pytest.approx(0.002) and r.usage['total_tokens'] == 1500

    def auth(provider, messages, tools_, secret, choice):
        raise llm.LLMError('HTTP 401', 'auth', 401)
    monkeypatch.setattr(llm, '_call', auth)
    with pytest.raises(llm.LLMError) as err:
        llm.chat(p, [{'role': 'user', 'content': 'hi'}], sleep=lambda s: None)
    assert err.value.kind == 'auth'


def test_chat_cancellation_abandons_the_call(monkeypatch):
    p = llm.Provider(id='t', kind='openai', model='m', base_url='http://x', max_retries=0)
    gate = threading.Event()

    def slow(*_):
        gate.wait(5)
        return {'content': '', 'tool_calls': [], 'finish_reason': 'stop', 'model': 'm'}, {}
    monkeypatch.setattr(llm, '_call', slow)
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.monotonic()
    with pytest.raises(llm.LLMCancelled):
        llm.chat(p, [{'role': 'user', 'content': 'x'}], cancel=cancel)
    gate.set()
    assert time.monotonic() - start < 2


def test_anthropic_message_conversion_groups_tool_results():
    msgs = [{'role': 'system', 'content': 'S'}, {'role': 'user', 'content': 'u'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'a', 'name': 'x', 'arguments': {}}, {'id': 'b', 'name': 'y', 'arguments': {}}]},
            {'role': 'tool', 'tool_call_id': 'a', 'content': '1'}, {'role': 'tool', 'tool_call_id': 'b', 'content': '2'}]
    system, conv = llm._anthropic_messages(msgs)
    assert system == 'S' and len(conv) == 3 and [b['tool_use_id'] for b in conv[2]['content']] == ['a', 'b']


# ---------------------------------------------------------------- runner --
class Scripted:
    """Test double for ``llm.chat``: returns queued responses and records the tools offered per call."""

    def __init__(self, script):
        self.script, self.offered = list(script), []

    def __call__(self, provider, messages, specs, cancel=None, **_):
        self.offered.append([s['name'] for s in specs or []])
        item = self.script.pop(0) if self.script else {'content': '完成。'}
        if callable(item):
            item = item(messages)
        calls = [{'id': f'c{i}', 'name': n, 'arguments': a, 'parse_error': None} for i, (n, a) in enumerate(item.get('calls', []))]
        return llm.Response(content=item.get('content', ''), tool_calls=calls, usage={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15},
                            latency_s=0.01, attempts=1, finish_reason='stop', model='scripted', provider='scripted', cost=0.0, currency='CNY')


PROVIDER = [llm.Provider(id='scripted', kind='openai', model='scripted', base_url='http://127.0.0.1:9', label='脚本替身')]


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    app = create_app(Config(port=4199, runtime=tmp_path, background=False))
    with TestClient(app, base_url='http://127.0.0.1:4199') as client:
        yield app, client


def _wait(client, sid, states=('done', 'awaiting_confirmation', 'awaiting_user', 'awaiting_approval', 'failed', 'cancelled', 'paused'), timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = client.get(f'/lab/api/agent/sessions/{sid}').json()
        if s['state'] in states:
            return s
        time.sleep(0.1)
    raise AssertionError(f'session stuck in {s["state"]}')


def test_rule_mode_end_to_end(gateway):
    app, client = gateway
    r = client.post('/lab/api/agent/sessions', json={'mode': 'rule', 'text': SMALL})
    assert r.status_code == 201, r.text
    sid = r.json()['session_id']
    s = _wait(client, sid)
    assert s['state'] == 'awaiting_confirmation' and s['mode'] == 'rule'
    assert s['messages'][0]['kind'] == 'notice' and '规则流程' in s['messages'][0]['text']
    card = next(m for m in s['messages'] if m['kind'] == 'confirmation')
    assert any(c['source'] == 'default' for c in card['data']['cards'])
    assert client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'confirm'}).status_code == 200
    s = _wait(client, sid, ('done', 'failed', 'awaiting_approval'))
    assert s['state'] == 'done', s.get('error')
    assert s['scenario_id'] and len(s['run_ids']) >= 2
    best = s['report']['evidence']['best_verified']
    assert best and s['verifications'][best['run_id']]['valid'] and s['verifications'][best['run_id']]['agrees']
    assert all(v['agrees'] for v in s['verifications'].values())
    names = [c['name'] for c in s['tool_calls']]
    assert names[:4] == ['rule_parse', 'build_scenario', 'recommend_algorithms', 'solve'] and 'verify_run' in names
    assert {st['kind']: st['status'] for st in s['plan']}['verify'] == 'done'
    assert s['report']['source'] == 'template' and s['usage']['calls'] == 0
    assert 'llm_messages' not in s


def test_missing_key_fields_are_asked_and_bare_answers_bind(gateway):
    app, client = gateway
    sid = client.post('/lab/api/agent/sessions', json={'mode': 'rule', 'text': '200×120米果园，3台车采样'}).json()['session_id']
    s = _wait(client, sid)
    assert s['state'] == 'awaiting_user' and s['pending']['questions'][0]['field'] == 'task_count'
    client.post(f'/lab/api/agent/sessions/{sid}/messages', json={'text': '40'})
    s = _wait(client, sid)
    assert s['state'] == 'awaiting_confirmation' and tc.value(s['contract'], 'task_count') == 40


def test_infeasible_task_asks_approval_before_changing_it(gateway):
    app, client = gateway
    sid = client.post('/lab/api/agent/sessions', json={'mode': 'rule', 'text': SMALL}).json()['session_id']
    _wait(client, sid)
    client.patch(f'/lab/api/agent/sessions/{sid}/contract', json={'fields': {'trip_mode': 'single', 'vehicle_capacity': 2}})
    s = client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'confirm'}).json()
    s = _wait(client, sid, ('awaiting_approval', 'done', 'failed'))
    assert s['state'] == 'awaiting_approval' and s['pending']['kind'] == 'contract_change'
    assert s['precheck']['findings'][0]['code'] == 'single_trip_capacity'
    assert tc.value(s['contract'], 'trip_mode') == 'single'      # unchanged until approved
    client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'approve'})
    s = _wait(client, sid, ('done', 'failed', 'awaiting_approval'))
    assert s['state'] == 'done' and s['report']['evidence']['best_verified']
    assert s['contract']['fields']['trip_mode']['source'] == 'repair'


def test_llm_flow_uses_phase_tools_and_flags_unverified_numbers(gateway):
    app, client = gateway
    lab = app.state.lab
    script = Scripted([
        {'calls': [('update_task_contract', {'fields': {'task_count': 8, 'vehicle_count': 2, 'field_size_m': [60, 40],
                                                        'vehicle_capacity': 4}, 'evidence': {'task_count': '8个采样点'},
                                             'unsupported': ['每个点须在上午 9 点前完成']})]},
        {'content': '理解为：60×40 m 果园中 2 台车服务 8 个采样点。'},
        {'calls': [('solve', {'algorithms': ['pyvrp.fleet']})]},                # refused: no scenario yet
        {'calls': [('build_scenario', {})]},
        {'calls': [('recommend_algorithms', {})]},
        {'calls': [('solve', {'algorithms': ['pyvrp.fleet', 'panel.savings_fleet'], 'time_limit_s': 1})]},
        {'content': '已完成求解。'},
        {'content': '最好方案总里程 999.9 m。'},
    ])
    lab.agent = AgentRunner(lab, chat=script, providers=PROVIDER)
    sid = client.post('/lab/api/agent/sessions', json={'mode': 'llm', 'text': '果园里两台车给八个点采样'}).json()['session_id']
    s = _wait(client, sid)
    assert s['state'] == 'awaiting_confirmation' and s['contract']['unsupported'] == ['每个点须在上午 9 点前完成']
    assert script.offered[0] == tools.PHASE_TOOLS['understand']
    client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'confirm'})
    s = _wait(client, sid, ('done', 'failed'))
    assert s['state'] == 'done', s.get('error')
    refused = next(c for c in s['tool_calls'] if c['name'] == 'solve')
    assert refused['status'] == 'refused' and refused['error']['code'] == 'no_scenario'
    guard = [c for c in s['tool_calls'] if c['name'] == 'verify_run']
    assert guard and all(c['actor'] == 'system' for c in guard)          # verification enforced by the runner
    assert s['report']['source'] == 'llm' and s['report']['unverified_numbers'] == ['999.9']
    assert s['usage']['calls'] == 8 and s['usage']['input_tokens'] == 80


def test_physical_tools_wait_for_approval_and_rejection_is_recorded(gateway):
    app, client = gateway
    lab = app.state.lab

    def sim_call(messages):
        rid = json.loads(next(m['content'] for m in reversed(messages) if m['role'] == 'tool' and '"runs"' in m['content']))['result']['runs'][0]['run_id']
        return {'calls': [('verify_run', {'run_id': rid}), ('start_simulation', {'run_id': rid})]}
    script = Scripted([
        {'calls': [('update_task_contract', {'fields': {'task_count': 8, 'vehicle_count': 2, 'field_size_m': [60, 40], 'simulate': True}})]},
        {'content': '好的。'},
        {'calls': [('build_scenario', {})]},
        {'calls': [('solve', {'algorithms': ['panel.savings_fleet']})]},
        sim_call,
        {'content': '用户拒绝了仿真，结束。'},
        {'content': '报告。'},
    ])
    lab.agent = AgentRunner(lab, chat=script, providers=PROVIDER)
    sid = client.post('/lab/api/agent/sessions', json={'mode': 'llm', 'auto_confirm': True, 'text': '跑一下仿真'}).json()['session_id']
    s = _wait(client, sid, ('awaiting_approval', 'done', 'failed'))
    assert s['state'] == 'awaiting_approval' and s['pending']['call']['name'] == 'start_simulation'
    assert not any(c['name'] == 'start_simulation' for c in s['tool_calls'])
    client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'reject'})
    s = _wait(client, sid, ('done', 'failed'))
    assert s['state'] == 'done' and not s.get('sim_runs')
    assert any('拒绝' in m['text'] for m in s['messages'] if m['role'] == 'event')


def test_controls_reject_invalid_transitions(gateway):
    app, client = gateway
    sid = client.post('/lab/api/agent/sessions', json={'mode': 'rule'}).json()['session_id']
    assert client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'pause'}).status_code == 409
    assert client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'approve'}).status_code == 409
    assert client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'confirm'}).status_code == 409
    assert client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'explode'}).status_code == 400
    assert client.get('/lab/api/agent/sessions/agt_missing').status_code == 404
    listed = client.get('/lab/api/agent/sessions').json()['sessions']
    assert listed[0]['session_id'] == sid


def test_llm_mode_without_a_usable_provider_is_refused_not_faked(gateway, monkeypatch, tmp_path):
    app, client = gateway
    monkeypatch.setattr(llm, 'CONFIG_PATH', tmp_path / 'none.json')
    monkeypatch.setattr(llm, 'probe', lambda p, timeout=2.0: {'reachable': False, 'model_present': False, 'detail': '本地模型服务未响应'})
    r = client.post('/lab/api/agent/sessions', json={'mode': 'llm', 'text': SMALL})
    assert r.status_code == 409 and r.json()['error'] == 'llm_unavailable'
    auto = client.post('/lab/api/agent/sessions', json={'mode': 'auto'}).json()
    assert auto['mode'] == 'rule' and '未调用大模型' in auto['messages'][0]['text']
    providers = client.get('/lab/api/agent/providers').json()
    assert 'api_key' not in json.dumps(providers)


def test_interrupted_sessions_are_recoverable(gateway):
    app, client = gateway
    lab = app.state.lab
    sid = client.post('/lab/api/agent/sessions', json={'mode': 'rule'}).json()['session_id']
    s = lab.agent.store.get(sid)
    s['state'] = 'running'
    lab.agent.store.save(s)
    fresh = AgentRunner(lab, path=lab.agent.store.path)
    assert fresh.store.get(sid)['state'] == 'interrupted'


# ---------------------------------------------------------------- benchmark --
def test_benchmark_split_is_by_family_and_reproducible(tmp_path):
    from urp_lab.agent.evals import dataset
    built = dataset.build(tmp_path / 'items.jsonl')
    items = dataset.load(tmp_path / 'items.jsonl')
    assert built['sha256'] == dataset.build(tmp_path / 'again.jsonl')['sha256']
    splits = {}
    for it in items:
        splits.setdefault(it['family'], set()).add(it['split'])
    assert all(len(v) == 1 for v in splits.values())          # no family crosses dev/test
    assert {'dev', 'test'} == {s for v in splits.values() for s in v}
    for it in items:
        for turn in it['turns']:
            for key, value in turn['gold'].items():
                tc.normalize(key, value)                        # every gold value is a valid contract value


def test_benchmark_scoring():
    from urp_lab.agent.evals.nlu import same, score_turn
    assert same(20000.1, 20000.100000000002) and same([60, 40.0], [60.0, 40]) and not same(True, 1)
    sc = score_turn({'task_count': 8, 'vehicle_count': 3, 'objective': 'makespan'}, {'task_count': 8, 'vehicle_count': 2, 'field_size_m': [60, 40]})
    assert sc['tp'] == 1 and sc['wrong'] == ['vehicle_count'] and sc['missed'] == ['field_size_m'] and sc['extra'] == ['objective']
    assert sc['fp'] == 2 and sc['fn'] == 2 and not sc['exact']


def test_environment_errors_fail_the_session_instead_of_reporting(gateway, monkeypatch):
    from urp_lab.jobs import SourceChangedError
    app, client = gateway
    lab = app.state.lab

    def refuse(*_args, **_kw):
        raise SourceChangedError('Lab 执行源码已改变')
    monkeypatch.setattr(lab.jobs, 'submit', refuse)
    sid = client.post('/lab/api/agent/sessions', json={'mode': 'rule', 'auto_confirm': True, 'text': SMALL}).json()['session_id']
    s = _wait(client, sid, ('done', 'failed'))
    assert s['state'] == 'failed' and s['error']['kind'] == 'environment' and s['report'] is None
    solve = next(c for c in s['tool_calls'] if c['name'] == 'solve')
    assert solve['error']['code'] == 'source_changed'
    assert {st['kind']: st['status'] for st in s['plan']}['solve'] == 'failed'


def test_inline_tool_calls_are_recovered_only_for_offered_tools():
    text = '好的。<tool_call>{"name": "ask_user", "arguments": {"questions": [{"question": "几台车？"}]}}</tool_call>' \
           '<tool_call>{"name": "rm_rf", "arguments": {}}</tool_call>'
    content, calls = llm.inline_tool_calls(text, {'ask_user'})
    assert [c['name'] for c in calls] == ['ask_user'] and calls[0]['recovered_from_text']
    assert calls[0]['arguments']['questions'][0]['question'] == '几台车？' and '<tool_call>' not in content


def test_session_export_bundles_contract_runs_and_integrity(gateway):
    import hashlib
    app, client = gateway
    sid = client.post('/lab/api/agent/sessions', json={'mode': 'rule', 'auto_confirm': True, 'text': SMALL}).json()['session_id']
    s = _wait(client, sid, ('done', 'failed'))
    assert s['state'] == 'done'
    r = client.get(f'/lab/api/agent/sessions/{sid}/export')
    assert r.status_code == 200 and 'attachment' in r.headers['content-disposition']
    bundle = r.json()
    rec = bundle['record']
    assert rec['schema'] == 'urp.agent-export/1' and set(rec['runs']) == set(s['run_ids']) and rec['scenarios'][s['scenario_id']]
    canonical = json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    assert hashlib.sha256(canonical.encode()).hexdigest() == bundle['integrity']['sha256']
    assert 'api_key' not in r.text and 'llm_messages' not in rec['session']


def test_sealed_fields_never_enter_a_contract():
    assert tc.normalize('real_field_id', 'NL_field_12') == 'nl_field_12'
    assert tc.normalize('real_field_id', 'ee_field_130') == 'ee_field_130'
    for sealed in ('ee_field_5', 'lt_field_3'):
        with pytest.raises(ValueError, match='封存'):
            tc.normalize('real_field_id', sealed)


def test_real_field_contract_builds_on_the_field_boundary(gateway):
    from urp_lab.library import fields
    if not fields.status().get('downloaded'):
        pytest.skip('Fields2Benchmark 未下载')
    fid = next(f['id'] for f in fields.listing('nl', None))
    app, client = gateway
    sid = client.post('/lab/api/agent/sessions', json={'mode': 'rule', 'text': '2台车采样'}).json()['session_id']
    s = _wait(client, sid)
    assert s['state'] == 'awaiting_user'
    r = client.patch(f'/lab/api/agent/sessions/{sid}/contract', json={'fields': {'real_field_id': fid, 'task_count': 10}})
    assert r.status_code == 200 and r.json()['state'] == 'awaiting_confirmation'
    client.post(f'/lab/api/agent/sessions/{sid}/control', json={'action': 'confirm'})
    s = _wait(client, sid, ('done', 'failed'))
    assert s['state'] == 'done', s.get('error')
    built = next(c for c in s['tool_calls'] if c['name'] == 'build_scenario')['result']
    assert built['tasks'] == 10 and built['vehicles'] == 2
    sc = client.get(f"/lab/api/scenarios/{s['scenario_id']}").json()
    assert fid in sc['provenance']['note']
