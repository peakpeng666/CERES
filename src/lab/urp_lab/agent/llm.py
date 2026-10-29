"""Provider-neutral chat with tool calling, bounded retries, cancellation, usage and cost records.

Providers are declared in ``~/.config/urp-workbench/llm.json`` (``URP_LLM_CONFIG``); secrets live only in
``~/.config/urp-workbench/credentials.json`` (``URP_LLM_CREDENTIALS``, mode 600) or in environment variables, are read
at call time in the gateway process and never returned by any endpoint, log line or export.

Internal message format (converted per provider)::

    {'role': 'system' | 'user' | 'assistant' | 'tool', 'content': str,
     'tool_calls': [{'id': str, 'name': str, 'arguments': dict}],   # assistant only
     'tool_call_id': str, 'name': str}                                # tool only
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import stat
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

CONFIG_PATH = Path(os.environ.get('URP_LLM_CONFIG') or '~/.config/urp-workbench/llm.json').expanduser()
CREDENTIALS_PATH = Path(os.environ.get('URP_LLM_CREDENTIALS') or '~/.config/urp-workbench/credentials.json').expanduser()
KINDS = ('ollama', 'openai', 'anthropic')
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
DEFAULT_PROVIDERS = [{
    'id': 'local', 'kind': 'ollama', 'label': '本地开源模型（Ollama）', 'base_url': 'http://127.0.0.1:11434',
    'model': 'qwen3.8:27b', 'enabled': True, 'price': {'input': 0.0, 'output': 0.0, 'currency': 'CNY'},
    'options': {'temperature': 0.2, 'num_ctx': 16384, 'think': False}, 'timeout_s': 240, 'max_retries': 1,
}]
KEY_PATTERNS = [re.compile(r'(?i)bearer\s+[A-Za-z0-9._\-]{8,}'), re.compile(r'\bsk-[A-Za-z0-9_\-]{12,}'),
                re.compile(r'\b[0-9a-f]{32}\.[A-Za-z0-9]{8,}\b'), re.compile(r'\bya29\.[A-Za-z0-9._\-]{10,}')]


class LLMError(RuntimeError):
    def __init__(self, message: str, kind: str = 'provider_error', status: int | None = None):
        super().__init__(message)
        self.kind, self.status = kind, status


class LLMCancelled(LLMError):
    def __init__(self):
        super().__init__('调用已取消', 'cancelled')


@dataclass
class Provider:
    id: str
    kind: str
    model: str
    base_url: str
    label: str = ''
    enabled: bool = True
    credential: str | None = None          # credentials.json key, "env:NAME" or "gcloud-adc"
    price: dict | None = None              # per million tokens: {'input', 'output', 'currency'}
    options: dict = field(default_factory=dict)
    timeout_s: float = 120.0
    max_retries: int = 2
    terms_note: str | None = None

    def public(self) -> dict:
        return {'id': self.id, 'kind': self.kind, 'model': self.model, 'label': self.label or self.id,
                'base_url': self.base_url, 'enabled': self.enabled, 'has_credential': self.credential is None or bool(_secret(self)),
                'price': self.price, 'terms_note': self.terms_note, 'local': self.kind == 'ollama'}


@dataclass
class Response:
    content: str
    tool_calls: list[dict]
    usage: dict
    latency_s: float
    attempts: int
    finish_reason: str
    model: str
    provider: str
    cost: float | None
    currency: str | None
    reasoning_chars: int = 0

    def record(self) -> dict:
        return {'provider': self.provider, 'model': self.model, 'latency_s': round(self.latency_s, 3),
                'attempts': self.attempts, 'finish_reason': self.finish_reason, 'usage': self.usage,
                'cost': self.cost, 'currency': self.currency, 'tool_calls': [c['name'] for c in self.tool_calls],
                'reasoning_chars': self.reasoning_chars}


def redact(text: str, secrets: list[str] = ()) -> str:
    out = str(text)
    for s in secrets:
        if s and len(s) >= 8:
            out = out.replace(s, '«redacted»')
    for pattern in KEY_PATTERNS:
        out = pattern.sub('«redacted»', out)
    return out


def _credentials() -> dict:
    if not CREDENTIALS_PATH.is_file():
        return {}
    mode = CREDENTIALS_PATH.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise LLMError(f'{CREDENTIALS_PATH} 的权限过宽（须为 600），拒绝读取', 'insecure_credentials')
    try:
        return json.loads(CREDENTIALS_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise LLMError(f'凭据文件无法解析：{type(exc).__name__}', 'invalid_credentials') from None


_ADC_CACHE: dict[str, tuple[float, str]] = {}


def _secret(p: Provider) -> str | None:
    ref = p.credential
    if not ref:
        return None
    if ref.startswith('env:'):
        return os.environ.get(ref[4:]) or None
    if ref == 'gcloud-adc':
        cached = _ADC_CACHE.get('adc')
        if cached and cached[0] > time.time():
            return cached[1]
        try:
            token = subprocess.run(['gcloud', 'auth', 'application-default', 'print-access-token'], capture_output=True,
                                   text=True, timeout=30, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        _ADC_CACHE['adc'] = (time.time() + 45 * 60, token)
        return token
    try:
        item = _credentials().get(ref) or {}
    except LLMError:
        return None
    return item.get('api_key') or None


def load_providers() -> list[Provider]:
    raw = DEFAULT_PROVIDERS
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            raw = data.get('providers') or raw
        except (OSError, json.JSONDecodeError):
            raw = DEFAULT_PROVIDERS
    out = []
    for item in raw:
        if not isinstance(item, dict) or item.get('kind') not in KINDS:
            continue
        if any(k in item for k in ('api_key', 'key', 'token')):
            continue  # secrets in the provider list are refused; use credentials.json
        known = {k: item[k] for k in Provider.__dataclass_fields__ if k in item}
        try:
            out.append(Provider(**known))
        except TypeError:
            continue
    return out


def default_provider_id(providers: list[Provider]) -> str | None:
    wanted = os.environ.get('URP_LLM_PROVIDER')
    if CONFIG_PATH.is_file() and not wanted:
        try:
            wanted = json.loads(CONFIG_PATH.read_text(encoding='utf-8')).get('default')
        except (OSError, json.JSONDecodeError):
            wanted = None
    enabled = [p for p in providers if p.enabled]
    if wanted and any(p.id == wanted for p in enabled):
        return wanted
    return enabled[0].id if enabled else None


def get_provider(provider_id: str | None) -> Provider:
    providers = load_providers()
    pid = provider_id or default_provider_id(providers)
    for p in providers:
        if p.id == pid:
            if not p.enabled:
                raise LLMError(f'模型提供方 {p.label or p.id} 未启用' + (f'：{p.terms_note}' if p.terms_note else ''), 'disabled')
            return p
    raise LLMError('没有已启用的模型提供方；请在 ~/.config/urp-workbench/llm.json 中配置', 'not_configured')


def probe(p: Provider, timeout: float = 2.0) -> dict:
    """Reachability without spending tokens: local servers are listed; remote providers report credential presence."""
    if p.kind == 'ollama':
        try:
            r = httpx.get(p.base_url.rstrip('/') + '/api/tags', timeout=timeout)
            r.raise_for_status()
            names = [m.get('name') for m in r.json().get('models', [])]
        except (httpx.HTTPError, ValueError) as exc:
            return {'reachable': False, 'model_present': False, 'detail': f'本地模型服务未响应（{type(exc).__name__}）'}
        present = p.model in names or f'{p.model}:latest' in names
        return {'reachable': True, 'model_present': present,
                'detail': '就绪' if present else f'服务在线，但尚未下载 {p.model}'}
    secret = _secret(p)
    return {'reachable': None, 'model_present': None,
            'detail': '已配置凭据，按需调用' if secret else '缺少凭据'}


# ---------------------------------------------------------------- formats --
def _openai_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m['role'] == 'assistant' and m.get('tool_calls'):
            out.append({'role': 'assistant', 'content': m.get('content') or '',
                        'tool_calls': [{'id': c['id'], 'type': 'function',
                                        'function': {'name': c['name'], 'arguments': json.dumps(c['arguments'], ensure_ascii=False)}}
                                       for c in m['tool_calls']]})
        elif m['role'] == 'tool':
            out.append({'role': 'tool', 'tool_call_id': m['tool_call_id'], 'content': m['content']})
        else:
            out.append({'role': m['role'], 'content': m.get('content') or ''})
    return out


def _ollama_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m['role'] == 'assistant' and m.get('tool_calls'):
            out.append({'role': 'assistant', 'content': m.get('content') or '',
                        'tool_calls': [{'function': {'name': c['name'], 'arguments': c['arguments']}} for c in m['tool_calls']]})
        elif m['role'] == 'tool':
            out.append({'role': 'tool', 'content': m['content'], 'tool_name': m.get('name', '')})
        else:
            out.append({'role': m['role'], 'content': m.get('content') or ''})
    return out


def _anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system, out = [], []
    for m in messages:
        if m['role'] == 'system':
            system.append(m['content'])
        elif m['role'] == 'assistant':
            blocks = [{'type': 'text', 'text': m['content']}] if m.get('content') else []
            blocks += [{'type': 'tool_use', 'id': c['id'], 'name': c['name'], 'input': c['arguments']} for c in m.get('tool_calls') or []]
            out.append({'role': 'assistant', 'content': blocks or [{'type': 'text', 'text': ''}]})
        elif m['role'] == 'tool':
            block = {'type': 'tool_result', 'tool_use_id': m['tool_call_id'], 'content': m['content']}
            if out and out[-1]['role'] == 'user' and isinstance(out[-1]['content'], list) and out[-1]['content'] \
                    and out[-1]['content'][0].get('type') == 'tool_result':
                out[-1]['content'].append(block)
            else:
                out.append({'role': 'user', 'content': [block]})
        else:
            out.append({'role': 'user', 'content': m.get('content') or ''})
    return '\n\n'.join(system), out


def _parse_arguments(raw) -> tuple[dict, str | None]:
    if isinstance(raw, dict):
        return raw, None
    try:
        value = json.loads(raw or '{}')
    except (TypeError, json.JSONDecodeError) as exc:
        return {}, f'参数不是合法 JSON：{exc}'
    return (value, None) if isinstance(value, dict) else ({}, '参数须为 JSON 对象')


def _call(p: Provider, messages: list[dict], tools: list[dict], secret: str | None, tool_choice: str | None) -> tuple[dict, dict]:
    """One HTTP attempt; returns (normalized message, usage)."""
    timeout = httpx.Timeout(p.timeout_s, connect=10.0)
    opts = dict(p.options or {})
    if p.kind == 'ollama':
        body = {'model': p.model, 'messages': _ollama_messages(messages), 'stream': False,
                'options': {k: opts[k] for k in ('temperature', 'num_ctx', 'top_p', 'seed') if k in opts},
                'keep_alive': opts.get('keep_alive', '10m')}
        if 'think' in opts:
            body['think'] = opts['think']
        if tools:
            body['tools'] = [{'type': 'function', 'function': t} for t in tools]
        r = httpx.post(p.base_url.rstrip('/') + '/api/chat', json=body, timeout=timeout)
        _raise_for(r, secret)
        data = r.json()
        msg = data.get('message') or {}
        calls = []
        for c in msg.get('tool_calls') or []:
            fn = c.get('function') or {}
            args, err = _parse_arguments(fn.get('arguments'))
            calls.append({'id': c.get('id') or f'call_{uuid.uuid4().hex[:10]}', 'name': fn.get('name', ''), 'arguments': args,
                          'parse_error': err})
        usage = {'input_tokens': data.get('prompt_eval_count'), 'output_tokens': data.get('eval_count')}
        return {'content': msg.get('content') or '', 'tool_calls': calls, 'finish_reason': data.get('done_reason') or 'stop',
                'reasoning_chars': len(msg.get('thinking') or ''), 'model': data.get('model') or p.model}, usage
    if p.kind == 'openai':
        body = {'model': p.model, 'messages': _openai_messages(messages)}
        for k in ('temperature', 'top_p', 'max_tokens'):
            if k in opts:
                body[k] = opts[k]
        if opts.get('extra_body'):
            body.update(opts['extra_body'])
        if tools:
            body['tools'] = [{'type': 'function', 'function': t} for t in tools]
            body['tool_choice'] = tool_choice or 'auto'
        headers = {'Authorization': f'Bearer {secret}'} if secret else {}
        r = httpx.post(p.base_url.rstrip('/') + '/chat/completions', json=body, headers=headers, timeout=timeout)
        _raise_for(r, secret)
        data = r.json()
        choice = (data.get('choices') or [{}])[0]
        msg = choice.get('message') or {}
        calls = []
        for c in msg.get('tool_calls') or []:
            fn = c.get('function') or {}
            args, err = _parse_arguments(fn.get('arguments'))
            calls.append({'id': c.get('id') or f'call_{uuid.uuid4().hex[:10]}', 'name': fn.get('name', ''), 'arguments': args,
                          'parse_error': err})
        u = data.get('usage') or {}
        usage = {'input_tokens': u.get('prompt_tokens'), 'output_tokens': u.get('completion_tokens')}
        return {'content': msg.get('content') or '', 'tool_calls': calls, 'finish_reason': choice.get('finish_reason') or 'stop',
                'reasoning_chars': len(msg.get('reasoning_content') or ''), 'model': data.get('model') or p.model}, usage
    system, conv = _anthropic_messages(messages)
    body = {'model': p.model, 'max_tokens': int(opts.get('max_tokens', 4096)), 'messages': conv}
    if system:
        body['system'] = system
    if 'temperature' in opts:
        body['temperature'] = opts['temperature']
    if tools:
        body['tools'] = [{'name': t['name'], 'description': t['description'], 'input_schema': t['parameters']} for t in tools]
        body['tool_choice'] = {'type': 'any' if tool_choice == 'required' else 'auto'}
    headers = {'x-api-key': secret or '', 'anthropic-version': '2023-06-01'}
    r = httpx.post(p.base_url.rstrip('/') + '/v1/messages', json=body, headers=headers, timeout=timeout)
    _raise_for(r, secret)
    data = r.json()
    text = ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
    calls = [{'id': b['id'], 'name': b['name'], 'arguments': b.get('input') or {}, 'parse_error': None}
             for b in data.get('content', []) if b.get('type') == 'tool_use']
    u = data.get('usage') or {}
    return {'content': text, 'tool_calls': calls, 'finish_reason': data.get('stop_reason') or 'stop', 'reasoning_chars': 0,
            'model': data.get('model') or p.model}, {'input_tokens': u.get('input_tokens'), 'output_tokens': u.get('output_tokens')}


def _raise_for(r: httpx.Response, secret: str | None):
    if r.status_code < 400:
        return
    detail = redact(r.text[:400], [secret or ''])
    kind = 'rate_limited' if r.status_code == 429 else 'auth' if r.status_code in (401, 403) else 'provider_error'
    raise LLMError(f'HTTP {r.status_code}：{detail}', kind, r.status_code)


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, LLMError):
        return exc.status in RETRY_STATUS
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix='llm')


def chat(provider: Provider, messages: list[dict], tools: list[dict] | None = None, *, tool_choice: str | None = None,
         cancel: threading.Event | None = None, sleep=time.sleep) -> Response:
    """Bounded retries on transport errors, 429 and 5xx only; a cancelled call is abandoned and reported as such."""
    secret = _secret(provider)
    if provider.credential and not secret:
        raise LLMError(f'{provider.label or provider.id} 缺少凭据', 'not_configured')
    start = time.monotonic()
    attempts, last = 0, None
    for attempt in range(max(0, int(provider.max_retries)) + 1):
        attempts = attempt + 1
        if cancel is not None and cancel.is_set():
            raise LLMCancelled()
        future = _POOL.submit(_call, provider, messages, tools or [], secret, tool_choice)
        try:
            while True:
                try:
                    msg, usage = future.result(timeout=0.2)
                    break
                except concurrent.futures.TimeoutError:
                    if cancel is not None and cancel.is_set():
                        future.cancel()
                        raise LLMCancelled() from None
        except LLMCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - normalized below
            last = exc
            if attempt < provider.max_retries and _retryable(exc):
                sleep(min(8.0, 1.0 * 3 ** attempt))
                continue
            if isinstance(exc, LLMError):
                raise
            kind = 'timeout' if isinstance(exc, httpx.TimeoutException) else 'connection' if isinstance(exc, httpx.TransportError) else 'provider_error'
            raise LLMError(redact(f'{type(exc).__name__}: {exc}', [secret or '']), kind) from None
        price = provider.price or {}
        cost = None
        if usage.get('input_tokens') is not None and usage.get('output_tokens') is not None and 'input' in price:
            cost = round((usage['input_tokens'] * price['input'] + usage['output_tokens'] * price.get('output', 0)) / 1e6, 6)
        usage['total_tokens'] = (usage.get('input_tokens') or 0) + (usage.get('output_tokens') or 0)
        return Response(content=msg['content'], tool_calls=msg['tool_calls'], usage=usage,
                        latency_s=time.monotonic() - start, attempts=attempts, finish_reason=msg['finish_reason'],
                        model=msg['model'], provider=provider.id, cost=cost, currency=price.get('currency'),
                        reasoning_chars=msg.get('reasoning_chars', 0))
    raise LLMError(str(last), 'provider_error')  # pragma: no cover - loop always returns or raises
