"""What the user has connected: one key per service and one selected model.

A model is addressed as ``service/model``, as in opencode. Pasting a key is the whole setup: the service then appears
with its models, and the first connection selects a model so the next task can use it. Keys go to the private credentials
file through ``llm.set_key`` and no function here returns one. Nothing connects or selects anything on its own, so an
existing subscription key is never spent without the user having chosen it.

Custom OpenAI-compatible endpoints and entries from older configurations live in ``llm.json`` under ``providers``.
"""
from __future__ import annotations

import json
import os
import re
import time

import httpx

from . import catalog, llm

LIGHT = re.compile(r'flash|mini|nano|lite|tiny|small|air|haiku|vision|vl\b|omni|ocr|audio|speech|image|embed|preview|highspeed|turbo|free|instruct', re.I)
KEY_SHAPE = re.compile(r'^[\x21-\x7e]{8,1024}$')
TIMEOUT_S = 180.0


class ProviderError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _config() -> dict:
    try:
        data = json.loads(llm.CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_config(data: dict):
    llm.write_private(llm.CONFIG_PATH, data)


def _custom() -> dict[str, dict]:
    """Endpoints declared in llm.json, shaped like catalog services. Secrets in that file are refused, as before."""
    out = {}
    for item in _config().get('providers') or []:
        if not isinstance(item, dict) or item.get('kind') not in llm.KINDS or not item.get('id') or not item.get('base_url'):
            continue
        if any(k in item for k in ('api_key', 'key', 'token')) or item.get('enabled') is False:
            continue
        ids = [str(m) for m in item.get('models') or []] or ([str(item['model'])] if item.get('model') else [])
        out[str(item['id'])] = {'id': str(item['id']), 'name': item.get('label') or item['id'], 'kind': item['kind'], 'base_url': str(item['base_url']).rstrip('/'),
                                'env': [], 'doc': None, 'key_page': None, 'credential': item.get('credential') or str(item['id']), 'custom': True,
                                'terms_note': item.get('terms_note'), 'options': item.get('options') or {}, 'price': item.get('price'),
                                'timeout_s': item.get('timeout_s'), 'max_retries': item.get('max_retries'),
                                'models': [{'id': m, 'name': m, 'temperature': True} for m in ids]}
    return out


def services() -> dict[str, dict]:
    return {**catalog.services(), **_custom()}


def _provider(service: dict, model: dict) -> llm.Provider:
    options = dict(service.get('options') or {})
    if model.get('temperature', True) and 'temperature' not in options:
        options['temperature'] = 0.2
    if model.get('reasoning_field'):
        options['reasoning_field'] = model['reasoning_field']
    if service['kind'] == 'anthropic':
        options.setdefault('max_tokens', min(int(model.get('output') or 8192), 8192))
    price = service.get('price')
    if price is None and model.get('input_price') is not None:
        price = {'input': model['input_price'], 'output': model.get('output_price') or 0., 'currency': 'USD'}
        price.update({key: model[field] for key, field in (('cached', 'cached_price'), ('cache_write', 'cache_write_price')) if model.get(field) is not None})
    return llm.Provider(id=f"{service['id']}/{model['id']}", kind=service['kind'], model=model['id'], base_url=service['base_url'],
                        label=f"{model.get('name') or model['id']} · {service['name']}", credential=service.get('credential') or service['id'],
                        env=tuple(service.get('env') or ()), price=price, options=options, timeout_s=float(service.get('timeout_s') or TIMEOUT_S),
                        max_retries=int(service.get('max_retries') if service.get('max_retries') is not None else 2), terms_note=service.get('terms_note'))


def connected(service: dict) -> str | None:
    """How this service is connected: by a saved key, by an environment variable, or not at all."""
    if llm.has_stored_key(service.get('credential') or service['id']):
        return 'key'
    return 'env' if any(os.environ.get(name) for name in service.get('env') or ()) else None


def resolve(ref: str | None) -> llm.Provider | None:
    """``service/model`` on a connected service. A bare service id means its first model, which older sessions recorded."""
    if not ref:
        return None
    sid, _, model_id = ref.partition('/')
    service = services().get(sid)
    if service is None or not connected(service):
        return None
    if not model_id:
        return _provider(service, service['models'][0]) if service['models'] else None
    model = next((m for m in service['models'] if m['id'] == model_id), None)
    if model is None:
        if service.get('custom'):
            model = {'id': model_id, 'name': model_id, 'temperature': True}
        else:
            return None
    return _provider(service, model)


def selected() -> str | None:
    """The chosen model, if its service is still connected. ``URP_LLM_PROVIDER`` overrides it for evaluation runs."""
    config = _config()
    for ref in (os.environ.get('URP_LLM_PROVIDER'), config.get('model'), config.get('default')):
        if ref and resolve(ref) is not None:
            return resolve(ref).id
    return None


def select(ref: str) -> str:
    provider = resolve(ref)
    if provider is None:
        raise ProviderError(400, 'unknown_model', '这个模型不存在，或它的服务商还没有连接')
    config = _config()
    config['model'] = provider.id
    _save_config(config)
    return provider.id


def default_model(service: dict) -> dict | None:
    """The newest full-size model with a known price; a person can pick another in one click."""
    models = service.get('models') or []
    return (next((m for m in models if not m.get('beta') and not LIGHT.search(m['id']) and m.get('input_price') is not None), None)
            or next((m for m in models if not m.get('beta')), None) or (models[0] if models else None))


def verify(kind: str, base_url: str, api_key: str, timeout: float = 10.0) -> dict:
    """Ask the service who it thinks we are, on the free model-list endpoint. Only a clear 401 rejects a key."""
    headers = {'x-api-key': api_key, 'anthropic-version': '2023-06-01'} if kind == 'anthropic' else {'Authorization': f'Bearer {api_key}'}
    url = base_url.rstrip('/') + ('/v1/models' if kind == 'anthropic' else '/models')
    try:
        r = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        return {'ok': None, 'detail': f'暂时连不上服务商（{type(exc).__name__}），已保存，调用时再验证', 'models': []}
    if r.status_code == 401:
        return {'ok': False, 'detail': '服务商拒绝了这个 API Key', 'models': []}
    if r.status_code == 200:
        try:
            ids = [str(m['id']) for m in r.json().get('data', []) if isinstance(m, dict) and m.get('id')]
        except ValueError:
            ids = []
        return {'ok': True, 'detail': '已验证', 'models': ids}
    return {'ok': None, 'detail': f'服务商未提供校验结果（HTTP {r.status_code}），已保存，调用时再验证', 'models': []}


def _clean_key(api_key) -> str:
    key = str(api_key or '').strip()
    if not KEY_SHAPE.match(key):
        raise ProviderError(400, 'invalid_key', 'API Key 的格式不对：应为 8 到 1024 个可见字符，不含空格')
    return key


def _remember(sid: str, verdict: dict):
    config = _config()
    config.setdefault('connections', {})[sid] = {'verified': verdict['ok'], 'detail': verdict['detail'], 'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    _save_config(config)


def connect(service_id: str, api_key) -> dict:
    service = services().get(service_id)
    if service is None:
        raise ProviderError(404, 'unknown_service', f'没有服务商 {service_id}')
    key = _clean_key(api_key)
    verdict = verify(service['kind'], service['base_url'], key)
    if verdict['ok'] is False:
        raise ProviderError(400, 'key_rejected', verdict['detail'])
    llm.set_key(service.get('credential') or service_id, key)
    _remember(service_id, verdict)
    if selected() is None and (model := default_model(service)):
        select(f"{service_id}/{model['id']}")
    return {'service': service_id, 'verified': verdict['ok'], 'detail': verdict['detail'], 'selected': selected()}


def disconnect(service_id: str) -> dict:
    service = services().get(service_id)
    if service is None:
        raise ProviderError(404, 'unknown_service', f'没有服务商 {service_id}')
    llm.set_key(service.get('credential') or service_id, None)
    config = _config()
    config.get('connections', {}).pop(service_id, None)
    if str(config.get('model') or '').split('/')[0] == service_id:
        config.pop('model')
    _save_config(config)
    return {'service': service_id, 'selected': selected()}


def add_custom(body: dict) -> dict:
    """Any OpenAI-compatible endpoint: a name, an address, a key and the model ids to offer."""
    name, base_url = str(body.get('name') or '').strip()[:60], str(body.get('base_url') or '').strip().rstrip('/')
    if not name or not re.match(r'^https?://[^\s]+$', base_url):
        raise ProviderError(400, 'invalid_endpoint', '需要名称和以 http:// 或 https:// 开头的接口地址')
    key = _clean_key(body.get('api_key'))
    verdict = verify('openai', base_url, key)
    if verdict['ok'] is False:
        raise ProviderError(400, 'key_rejected', verdict['detail'])
    typed = [m.strip() for m in re.split(r'[\s,，]+', str(body.get('models') or '')) if m.strip()]
    models = typed or verdict['models'][:50]
    if not models:
        raise ProviderError(400, 'no_models', '这个接口没有返回模型列表，请手动填写模型名')
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or f'{int(time.time())}'
    sid = f'custom-{slug}'
    config = _config()
    rows = [p for p in config.get('providers') or [] if not (isinstance(p, dict) and p.get('id') == sid)]
    rows.append({'id': sid, 'kind': 'openai', 'label': name, 'base_url': base_url, 'models': models, 'credential': sid})
    config['providers'] = rows
    _save_config(config)
    llm.set_key(sid, key)
    _remember(sid, verdict)
    if selected() is None:
        select(f'{sid}/{models[0]}')
    return {'service': sid, 'verified': verdict['ok'], 'detail': verdict['detail'], 'selected': selected()}


def remove_custom(service_id: str) -> dict:
    disconnect(service_id)
    config = _config()
    config['providers'] = [p for p in config.get('providers') or [] if not (isinstance(p, dict) and p.get('id') == service_id)]
    _save_config(config)
    return {'service': service_id, 'selected': selected()}


def _public_model(service: dict, m: dict) -> dict:
    return {'ref': f"{service['id']}/{m['id']}", 'id': m['id'], 'name': m.get('name') or m['id'], 'context': m.get('context'), 'reasoning': bool(m.get('reasoning')),
            'input_price': m.get('input_price'), 'output_price': m.get('output_price'), 'released': m.get('released'), 'beta': bool(m.get('beta'))}


def models_of(service_id: str) -> dict:
    service = services().get(service_id)
    if service is None:
        raise ProviderError(404, 'unknown_service', f'没有服务商 {service_id}')
    return {**_row(service), 'models': [_public_model(service, m) for m in service['models']]}


def _row(service: dict) -> dict:
    state = (_config().get('connections') or {}).get(service['id']) or {}
    return {'id': service['id'], 'name': service['name'], 'kind': service['kind'], 'doc': service.get('doc'), 'key_page': service.get('key_page'), 'base_url': service['base_url'],
            'custom': bool(service.get('custom')), 'terms_note': service.get('terms_note'), 'model_count': len(service['models']), 'connected': connected(service),
            'verified': state.get('verified'), 'detail': state.get('detail'), 'env': service.get('env') or []}


def overview() -> dict:
    """Everything the settings page and the model picker show. No key, masked or otherwise, leaves this module."""
    catalog.refresh_in_background()
    found = services()
    rows = sorted((_row(s) for s in found.values()), key=lambda r: catalog.rank(r['id']))
    live = [{**r, 'models': [_public_model(found[r['id']], m) for m in found[r['id']]['models']]} for r in rows if r['connected']]
    meta = catalog.load()
    return {'selected': selected(), 'connected': live, 'services': rows, 'featured': [s for s in catalog.FEATURED if s in found],
            'catalog': {'source': meta.get('source'), 'updated_utc': meta.get('updated_utc'), 'services': len(found)}}
