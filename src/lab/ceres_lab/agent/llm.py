# CERES 多厂商大模型统一调用客户端：支持工具调用 (Function Calling)、指数退避重试与用量统计
# 密钥仅从凭证文件或环境变量读取，严禁泄漏入日志与导出数据
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import stat
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..env_compat import config_directory

log = logging.getLogger('ceres_lab.agent')

CONFIG_PATH = Path(os.environ.get('CERES_LLM_CONFIG') or config_directory() / 'llm.json').expanduser()
CREDENTIALS_PATH = Path(os.environ.get('CERES_LLM_CREDENTIALS') or config_directory() / 'credentials.json').expanduser()
KINDS = ('openai', 'anthropic')
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
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
    env: tuple = ()                        # environment variables that also hold this service's key
    price: dict | None = None              # per million tokens: {'input', 'output', 'cached', 'cache_write', 'currency'}
    options: dict = field(default_factory=dict)
    timeout_s: float = 120.0
    max_retries: int = 2
    terms_note: str | None = None

    def public(self) -> dict:
        return {'id': self.id, 'kind': self.kind, 'model': self.model, 'label': self.label or self.id,
                'base_url': self.base_url, 'enabled': self.enabled, 'has_credential': self.credential is None or bool(_secret(self)),
                'price': self.price, 'terms_note': self.terms_note}


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
    reasoning: str | None = None           # handed back on the next call by models that require their own reasoning text

    def record(self) -> dict:
        return {'provider': self.provider, 'model': self.model, 'latency_s': round(self.latency_s, 3),
                'attempts': self.attempts, 'finish_reason': self.finish_reason, 'usage': self.usage,
                'cost': self.cost, 'currency': self.currency, 'tool_calls': [c['name'] for c in self.tool_calls],
                'reasoning_chars': self.reasoning_chars}


def redact(text: str, secrets: list[str] = ()) -> str:
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, '«redacted»')
    for pattern in KEY_PATTERNS:
        out = pattern.sub('«redacted»', out)
    return out


def _credentials() -> dict:
    if not CREDENTIALS_PATH.is_file():
        return {}
    mode = CREDENTIALS_PATH.stat().st_mode
    # POSIX modes do not exist on Windows or on a volume bind-mounted from it; there the user profile's ACL applies.
    if os.name != 'nt' and os.environ.get('CERES_CREDENTIALS_MOUNTED') != '1' and mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise LLMError(f'{CREDENTIALS_PATH} 的权限过宽（须为 600），拒绝读取', 'insecure_credentials')
    try:
        return json.loads(CREDENTIALS_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise LLMError(f'凭据文件无法解析：{type(exc).__name__}', 'invalid_credentials') from None


_write_lock = threading.Lock()


def write_private(path: Path, data: dict):
    """Atomic JSON write that is never readable by another user, not even for a moment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def set_key(ref: str, api_key: str | None):
    """Store or forget one service's key. Other entries of the file are left as they are."""
    with _write_lock:
        data = _credentials()
        if api_key is None:
            data.pop(ref, None)
        else:
            data[ref] = {'api_key': api_key}
        write_private(CREDENTIALS_PATH, data)


def has_stored_key(ref: str) -> bool:
    try:
        return bool((_credentials().get(ref) or {}).get('api_key'))
    except LLMError:
        return False


_ADC_CACHE: dict[str, tuple[float, str]] = {}


def _secret(p: Provider) -> str | None:
    ref = p.credential
    if not ref:
        return None
    for name in p.env:
        if os.environ.get(name) and not has_stored_key(ref):
            return os.environ[name]
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


# ---------------------------------------------------------------- formats --
def _openai_messages(messages: list[dict], reasoning_field: str | None = None) -> list[dict]:
    out = []
    for m in messages:
        if m['role'] == 'assistant' and m.get('tool_calls'):
            item = {'role': 'assistant', 'content': m.get('content') or '',
                    'tool_calls': [{'id': c['id'], 'type': 'function',
                                    'function': {'name': c['name'], 'arguments': json.dumps(c['arguments'], ensure_ascii=False)}}
                                   for c in m['tool_calls']]}
            if reasoning_field and m.get('reasoning'):
                item[reasoning_field] = m['reasoning']
            out.append(item)
        elif m['role'] == 'tool':
            out.append({'role': 'tool', 'tool_call_id': m['tool_call_id'], 'content': m['content']})
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
            if out and out[-1]['role'] == 'user':
                prev = out[-1]['content']
                out[-1]['content'] = ([{'type': 'text', 'text': prev}] if isinstance(prev, str) else prev) + [block]
            else:
                out.append({'role': 'user', 'content': [block]})
        else:
            text = m.get('content') or ''
            if out and out[-1]['role'] == 'user':
                prev = out[-1]['content']
                out[-1]['content'] = ([{'type': 'text', 'text': prev}] if isinstance(prev, str) else prev) + [{'type': 'text', 'text': text}]
            else:
                out.append({'role': 'user', 'content': text})
    return '\n\n'.join(system), out


def _parse_arguments(raw) -> tuple[dict, str | None]:
    if isinstance(raw, dict):
        return raw, None
    try:
        value = json.loads(raw or '{}')
    except (TypeError, json.JSONDecodeError) as exc:
        return {}, f'参数不是合法 JSON：{exc}'
    return (value, None) if isinstance(value, dict) else ({}, '参数须为 JSON 对象')


INLINE_CALL = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.S)


