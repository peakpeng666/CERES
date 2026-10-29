"""/lab/api/agent — task agent endpoints (mounted by the gateway). Responses never contain credentials."""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..services import ServiceError
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
async def providers(request: Request):
    return _fresh(await run_in_threadpool(runner(request).providers))


@router.get('/agent/sessions')
def sessions(request: Request):
    return _fresh({'sessions': runner(request).list()})


@router.post('/agent/sessions', status_code=201)
def create(request: Request, payload: Any = Body(None)):
    return _fresh(_call(runner(request).create, _body(payload)), 201)


@router.get('/agent/sessions/{session_id}')
def get(request: Request, session_id: str):
    return _fresh(_call(runner(request).get, session_id))


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
