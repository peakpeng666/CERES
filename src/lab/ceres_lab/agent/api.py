"""/lab/api/agent — task agent endpoints (mounted by the gateway). Responses never contain credentials."""
from __future__ import annotations

import threading
from typing import Any

import httpx
from fastapi import APIRouter, Body, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..services import ServiceError
from . import capabilities, catalog, providers
from .runner import AgentRunner, RunnerError

router = APIRouter()
_LOCK = threading.Lock()


def runner(request: Request) -> AgentRunner:
    lab = request.app.state.lab
    with _LOCK:
        if getattr(lab, 'agent', None) is None:
            lab.agent = AgentRunner(lab)
        return lab.agent


def _body(payload: Any) -> dict:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ServiceError(400, 'invalid_body', '请求体应为 JSON 对象')
    return payload


def _call(fn, *args):
    try:
        return fn(*args)
    except RunnerError as exc:
        raise ServiceError(exc.status, exc.code, exc.message) from None


def _fresh(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={'Cache-Control': 'no-store'})


@router.get('/agent/providers')
async def provider_list(request: Request):
    return _fresh(await run_in_threadpool(runner(request).providers))


def _provider_call(fn, *args):
    try:
        return _fresh(fn(*args))
    except providers.ProviderError as exc:
        raise ServiceError(exc.status, exc.code, exc.message) from None


@router.get('/agent/providers/{service_id}/models')
def provider_models(service_id: str):
    return _provider_call(providers.models_of, service_id)


@router.post('/agent/providers/refresh')
def provider_refresh():
    try:
        meta = catalog.refresh()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise ServiceError(502, 'catalog_unavailable', f'暂时无法更新模型目录：{type(exc).__name__}') from None
    return _fresh({'updated_utc': meta['updated_utc'], 'services': len(meta['services'])})


@router.post('/agent/providers/custom')
def provider_custom(payload: Any = Body(None)):
    return _provider_call(providers.add_custom, _body(payload))


@router.post('/agent/providers/{service_id}/connect')
def provider_connect(service_id: str, payload: Any = Body(None)):
    return _provider_call(providers.connect, service_id, _body(payload).get('api_key'))


@router.post('/agent/providers/{service_id}/disconnect')
def provider_disconnect(service_id: str):
    return _provider_call(providers.remove_custom if service_id.startswith('custom-') else providers.disconnect, service_id)


@router.post('/agent/model')
def choose_model(payload: Any = Body(None)):
    return _provider_call(lambda: {'selected': providers.select(str(_body(payload).get('model') or ''))})


@router.get('/agent/harness')
def harness(request: Request):
    return _fresh(runner(request).harness())


@router.get('/agent/capabilities')
def capabilities_list(request: Request):
    return _fresh(capabilities.capabilities(request.app.state.lab))


@router.get('/agent/jobs')
def jobs_list(request: Request):
    return _fresh(capabilities.jobs(request.app.state.lab))


@router.get('/agent/evals')
def evals(request: Request):
    return _fresh(runner(request).evals())


@router.get('/agent/sessions')
def sessions(request: Request):
    return _fresh({'sessions': runner(request).list()})


@router.post('/agent/sessions', status_code=201)
def create(request: Request, payload: Any = Body(None)):
    return _fresh(_call(runner(request).create, _body(payload)), 201)


@router.get('/agent/sessions/{session_id}')
def get(request: Request, session_id: str):
    return _fresh(_call(runner(request).get, session_id))


@router.patch('/agent/sessions/{session_id}')
def patch_session(request: Request, session_id: str, payload: Any = Body(None)):
    return _fresh(_call(runner(request).patch_session, session_id, _body(payload)))


@router.get('/agent/sessions/{session_id}/export')
def export(request: Request, session_id: str):
    data = _call(runner(request).export, session_id)
    return JSONResponse(data, headers={'Cache-Control': 'no-store',
                                       'Content-Disposition': f'attachment; filename="{session_id}.json"'})


@router.post('/agent/sessions/{session_id}/messages', status_code=202)
def message(request: Request, session_id: str, payload: Any = Body(None)):
    return _fresh(_call(runner(request).message, session_id, str(_body(payload).get('text') or '')), 202)


@router.post('/agent/sessions/{session_id}/control')
def control(request: Request, session_id: str, payload: Any = Body(None)):
    body = _body(payload)
    return _fresh(_call(runner(request).control, session_id, str(body.get('action') or ''), body))


@router.patch('/agent/sessions/{session_id}/contract')
def contract(request: Request, session_id: str, payload: Any = Body(None)):
    fields = _body(payload).get('fields')
    if not isinstance(fields, dict):
        raise ServiceError(400, 'invalid_body', 'fields 须为对象')
    return _fresh(_call(runner(request).edit_contract, session_id, fields))