def inline_tool_calls(content: str, allowed: set[str]) -> tuple[str, list[dict]]:
    """Recover tool calls an open-weight model wrote as ``<tool_call>{json}</tool_call>`` text instead of structured calls."""
    calls = []
    for raw in INLINE_CALL.findall(content or ''):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        name = data.get('name') if isinstance(data, dict) else None
        if name in allowed:
            args, err = _parse_arguments(data.get('arguments', {}))
            calls.append({'id': f'call_{uuid.uuid4().hex[:10]}', 'name': name, 'arguments': args, 'parse_error': err,
                          'recovered_from_text': True})
    return (INLINE_CALL.sub('', content).strip() if calls else content), calls


def _call(p: Provider, messages: list[dict], tools: list[dict], secret: str | None, tool_choice: str | None,
          *, transport: httpx.BaseTransport | None = None) -> tuple[dict, dict]:
    """One HTTP attempt; returns (normalized message, usage)."""
    timeout = httpx.Timeout(p.timeout_s, connect=10.0)
    opts = dict(p.options or {})

    def post(url, **kwargs):
        if transport is None:
            return httpx.post(url, **kwargs)
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    if p.kind == 'openai':
        field_name = opts.get('reasoning_field')
        body = {'model': p.model, 'messages': _openai_messages(messages, field_name)}
        for k in ('temperature', 'top_p', 'max_tokens'):
            if k in opts:
                body[k] = opts[k]
        if opts.get('extra_body'):
            body.update(opts['extra_body'])
        if tools:
            body['tools'] = [{'type': 'function', 'function': t} for t in tools]
            body['tool_choice'] = tool_choice or 'auto'
        headers = {'Authorization': f'Bearer {secret}'} if secret else {}
        r = post(p.base_url.rstrip('/') + '/chat/completions', json=body, headers=headers, timeout=timeout)
        _raise_for(r, secret)
        data = r.json()
        if not isinstance(data, dict) or not isinstance(data.get('choices'), list) or not data['choices']:
            raise LLMError('模型返回缺少 choices', 'invalid_response')
        choice = data['choices'][0]
        msg = choice.get('message') if isinstance(choice, dict) else None
        if not isinstance(msg, dict) or (msg.get('content') is not None and not isinstance(msg['content'], str)):
            raise LLMError('模型返回的 message/content 类型无效', 'invalid_response')
        if msg.get('tool_calls') is not None and not isinstance(msg['tool_calls'], list):
            raise LLMError('模型返回的 tool_calls 须为数组', 'invalid_response')
        calls = []
        for c in msg.get('tool_calls') or []:
            fn = c.get('function') if isinstance(c, dict) else None
            if not isinstance(fn, dict) or not isinstance(fn.get('name'), str) or not fn['name']:
                raise LLMError('模型返回的工具调用缺少函数名', 'invalid_response')
            args, err = _parse_arguments(fn.get('arguments'))
            calls.append({'id': c.get('id') or f'call_{uuid.uuid4().hex[:10]}', 'name': fn.get('name', ''), 'arguments': args,
                          'parse_error': err})
        u = data.get('usage') or {}
        cached = (u.get('prompt_tokens_details') or {}).get('cached_tokens') or u.get('prompt_cache_hit_tokens') or 0
        usage = {'input_tokens': u.get('prompt_tokens'), 'output_tokens': u.get('completion_tokens'), 'cached_tokens': cached}
        content = msg.get('content') or ''
        if tools and tool_choice != 'none' and not calls:
            content, calls = inline_tool_calls(content, {t['name'] for t in tools})
        reasoning = msg.get(field_name or 'reasoning_content') or ''
        return {'content': content, 'tool_calls': calls, 'finish_reason': choice.get('finish_reason') or 'stop', 'reasoning': reasoning if field_name else None,
                'reasoning_chars': len(reasoning), 'model': data.get('model') or p.model}, usage
    system, conv = _anthropic_messages(messages)
    body = {'model': p.model, 'max_tokens': int(opts.get('max_tokens', 4096)), 'messages': conv}
    # The system prompt and tools repeat on every turn of a session. Anthropic caches them only when asked; OpenAI-style
    # services do it on their own. Compatible proxies are left alone, since a marker they do not know can be refused.
    mark = {'cache_control': {'type': 'ephemeral'}} if urlsplit(p.base_url).hostname == 'api.anthropic.com' else {}
    if system:
        body['system'] = [{'type': 'text', 'text': system, **mark}] if mark else system
    if 'temperature' in opts:
        body['temperature'] = opts['temperature']
    if tools:
        body['tools'] = [{'name': t['name'], 'description': t['description'], 'input_schema': t['parameters']} for t in tools]
        body['tools'][-1].update(mark)
        body['tool_choice'] = {'type': 'none' if tool_choice == 'none' else 'any' if tool_choice == 'required' else 'auto'}
    headers = {'x-api-key': secret or '', 'anthropic-version': '2023-06-01'}
    r = post(p.base_url.rstrip('/') + '/v1/messages', json=body, headers=headers, timeout=timeout)
    _raise_for(r, secret)
    data = r.json()
    text = ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
    calls = [{'id': b['id'], 'name': b['name'], 'arguments': b.get('input') or {}, 'parse_error': None}
             for b in data.get('content', []) if b.get('type') == 'tool_use']
    u = data.get('usage') or {}
    read, written = u.get('cache_read_input_tokens') or 0, u.get('cache_creation_input_tokens') or 0
    return {'content': text, 'tool_calls': calls, 'finish_reason': data.get('stop_reason') or 'stop', 'reasoning_chars': 0, 'model': data.get('model') or p.model}, {
        'input_tokens': None if u.get('input_tokens') is None else u['input_tokens'] + read + written, 'output_tokens': u.get('output_tokens'),
        'cached_tokens': read, 'cache_write_tokens': written}


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
         cancel: threading.Event | None = None, sleep=time.sleep, api_key: str | None = None,
         transport: httpx.BaseTransport | None = None) -> Response:
    """Bounded retries on transport errors, 429 and 5xx only; a cancelled call is abandoned and reported as such."""
    secret = api_key if api_key is not None else _secret(provider)
    if provider.credential and not secret:
        raise LLMError(f'{provider.label or provider.id} 缺少凭据', 'not_configured')
    start = time.monotonic()
    attempts, last = 0, None
    for attempt in range(max(0, int(provider.max_retries)) + 1):
        attempts = attempt + 1
        if cancel is not None and cancel.is_set():
            raise LLMCancelled()
        kwargs = {'transport': transport} if transport is not None else {}
        future = _POOL.submit(_call, provider, messages, tools or [], secret, tool_choice, **kwargs)
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
            cached, written = usage.get('cached_tokens') or 0, usage.get('cache_write_tokens') or 0
            plain = usage['input_tokens'] - cached - written
            cost = round((plain * price['input'] + cached * price.get('cached', price['input']) + written * price.get('cache_write', price['input'])
                          + usage['output_tokens'] * price.get('output', 0)) / 1e6, 6)
        usage['total_tokens'] = (usage.get('input_tokens') or 0) + (usage.get('output_tokens') or 0)
        return Response(content=msg['content'], tool_calls=msg['tool_calls'], usage=usage,
                        latency_s=time.monotonic() - start, attempts=attempts, finish_reason=msg['finish_reason'],
                        model=msg['model'], provider=provider.id, cost=cost, currency=price.get('currency'),
                        reasoning_chars=msg.get('reasoning_chars', 0), reasoning=msg.get('reasoning'))
    raise LLMError(str(last), 'provider_error')  # pragma: no cover - loop always returns or raises

# 兼容环境变量直连的科研对话客户端接口
from .research_llm import ModelError, OpenAICompatibleModel, Reply, model_from_env
