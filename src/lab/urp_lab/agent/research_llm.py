# CERES 科研智能体大模型适配层（复用统一重试与私钥存储，支持环境变量直连）
from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from typing import Mapping, Protocol
from urllib.parse import urlsplit

import httpx

DEFAULT_BASE_URL = 'https://open.bigmodel.cn/api/paas/v4'
DEFAULT_MODEL = 'glm-5.3'
KEY_VARS = ('URP_LLM_API_KEY', 'ZHIPU_API_KEY')
SETUP_HINT = ('在启动网关的环境里设置 URP_LLM_API_KEY（或 ZHIPU_API_KEY），可选 URP_LLM_MODEL、URP_LLM_BASE_URL，'
              '然后重启工作台。任何 OpenAI 兼容的 /chat/completions 接口都可以使用。')


class ModelError(RuntimeError):
    """The model endpoint failed or returned something the agent cannot use."""


@dataclass
class Reply:
    content: str
    tool_calls: list[dict]                 # [{"id", "name", "arguments": raw JSON string}]
    reasoning: str | None = None
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None


class ChatModel(Protocol):
    def describe(self) -> dict: ...

    def complete(self, messages: list[dict], tools: list[dict]) -> Reply: ...


def _local(host: str) -> bool:
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_base_url(url: str) -> str:
    """HTTPS for remote endpoints; plain HTTP only for a model served on this machine (vLLM, Ollama…)."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or '').lower()
    if not host or parts.scheme not in ('https', 'http') or (parts.scheme == 'http' and not _local(host)):
        raise ValueError(f'URP_LLM_BASE_URL 须为 https:// 地址（本机模型可用 http://127.0.0.1）：{url}')
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('URP_LLM_BASE_URL 不能包含账号、查询参数或片段；密钥请放在 URP_LLM_API_KEY')
    return url.strip().rstrip('/')


class OpenAICompatibleModel:
    def __init__(self, base_url: str, model: str, api_key: str, *, temperature: float = 0.2,
                 timeout_s: float = 120.0, max_tokens: int | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = check_base_url(base_url)
        self.model, self.temperature, self.timeout_s, self.max_tokens = model, temperature, timeout_s, max_tokens
        self._key = api_key
        self._transport = transport

    def describe(self) -> dict:
        return {'provider': 'openai-compatible', 'endpoint': urlsplit(self.base_url).hostname, 'model': self.model,
                'temperature': self.temperature, 'max_tokens': self.max_tokens, 'timeout_s': self.timeout_s}

    def complete(self, messages: list[dict], tools: list[dict]) -> Reply:
        from . import llm

        options = {'temperature': self.temperature, 'reasoning_field': 'reasoning_content'}
        if self.max_tokens is not None:
            options['max_tokens'] = self.max_tokens
        provider = llm.Provider(id='openai-compatible', kind='openai', model=self.model,
                                base_url=self.base_url, options=options, timeout_s=self.timeout_s)
        return _complete(provider, messages, tools, api_key=self._key, transport=self._transport)


def model_from_env(env: Mapping[str, str] | None = None) -> tuple[OpenAICompatibleModel | None, str | None]:
    """(model, None) when configured; (None, reason) otherwise. Reads only URP_LLM_* and ZHIPU_API_KEY."""
    env = os.environ if env is None else env
    key = next((env[k].strip() for k in KEY_VARS if (env.get(k) or '').strip()), '')
    if not key:
        return None, '未配置大模型：' + SETUP_HINT
    try:
        temperature = float(env.get('URP_LLM_TEMPERATURE') or 0.2)
        timeout_s = float(env.get('URP_LLM_TIMEOUT_S') or 120)
        max_tokens = int(env['URP_LLM_MAX_TOKENS']) if env.get('URP_LLM_MAX_TOKENS') else None
        if not 0 <= temperature <= 2 or not 5 <= timeout_s <= 900 or (max_tokens is not None and max_tokens < 256):
            raise ValueError
    except ValueError:
        return None, 'URP_LLM_TEMPERATURE 须在 0–2，URP_LLM_TIMEOUT_S 须在 5–900 秒，URP_LLM_MAX_TOKENS 须 ≥ 256'
    try:
        model = OpenAICompatibleModel(env.get('URP_LLM_BASE_URL') or DEFAULT_BASE_URL,
                                      (env.get('URP_LLM_MODEL') or DEFAULT_MODEL).strip(), key,
                                      temperature=temperature, timeout_s=timeout_s, max_tokens=max_tokens)
    except ValueError as exc:
        return None, str(exc)
    return model, None


def _internal_tools(tools: list[dict]) -> list[dict]:
    """Research tools are OpenAI definitions ({'type': 'function', 'function': {...}}); llm.chat wraps plain ones itself."""
    return [t['function'] if t.get('type') == 'function' and isinstance(t.get('function'), dict) else t for t in tools or []]


def _internal_messages(messages: list[dict]) -> list[dict]:
    """The research history keeps tool calls as OpenAI sends them; llm.chat takes {'id', 'name', 'arguments': dict}."""
    from .llm import _parse_arguments

    out = []
    for message in messages:
        if message.get('reasoning_content') and not message.get('reasoning'):
            message = {**message, 'reasoning': message['reasoning_content']}
        if message.get('role') == 'assistant' and message.get('tool_calls'):
            calls = []
            for call in message['tool_calls']:
                fn = call['function'] if isinstance(call.get('function'), dict) else call
                # Arguments that were not a JSON object already came back to the model as a tool error.
                arguments, _ = _parse_arguments(fn.get('arguments'))
                calls.append({'id': call['id'], 'name': fn['name'], 'arguments': arguments})
            message = {**message, 'tool_calls': calls}
        out.append(message)
    return out


class ConnectedModel:
    """Research dialogue uses the same selected provider and private key store as task conversations."""

    def __init__(self, provider):
        self.provider = provider

    def describe(self) -> dict:
        p = self.provider
        return {'provider': p.id, 'endpoint': urlsplit(p.base_url).hostname, 'model': p.model,
                'temperature': p.options.get('temperature'), 'timeout_s': p.timeout_s}

    def complete(self, messages: list[dict], tools: list[dict]) -> Reply:
        return _complete(self.provider, messages, tools)


def _complete(provider, messages: list[dict], tools: list[dict], **kwargs) -> Reply:
    from . import llm

    try:
        response = llm.chat(provider, _internal_messages(messages), _internal_tools(tools), **kwargs)
    except llm.LLMError as exc:
        raise ModelError(str(exc)) from None
    calls = [{'id': c['id'], 'name': c['name'],
              'arguments': '{' if c.get('parse_error') else json.dumps(c['arguments'], ensure_ascii=False)}
             for c in response.tool_calls]
    usage = {'prompt_tokens': response.usage.get('input_tokens'),
             'completion_tokens': response.usage.get('output_tokens'),
             'total_tokens': response.usage.get('total_tokens'),
             'cached_tokens': response.usage.get('cached_tokens', 0)}
    return Reply(response.content, calls, response.reasoning, usage, response.finish_reason)


def configured_model():
    """Inspect local configuration only; no model call or provider verification happens here."""
    from . import llm, providers

    try:
        provider = providers.resolve(providers.selected())
    except llm.LLMError as exc:
        return None, str(exc)
    if provider is not None:
        return ConnectedModel(provider), None
    return model_from_env()
