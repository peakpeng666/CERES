"""Model services a person can connect with nothing but an API key.

The catalog follows models.dev, the open registry opencode reads as well. A trimmed snapshot ships with the code, so the
list works offline; ``refresh`` replaces it with a cached copy of the current registry. Only services reachable through an
OpenAI-compatible or Anthropic endpoint with a single key qualify, and only models that can call tools, because the agent
works through tools. Local runtimes, cloud-account logins and subscription plans limited to coding tools are left out.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger('urp_lab.agent')

SOURCE = os.environ.get('URP_MODELS_URL') or 'https://models.dev/api.json'
SNAPSHOT = Path(__file__).resolve().parent / 'data' / 'model-catalog.json'
CONFIG_DIR = Path(os.environ.get('URP_CONFIG_DIR') or '~/.config/urp-workbench').expanduser()
CACHE = CONFIG_DIR / 'model-catalog.json'
MAX_AGE_S = 24 * 3600
SCHEMA = 'urp.model-catalog/1'

# Reached through a dedicated SDK upstream, so the registry names no endpoint for them.
NATIVE_BASE = {'openai': 'https://api.openai.com/v1', 'anthropic': 'https://api.anthropic.com',
               'google': 'https://generativelanguage.googleapis.com/v1beta/openai', 'xai': 'https://api.x.ai/v1',
               'groq': 'https://api.groq.com/openai/v1', 'mistral': 'https://api.mistral.ai/v1',
               'togetherai': 'https://api.together.xyz/v1', 'deepinfra': 'https://api.deepinfra.com/v1/openai',
               'cerebras': 'https://api.cerebras.ai/v1', 'cohere': 'https://api.cohere.ai/compatibility/v1'}
LEFT_OUT = re.compile(r'coding-plan|code-plan|token-plan|step-plan|copilot|gitlab|lmstudio|ollama|bedrock|vertex|azure|sap-ai'
                      r'|watsonx|cloudflare|^opencode|^vercel$|^oci$|snowflake|databricks|^neon$')
OPENAI_SDKS = {'@ai-sdk/openai-compatible', '@ai-sdk/openai', '@openrouter/ai-sdk-provider'}

FEATURED = ('deepseek', 'zhipuai', 'moonshotai-cn', 'alibaba-cn', 'volcengine', 'siliconflow-cn', 'minimax-cn', 'stepfun',
            'openrouter', 'openai', 'anthropic', 'google', 'xai', 'mistral', 'groq')
NAMES = {'deepseek': 'DeepSeek', 'zhipuai': '智谱 GLM', 'moonshotai-cn': 'Kimi', 'alibaba-cn': '通义千问', 'volcengine': '豆包（火山方舟）',
         'siliconflow-cn': '硅基流动', 'minimax-cn': 'MiniMax', 'stepfun': '阶跃星辰', 'google': 'Google Gemini'}
KEY_PAGES = {'deepseek': 'https://platform.deepseek.com/api_keys', 'zhipuai': 'https://open.bigmodel.cn/usercenter/apikeys',
             'moonshotai-cn': 'https://platform.moonshot.cn/console/api-keys', 'alibaba-cn': 'https://bailian.console.aliyun.com/?tab=model#/api-key',
             'volcengine': 'https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey', 'siliconflow-cn': 'https://cloud.siliconflow.cn/account/ak',
             'minimax-cn': 'https://platform.minimaxi.com/user-center/basic-information/interface-key', 'stepfun': 'https://platform.stepfun.com/interface-key',
             'openrouter': 'https://openrouter.ai/settings/keys', 'openai': 'https://platform.openai.com/api-keys',
             'anthropic': 'https://console.anthropic.com/settings/keys', 'google': 'https://aistudio.google.com/apikey', 'xai': 'https://console.x.ai',
             'mistral': 'https://console.mistral.ai/api-keys', 'groq': 'https://console.groq.com/keys'}


def _model(m: dict) -> dict | None:
    if not m.get('tool_call') or m.get('status') == 'deprecated' or m.get('provider'):
        return None
    if 'text' not in ((m.get('modalities') or {}).get('output') or ['text']):
        return None
    cost, limit = m.get('cost') or {}, m.get('limit') or {}
    interleaved = m.get('interleaved')
    return {'id': m['id'], 'name': m.get('name') or m['id'], 'context': limit.get('context'), 'output': limit.get('output'),
            'reasoning': bool(m.get('reasoning')), 'temperature': m.get('temperature', True) is not False, 'released': m.get('release_date'),
            'input_price': cost.get('input'), 'output_price': cost.get('output'), 'beta': m.get('status') in ('alpha', 'beta') or None,
            'reasoning_field': interleaved.get('field') if isinstance(interleaved, dict) else None}


def _service(sid: str, p: dict) -> dict | None:
    npm, api = p.get('npm'), p.get('api') or NATIVE_BASE.get(sid)
    if LEFT_OUT.search(sid) or not api or '$' in api or not p.get('env') or re.search(r'//(localhost|127\.|0\.0\.0\.0|\[::1\])', api):
        return None
    if npm == '@ai-sdk/anthropic':
        kind, api = 'anthropic', re.sub(r'/v1/?$', '', api)
    elif npm in OPENAI_SDKS or sid in NATIVE_BASE:
        kind = 'openai'
    else:
        return None
    models = [m for m in map(_model, (p.get('models') or {}).values()) if m]
    if not models:
        return None
    models.sort(key=lambda m: (m['released'] or '', m['id']), reverse=True)
    return {'id': sid, 'name': NAMES.get(sid) or p.get('name') or sid, 'kind': kind, 'base_url': api.rstrip('/'), 'env': list(p['env']),
            'doc': p.get('doc'), 'key_page': KEY_PAGES.get(sid), 'models': models}


def trim(raw: dict, updated_utc: str, source: str) -> dict:
    services = {sid: entry for sid, entry in ((k, _service(k, v)) for k, v in raw.items()) if entry}
    return {'schema': SCHEMA, 'source': source, 'updated_utc': updated_utc, 'services': services}


_lock = threading.Lock()
_loaded: tuple[float, dict] | None = None
_refreshing = threading.Event()


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('schema') == SCHEMA and data.get('services') else None


def load() -> dict:
    """The newest catalog available: a refreshed copy when one exists, otherwise the shipped snapshot."""
    global _loaded
    stamp = CACHE.stat().st_mtime if CACHE.is_file() else 0.
    with _lock:
        if _loaded and _loaded[0] == stamp:
            return _loaded[1]
        data = _read(CACHE) or _read(SNAPSHOT) or {'schema': SCHEMA, 'source': 'none', 'updated_utc': None, 'services': {}}
        _loaded = (stamp, data)
        return data


def services() -> dict[str, dict]:
    return load()['services']


def rank(sid: str) -> tuple:
    return (FEATURED.index(sid) if sid in FEATURED else len(FEATURED), sid)


def refresh(timeout: float = 20.0) -> dict:
    """Fetch the registry and keep the trimmed result next to the user's configuration."""
    response = httpx.get(SOURCE, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    data = trim(response.json(), time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), SOURCE)
    if not data['services']:
        raise ValueError('模型目录为空')
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(CACHE)
    return load()


def refresh_in_background():
    """At most one attempt at a time and one fetch a day. A failed fetch leaves the current catalog in place."""
    if os.environ.get('URP_MODELS_REFRESH') != '1' or _refreshing.is_set():
        return
    if CACHE.is_file() and time.time() - CACHE.stat().st_mtime < MAX_AGE_S:
        return
    _refreshing.set()

    def run():
        try:
            refresh()
        except (httpx.HTTPError, OSError, ValueError) as exc:
            log.info('model catalog refresh skipped: %s', type(exc).__name__)
        finally:
            _refreshing.clear()
    threading.Thread(target=run, name='model-catalog', daemon=True).start()


if __name__ == '__main__':
    # Regenerate the shipped snapshot: python -m urp_lab.agent.catalog
    raw = httpx.get(SOURCE, timeout=60, follow_redirects=True).raise_for_status().json()
    out = trim(raw, time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), SOURCE)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f"{len(out['services'])} services, {sum(len(s['models']) for s in out['services'].values())} models, {SNAPSHOT.stat().st_size // 1024} KB")
