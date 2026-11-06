"""/lab/api/agent endpoints (CONTRACT §9.1). Request bodies are typed, so they appear in the OpenAPI schema."""
from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description='用户消息（中文或英文）')


class AgentDecision(BaseModel):
    call_id: str = Field(min_length=1, max_length=120)
    approve: bool
    note: str | None = Field(default=None, max_length=500)


def install(app: FastAPI, lab, api: str = '/lab/api'):
    base = f'{api}/research/agent'

    @app.get(f'{api}/agent', include_in_schema=False)
    @app.get(base)
    def agent_status():
        return lab.research_agent.status()

    @app.get(f'{base}/sessions')
    def agent_sessions(limit: int = Query(50, ge=1, le=200)):
        return JSONResponse({'sessions': lab.research_agent.list(limit)}, headers={'Cache-Control': 'no-store'})

    @app.post(f'{base}/sessions', status_code=202)
    def agent_start(body: AgentMessage):
        return lab.research_agent.start(body.message)

    @app.get(f'{base}/sessions/{{session_id}}')
    def agent_session(session_id: str, messages: bool = False):
        return JSONResponse(lab.research_agent.get(session_id, messages), headers={'Cache-Control': 'no-store'})

    @app.post(f'{base}/sessions/{{session_id}}/messages', status_code=202)
    def agent_send(session_id: str, body: AgentMessage):
        return lab.research_agent.send(session_id, body.message)

    @app.post(f'{base}/sessions/{{session_id}}/approval', status_code=202)
    def agent_decide(session_id: str, body: AgentDecision):
        return lab.research_agent.decide(session_id, body.call_id, body.approve, body.note)

    @app.post(f'{base}/sessions/{{session_id}}/cancel')
    def agent_cancel(session_id: str):
        return lab.research_agent.cancel(session_id)
