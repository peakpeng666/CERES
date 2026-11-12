"""Connecting a model service with nothing but an API key, against a local stand-in for the service."""
import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient

from ceres_lab.agent import catalog, llm, providers
from ceres_lab.gateway import Config, create_app

GOOD = 'good-test-key-123456'


class Service:
    """Speaks the OpenAI and Anthropic shapes; remembers what it was sent."""

    def __init__(self):
        self.seen, self.model_status, self.replies = [], None, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self):
                return self.headers.get('Authorization') == f'Bearer {GOOD}' or self.headers.get('x-api-key') == GOOD

            def do_GET(self):
                if outer.model_status:
                    return self._send(outer.model_status, {'error': 'nope'})
                if not self._authorized():
                    return self._send(401, {'error': {'message': f"bad key {self.headers.get('Authorization')}"}})
                self._send(200, {'data': [{'id': 'listed-a'}, {'id': 'listed-b'}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.seen.append({'path': self.path, 'body': body, 'auth': self.headers.get('Authorization') or self.headers.get('x-api-key')})
                self._send(200, outer.replies.pop(0))

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def service():
    s = Service()
    yield s
    s.close()


def model(mid, **extra):
    return {'id': mid, 'name': mid.upper(), 'context': 1000, 'output': 500, 'reasoning': False, 'temperature': True, 'released': '2026-01-01',
            'input_price': 1.0, 'output_price': 2.0, 'beta': None, 'reasoning_field': None, **extra}


@pytest.fixture
def home(tmp_path, monkeypatch, service):
    """A private configuration directory and a catalog that points at the stand-in service."""
    monkeypatch.setattr(llm, 'CONFIG_PATH', tmp_path / 'llm.json')
    monkeypatch.setattr(llm, 'CREDENTIALS_PATH', tmp_path / 'credentials.json')
    monkeypatch.setattr(catalog, 'CACHE', tmp_path / 'model-catalog.json')
    monkeypatch.setattr(catalog, '_loaded', None)
    monkeypatch.delenv('CERES_LLM_PROVIDER', raising=False)
    monkeypatch.delenv('STAND_IN_KEY', raising=False)
    services = {'stand-in': {'id': 'stand-in', 'name': '测试服务', 'kind': 'openai', 'base_url': f'{service.url}/v1', 'env': ['STAND_IN_KEY'], 'doc': None, 'key_page': None,
                             'models': [model('stand-mini', released='2026-03-01'), model('stand-large', released='2026-02-01'),
                                        model('stand-think', released='2026-01-01', temperature=False, reasoning=True, reasoning_field='reasoning_content')]},
                'stand-claude': {'id': 'stand-claude', 'name': '测试 Messages', 'kind': 'anthropic', 'base_url': service.url, 'env': [], 'doc': None, 'key_page': None,
                                 'models': [model('claude-x', output=64000)]}}
    (tmp_path / 'model-catalog.json').write_text(json.dumps({'schema': catalog.SCHEMA, 'source': 'test', 'updated_utc': '2026-11-05T00:00:00Z', 'services': services}))
    return tmp_path


def test_a_good_key_is_the_whole_setup(home, service):
    done = providers.connect('stand-in', f'  {GOOD}\n')
    assert done['verified'] is True and done['selected'] == 'stand-in/stand-large', 'a full-size model, not the newest small one'
    p = providers.resolve(done['selected'])
    assert (p.kind, p.model, p.base_url) == ('openai', 'stand-large', f'{service.url}/v1')
    assert p.public()['has_credential'] and p.price == {'input': 1.0, 'output': 2.0, 'currency': 'USD'}
    view = providers.overview()
    assert view['selected'] == 'stand-in/stand-large'
    assert [c['id'] for c in view['connected']] == ['stand-in'] and len(view['connected'][0]['models']) == 3
    assert view['connected'][0]['verified'] is True


def test_the_key_is_private_and_never_comes_back(home, service):
    providers.connect('stand-in', GOOD)
    creds = home / 'credentials.json'
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600
    assert json.loads(creds.read_text())['stand-in'] == {'api_key': GOOD}
    assert GOOD not in json.dumps(providers.overview()) and GOOD not in json.dumps(providers.models_of('stand-in'))
    assert GOOD not in (home / 'llm.json').read_text()


def test_a_rejected_key_is_not_stored_and_not_repeated(home, service):
    with pytest.raises(providers.ProviderError) as e:
        providers.connect('stand-in', 'wrong-test-key-999999')
    assert e.value.code == 'key_rejected' and 'wrong-test-key' not in e.value.message
    assert not (home / 'credentials.json').exists() and providers.selected() is None


@pytest.mark.parametrize('bad', ['', 'short', 'has space inside key', 'x' * 2000, None, '密钥不应是中文字符'])
def test_malformed_keys_never_reach_the_service(home, service, bad):
    with pytest.raises(providers.ProviderError) as e:
        providers.connect('stand-in', bad)
    assert e.value.code == 'invalid_key'


@pytest.mark.parametrize('status', [403, 404, 500])
def test_a_service_that_cannot_confirm_keeps_the_key_unverified(home, service, status):
    service.model_status = status
    done = providers.connect('stand-in', GOOD)
    assert done['verified'] is None and f'HTTP {status}' in done['detail']
    assert providers.resolve('stand-in/stand-large') is not None


def test_an_unreachable_service_keeps_the_key_unverified(home, service):
    service.close()
    done = providers.connect('stand-in', GOOD)
    assert done['verified'] is None and '暂时连不上' in done['detail']


def test_disconnecting_forgets_the_key_and_the_selection_only(home, service):
    llm.set_key('zhipu-coding-plan', 'existing-subscription-key')
    providers.connect('stand-in', GOOD)
    assert providers.disconnect('stand-in')['selected'] is None
    kept = json.loads((home / 'credentials.json').read_text())
    assert 'stand-in' not in kept and kept['zhipu-coding-plan']['api_key'] == 'existing-subscription-key'
    assert providers.resolve('stand-in/stand-large') is None


def test_only_connected_models_can_be_selected(home, service):
    with pytest.raises(providers.ProviderError):
        providers.select('stand-in/stand-large')
    providers.connect('stand-in', GOOD)
    assert providers.select('stand-in/stand-think') == 'stand-in/stand-think'
    with pytest.raises(providers.ProviderError):
        providers.select('stand-in/no-such-model')


def test_older_configurations_keep_working_and_spend_nothing_by_themselves(home, service):
    (home / 'llm.json').write_text(json.dumps({'default': 'local', 'providers': [
        {'id': 'local', 'kind': 'ollama', 'base_url': 'http://127.0.0.1:11434', 'model': 'qwen'},
        {'id': 'plan', 'kind': 'openai', 'label': '订阅端点', 'base_url': f'{service.url}/v1', 'model': 'glm', 'credential': 'plan', 'terms_note': '仅限编程工具'},
        {'id': 'off', 'kind': 'openai', 'base_url': 'http://x', 'model': 'm', 'enabled': False}]}))
    llm.set_key('plan', 'existing-subscription-key')
    view = providers.overview()
    assert view['selected'] is None, 'a subscription key is never picked for the user'
    assert [c['id'] for c in view['connected']] == ['plan']
    assert 'local' not in {r['id'] for r in view['services']} and 'off' not in {r['id'] for r in view['services']}
    assert view['connected'][0]['terms_note'] == '仅限编程工具'
    assert providers.resolve('plan').id == 'plan/glm', 'sessions recorded the bare id'
    assert providers.select('plan/glm') == 'plan/glm'


def test_a_legacy_default_the_user_chose_is_honoured(home, service):
    (home / 'llm.json').write_text(json.dumps({'default': 'plan', 'providers': [{'id': 'plan', 'kind': 'openai', 'base_url': f'{service.url}/v1', 'model': 'glm'}]}))
    llm.set_key('plan', 'existing-subscription-key')
    assert providers.selected() == 'plan/glm'


def test_an_environment_variable_connects_without_a_stored_key(home, service, monkeypatch):
    monkeypatch.setenv('STAND_IN_KEY', 'key-from-the-environment')
    assert providers.overview()['connected'][0]['connected'] == 'env'
    assert llm._secret(providers.resolve('stand-in/stand-large')) == 'key-from-the-environment'
    llm.set_key('stand-in', GOOD)
    assert llm._secret(providers.resolve('stand-in/stand-large')) == GOOD, 'what the user pasted wins'


def test_a_custom_endpoint_needs_a_name_an_address_and_a_key(home, service):
    done = providers.add_custom({'name': 'Lab Gateway', 'base_url': f'{service.url}/v1/', 'api_key': GOOD})
    assert done['service'] == 'custom-lab-gateway' and done['selected'] == 'custom-lab-gateway/listed-a'
    assert GOOD not in (home / 'llm.json').read_text()
    row = next(c for c in providers.overview()['connected'] if c['id'] == 'custom-lab-gateway')
    assert [m['id'] for m in row['models']] == ['listed-a', 'listed-b'] and row['custom']
    typed = providers.add_custom({'name': 'Typed', 'base_url': f'{service.url}/v1', 'api_key': GOOD, 'models': 'one, two\nthree'})
    assert typed['service'] == 'custom-typed'
    assert [m['id'] for m in providers.models_of('custom-typed')['models']] == ['one', 'two', 'three']
    providers.remove_custom('custom-lab-gateway')
    assert 'custom-lab-gateway' not in providers.services() and providers.selected() is None


@pytest.mark.parametrize('body', [{'name': '', 'base_url': 'https://x', 'api_key': GOOD}, {'name': 'a', 'base_url': 'ftp://x', 'api_key': GOOD}, {'name': 'a', 'base_url': 'https://x'}])
def test_incomplete_custom_endpoints_are_refused(home, body):
    with pytest.raises(providers.ProviderError):
        providers.add_custom(body)


def test_a_chat_goes_out_with_the_key_and_the_model_rules(home, service):
    providers.connect('stand-in', GOOD)
    service.replies = [{'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 1000, 'completion_tokens': 500}, 'model': 'stand-large'}]
    r = llm.chat(providers.resolve('stand-in/stand-large'), [{'role': 'user', 'content': 'hi'}])
    assert r.content == 'ok' and r.cost == 0.002 and r.currency == 'USD' and r.provider == 'stand-in/stand-large'
    sent = service.seen[0]
    assert sent['path'] == '/v1/chat/completions' and sent['auth'] == f'Bearer {GOOD}'
    assert sent['body']['model'] == 'stand-large' and sent['body']['temperature'] == 0.2


def test_a_reasoning_model_gets_its_own_reasoning_back_during_tool_use(home, service):
    providers.connect('stand-in', GOOD)
    p = providers.resolve('stand-in/stand-think')
    service.replies = [
        {'choices': [{'message': {'content': '', 'reasoning_content': '先看地图', 'tool_calls': [{'id': 'c1', 'function': {'name': 'look', 'arguments': '{}'}}]}}], 'usage': {}},
        {'choices': [{'message': {'content': '好了'}}], 'usage': {}}]
    tool = {'name': 'look', 'description': 'd', 'parameters': {'type': 'object', 'properties': {}}}
    first = llm.chat(p, [{'role': 'user', 'content': 'go'}], [tool])
    assert first.reasoning == '先看地图' and 'temperature' not in service.seen[0]['body'], 'this model refuses a temperature'
    history = [{'role': 'user', 'content': 'go'}, {'role': 'assistant', 'content': '', 'reasoning': first.reasoning, 'tool_calls': [{'id': 'c1', 'name': 'look', 'arguments': {}}]},
               {'role': 'tool', 'tool_call_id': 'c1', 'name': 'look', 'content': '{}'}]
    llm.chat(p, history, [tool])
    assert service.seen[1]['body']['messages'][1]['reasoning_content'] == '先看地图'


def test_the_messages_api_path_works_with_one_key(home, service):
    done = providers.connect('stand-claude', GOOD)
    assert done['verified'] is True
    p = providers.resolve('stand-claude/claude-x')
    assert p.options['max_tokens'] == 8192
    service.replies = [{'content': [{'type': 'text', 'text': '你好'}], 'usage': {'input_tokens': 3, 'output_tokens': 2}, 'model': 'claude-x'}]
    assert llm.chat(p, [{'role': 'user', 'content': 'hi'}]).content == '你好'
    assert service.seen[0]['path'] == '/v1/messages' and service.seen[0]['auth'] == GOOD


def registry():
    tool = {'tool_call': True, 'modalities': {'output': ['text']}, 'cost': {'input': 1, 'output': 2}, 'limit': {'context': 10, 'output': 5}}
    return {
        'plain': {'npm': '@ai-sdk/openai-compatible', 'api': 'https://a.example/v1/', 'env': ['A'], 'name': 'A', 'models': {
            'x': {**tool, 'id': 'x', 'interleaved': {'field': 'reasoning_content'}, 'release_date': '2026-02-01'},
            'y': {**tool, 'id': 'y', 'release_date': '2026-03-01', 'temperature': False},
            'no-tools': {**tool, 'id': 'no-tools', 'tool_call': False}, 'old': {**tool, 'id': 'old', 'status': 'deprecated'},
            'image': {**tool, 'id': 'image', 'modalities': {'output': ['image']}}}},
        'zhipuai-coding-plan': {'npm': '@ai-sdk/openai-compatible', 'api': 'https://b.example/v1', 'env': ['B'], 'models': {'x': {**tool, 'id': 'x'}}},
        'lmstudio': {'npm': '@ai-sdk/openai-compatible', 'api': 'http://127.0.0.1:1234/v1', 'env': ['C'], 'models': {'x': {**tool, 'id': 'x'}}},
        'on-my-box': {'npm': '@ai-sdk/openai-compatible', 'api': 'http://localhost:9/v1', 'env': ['C'], 'models': {'x': {**tool, 'id': 'x'}}},
        'account': {'npm': '@ai-sdk/openai-compatible', 'api': 'https://${ACCOUNT}.example/v1', 'env': ['D'], 'models': {'x': {**tool, 'id': 'x'}}},
        'amazon-bedrock': {'npm': '@ai-sdk/amazon-bedrock', 'env': ['E'], 'models': {'x': {**tool, 'id': 'x'}}},
        'minimax-cn': {'npm': '@ai-sdk/anthropic', 'api': 'https://c.example/anthropic/v1', 'env': ['F'], 'models': {'x': {**tool, 'id': 'x'}}},
        'openai': {'npm': '@ai-sdk/openai', 'env': ['G'], 'models': {'x': {**tool, 'id': 'x'}}},
        'empty': {'npm': '@ai-sdk/openai-compatible', 'api': 'https://d.example/v1', 'env': ['H'], 'models': {'x': {**tool, 'id': 'x', 'tool_call': False}}},
    }


def test_the_catalog_keeps_what_one_key_can_reach():
    out = catalog.trim(registry(), '2026-11-05T00:00:00Z', 'test')['services']
    assert set(out) == {'plain', 'minimax-cn', 'openai'}
    assert [m['id'] for m in out['plain']['models']] == ['y', 'x'], 'newest first; models without tools, retired or non-text are gone'
    assert out['plain']['base_url'] == 'https://a.example/v1'
    assert out['plain']['models'][1]['reasoning_field'] == 'reasoning_content' and out['plain']['models'][0]['temperature'] is False
    assert (out['minimax-cn']['kind'], out['minimax-cn']['base_url']) == ('anthropic', 'https://c.example/anthropic')
    assert out['openai']['base_url'] == 'https://api.openai.com/v1' and out['minimax-cn']['name'] == 'MiniMax'


def test_the_shipped_snapshot_has_the_services_people_expect():
    data = json.loads(catalog.SNAPSHOT.read_text(encoding='utf-8'))
    assert data['schema'] == catalog.SCHEMA
    assert set(catalog.FEATURED) <= set(data['services'])
    assert not [s for s in data['services'] if catalog.LEFT_OUT.search(s)]
    assert all(m for s in data['services'].values() for m in s['models'])


def test_refreshing_replaces_the_catalog_and_a_failure_keeps_it(home, service, monkeypatch):
    before = set(catalog.services())
    monkeypatch.setattr(catalog, 'SOURCE', f'{service.url}/gone')
    service.model_status = 500
    with pytest.raises(httpx.HTTPError):
        catalog.refresh()
    assert set(catalog.services()) == before
    service.model_status = None
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: httpx.Response(200, json=registry(), request=httpx.Request('GET', 'http://registry.test')))
    meta = catalog.refresh()
    assert set(meta['services']) == {'plain', 'minimax-cn', 'openai'} and set(catalog.services()) == {'plain', 'minimax-cn', 'openai'}


def test_no_network_is_touched_unless_the_app_asks_for_it(home, monkeypatch):
    monkeypatch.delenv('CERES_MODELS_REFRESH', raising=False)
    monkeypatch.setattr(catalog, 'refresh', lambda: pytest.fail('should not fetch'))
    catalog.refresh_in_background()


def test_a_mounted_credentials_file_can_be_read_despite_its_mode(home, monkeypatch):
    creds = home / 'credentials.json'
    creds.write_text(json.dumps({'k': {'api_key': 'abcdefghijkl'}}))
    creds.chmod(0o777)
    with pytest.raises(llm.LLMError):
        llm._credentials()
    monkeypatch.setenv('CERES_CREDENTIALS_MOUNTED', '1')
    assert llm._credentials()['k']['api_key'] == 'abcdefghijkl'


@pytest.fixture
def client(tmp_path, home):
    app = create_app(Config(runtime=tmp_path / 'runtime', background=False))
    with TestClient(app, base_url='http://127.0.0.1:4100') as c:
        yield c


def test_the_whole_exchange_over_http(client, service):
    api = '/lab/api/agent'
    first = client.get(f'{api}/providers').json()
    assert first['selected'] is None and first['connected'] == []
    assert {'id': 'stand-in'}.items() <= next(r for r in first['services'] if r['id'] == 'stand-in').items()
    assert client.post(f'{api}/providers/stand-in/connect', json={'api_key': 'wrong-test-key-999999'}).status_code == 400
    done = client.post(f'{api}/providers/stand-in/connect', json={'api_key': GOOD})
    assert done.status_code == 200 and done.json()['selected'] == 'stand-in/stand-large' and GOOD not in done.text
    assert client.post(f'{api}/model', json={'model': 'stand-in/stand-mini'}).json() == {'selected': 'stand-in/stand-mini'}
    assert client.post(f'{api}/model', json={'model': 'nobody/none'}).status_code == 400
    assert len(client.get(f'{api}/providers/stand-in/models').json()['models']) == 3
    assert client.get(f'{api}/providers/nobody/models').status_code == 404
    assert GOOD not in client.get(f'{api}/providers').text
    session = client.post(f'{api}/sessions', json={'mode': 'hybrid'}).json()
    assert session['provider'] == {'id': 'stand-in/stand-mini', 'kind': 'openai', 'model': 'stand-mini', 'label': 'STAND-MINI · 测试服务'}
    assert client.post(f'{api}/providers/stand-in/disconnect', json={}).json()['selected'] is None
    assert client.post(f'{api}/sessions', json={'mode': 'hybrid'}).status_code == 409


def test_a_web_page_cannot_write_a_key_into_the_app(client):
    bad = client.post('/lab/api/agent/providers/stand-in/connect', json={'api_key': GOOD}, headers={'Origin': 'https://evil.example'})
    assert bad.status_code == 403


class Reply:
    status_code = 200
    text = ''

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def test_anthropic_caches_the_repeated_prompt_and_the_bill_follows(monkeypatch):
    sent = {}

    def post(url, json, headers, timeout):
        sent.update(json)
        return Reply({'content': [{'type': 'text', 'text': '好'}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 10, 'output_tokens': 20, 'cache_read_input_tokens': 900, 'cache_creation_input_tokens': 100}})

    monkeypatch.setattr(llm.httpx, 'post', post)
    provider = llm.Provider(id='a', kind='anthropic', model='m', base_url='https://api.anthropic.com', price={'input': 3.0, 'output': 15.0, 'cached': .3, 'cache_write': 3.75, 'currency': 'USD'})
    tools = [{'name': 'x', 'description': 'x', 'parameters': {}}, {'name': 'y', 'description': 'y', 'parameters': {}}]
    r = llm.chat(provider, [{'role': 'system', 'content': '规则'}, {'role': 'user', 'content': '你好'}], tools)
    assert sent['system'][0]['cache_control'] == {'type': 'ephemeral'} and sent['tools'][-1]['cache_control'] and 'cache_control' not in sent['tools'][0]
    assert r.usage['input_tokens'] == 1010 and r.usage['cached_tokens'] == 900
    assert r.cost == pytest.approx((10 * 3 + 900 * .3 + 100 * 3.75 + 20 * 15) / 1e6)


def test_a_compatible_proxy_never_sees_the_cache_marker(monkeypatch):
    sent = {}

    def post(url, json, headers, timeout):
        sent.update(json)
        return Reply({'content': [], 'usage': {'input_tokens': 5, 'output_tokens': 1}})

    monkeypatch.setattr(llm.httpx, 'post', post)
    provider = llm.Provider(id='p', kind='anthropic', model='m', base_url='https://proxy.example/anthropic')
    llm.chat(provider, [{'role': 'system', 'content': '规则'}, {'role': 'user', 'content': '你好'}], [{'name': 'x', 'description': 'x', 'parameters': {}}])
    assert sent['system'] == '规则' and 'cache_control' not in sent['tools'][0]


def test_cached_tokens_of_openai_style_services_are_billed_at_their_own_price(monkeypatch):
    def post(url, json, headers, timeout):
        return Reply({'choices': [{'message': {'content': '好'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 1000, 'completion_tokens': 10, 'prompt_cache_hit_tokens': 800}})

    monkeypatch.setattr(llm.httpx, 'post', post)
    provider = llm.Provider(id='d', kind='openai', model='m', base_url='https://api.deepseek.com', price={'input': 1.0, 'output': 2.0, 'cached': .1, 'currency': 'USD'})
    r = llm.chat(provider, [{'role': 'user', 'content': '你好'}])
    assert r.cost == pytest.approx((200 * 1 + 800 * .1 + 10 * 2) / 1e6)
