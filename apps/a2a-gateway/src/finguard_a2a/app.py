import secrets
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from finguard_a2a.bootstrap import Services, build_services
from finguard_a2a.config import Settings
from finguard_a2a.domain.agents import AgentStatus, Reason
from finguard_a2a.ports.remote_agents import RemoteError


class _Wire(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SkillView(_Wire):
    id: str
    name: str
    description: str
    tags: list[str]
    examples: list[str]
    input_modes: list[str]
    output_modes: list[str]


class AgentView(_Wire):
    id: str
    name: str
    usable: bool
    reason: Reason | None
    card_digest: str
    link_hosts: list[str]
    skills: list[SkillView]


class AgentsView(_Wire):
    agents: list[AgentView]


class MessageIn(_Wire):
    skill_id: str
    message_id: str
    text: str
    data: dict[str, object] | None = None
    context_id: str | None = None


class ReplyView(_Wire):
    state: Literal["COMPLETED", "INPUT_REQUIRED", "AUTH_REQUIRED", "FAILED", "REJECTED", "CANCELED"]
    task_id: str | None
    context_id: str | None
    parts: list[dict[str, object]]
    # `file` parts the agent sent, which are not passed on (spec §4.3).
    dropped_parts: int
    latency_ms: int


class _RefusedError(Exception):
    def __init__(self, status: int, code: str, reason: str | None = None) -> None:
        self.status, self.code, self.reason = status, code, reason


_ERROR_STATUS = {"reply_invalid": 502, "reply_too_large": 502, "agent_error": 502, "timeout": 504}


def _view(status: AgentStatus) -> AgentView:
    card = status.card
    skills = (
        [
            SkillView(
                id=s.id,
                name=s.name,
                description=s.description,
                tags=list(s.tags),
                examples=list(s.examples),
                input_modes=list(s.input_modes),
                output_modes=list(s.output_modes),
            )
            for s in card.skills
        ]
        if card is not None and status.usable
        else []
    )
    return AgentView(
        id=status.agent.id,
        name=(card.name if card is not None and card.name else status.agent.id),
        usable=status.usable,
        reason=status.reason,
        card_digest=status.card_digest,
        link_hosts=list(status.link_hosts),
        skills=skills,
    )


def build_app(
    settings: Settings | None = None,
    *,
    http: httpx.AsyncClient | None = None,
    environ: Mapping[str, str] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    owned = http is None
    shared = http or httpx.AsyncClient(timeout=settings.timeout_seconds)
    services = build_services(settings, shared, environ)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await services.remote.aclose()
        if owned:
            await shared.aclose()

    app = FastAPI(title="FinGuard A2A gateway", lifespan=lifespan)
    app.state.services = services

    @app.exception_handler(_RefusedError)
    async def refuse(_request: Request, error: _RefusedError) -> JSONResponse:
        return JSONResponse({"code": error.code, "reason": error.reason}, status_code=error.status)

    def authorised(request: Request) -> None:
        given = request.headers.get("authorization", "")
        expected = f"Bearer {services.token}"
        if not secrets.compare_digest(given.encode(), expected.encode()):
            raise _RefusedError(401, "unauthorised")

    gated = Annotated[None, Depends(authorised)]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    async def version() -> dict[str, str]:
        return {"version": settings.service_version}

    @app.get("/v1/agents", response_model=AgentsView, response_model_by_alias=True)
    async def agents(_: gated) -> AgentsView:
        return AgentsView(agents=[_view(s) for s in await services.directory.statuses()])

    @app.post("/v1/agents/{agent_id}/messages", response_model=ReplyView)
    async def send(agent_id: str, body: MessageIn, _: gated) -> ReplyView:
        return await _send(services, agent_id, body)

    return app


async def _send(services: Services, agent_id: str, body: MessageIn) -> ReplyView:
    status = await services.directory.status(agent_id)
    if status is None:
        raise _RefusedError(404, "agent_unknown")
    if not status.usable or status.card is None:
        raise _RefusedError(409, "agent_unusable", status.reason)
    if body.skill_id not in {s.id for s in status.card.skills}:
        raise _RefusedError(422, "skill_unknown")
    started = time.monotonic()
    with services.tracer.start_as_current_span("a2a.send") as span:
        span.set_attribute("agent.id", agent_id)
        span.set_attribute("skill.id", body.skill_id)
        try:
            reply = await services.remote.send(
                status,
                skill_id=body.skill_id,
                message_id=body.message_id,
                text=body.text,
                data=body.data,
                context_id=body.context_id,
            )
        except RemoteError as error:
            span.set_attribute("error.code", error.code)
            span.set_attribute("latency_ms", int((time.monotonic() - started) * 1000))
            raise _RefusedError(_ERROR_STATUS[error.code], error.code) from error
        latency = int((time.monotonic() - started) * 1000)
        span.set_attribute("state", reply.state)
        span.set_attribute("dropped_parts", reply.dropped_parts)
        span.set_attribute("latency_ms", latency)
    return ReplyView.model_validate(
        {
            "state": reply.state,
            "task_id": reply.task_id,
            "context_id": reply.context_id,
            "parts": list(reply.parts),
            "dropped_parts": reply.dropped_parts,
            "latency_ms": latency,
        }
    )
