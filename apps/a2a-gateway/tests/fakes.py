"""Minimal in-process A2A 1.0 agents built with the SDK's own server, and the wiring to reach them.

Nothing here imports another app: the agents only need to look like agents from outside.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import httpx
from a2a.helpers import new_data_part, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    APIKeySecurityScheme,
    ClientCredentialsOAuthFlow,
    HTTPAuthSecurityScheme,
    Message,
    OAuth2SecurityScheme,
    OAuthFlows,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

TOKEN = "gateway-test-token"
RPC_PATH = "/a2a/jsonrpc"


@dataclass
class Recorder:
    """What an agent saw: the headers of each POST and the message it carried."""

    headers: list[dict[str, str]] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)


def skill(
    skill_id: str = "timetable",
    *,
    input_modes: tuple[str, ...] = (),
    output_modes: tuple[str, ...] = (),
) -> AgentSkill:
    return AgentSkill(
        id=skill_id,
        name=skill_id.title(),
        description=f"The {skill_id} skill.",
        tags=["sports", "booking"],
        examples=["What is free on Monday?"],
        input_modes=list(input_modes),
        output_modes=list(output_modes),
    )


def api_key_security(name: str = "X-Api-Key", location: str = "header") -> dict[str, object]:
    return {
        "schemes": {
            "key": SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(location=location, name=name)
            )
        },
        "requirements": ["key"],
    }


def bearer_security() -> dict[str, object]:
    return {
        "schemes": {
            "tok": SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer"))
        },
        "requirements": ["tok"],
    }


def oauth_security() -> dict[str, object]:
    return {
        "schemes": {
            "oa": SecurityScheme(
                oauth2_security_scheme=OAuth2SecurityScheme(
                    flows=OAuthFlows(
                        client_credentials=ClientCredentialsOAuthFlow(
                            token_url="http://idp/token", scopes={"read": "read"}
                        )
                    )
                )
            )
        },
        "requirements": ["oa"],
    }


def make_card(
    *,
    url: str = "http://agent:8000",
    bindings: tuple[str, ...] = ("JSONRPC", "HTTP+JSON"),
    version: str = "1.0",
    skills: list[AgentSkill] | None = None,
    security: Mapping[str, object] | None = None,
    provider_url: str | None = None,
    input_modes: tuple[str, ...] = ("text/plain",),
) -> AgentCard:
    card = AgentCard(
        name="Fake agent",
        description="A fake agent for tests.",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(url=f"{url}{RPC_PATH}", protocol_binding=b, protocol_version=version)
            for b in bindings
        ],
        capabilities=AgentCapabilities(),
        default_input_modes=list(input_modes),
        default_output_modes=["text/plain", "application/json"],
        skills=[skill()] if skills is None else skills,
    )
    if provider_url is not None:
        card.provider.CopyFrom(AgentProvider(url=provider_url, organization="Fake"))
    if security is not None:
        schemes = security["schemes"]
        assert isinstance(schemes, dict)
        for name, scheme in schemes.items():
            card.security_schemes[name].CopyFrom(scheme)
        requirements = security["requirements"]
        assert isinstance(requirements, list)
        for name in requirements:
            card.security_requirements.append(
                SecurityRequirement(schemes={name: StringList(list=[])})
            )
    return card


Script = Callable[[RequestContext, EventQueue], Awaitable[None]]


class _Executor(AgentExecutor):
    def __init__(self, recorder: Recorder, script: Script) -> None:
        self._recorder = recorder
        self._script = script

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        self._recorder.messages.append(context.message)
        await self._script(context, event_queue)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


async def _updater(context: RequestContext, queue: EventQueue) -> TaskUpdater:
    assert context.message is not None
    task = new_task_from_user_message(context.message)
    await queue.enqueue_event(task)
    return TaskUpdater(queue, task.id, task.context_id)


def reply_with(
    state: str = "COMPLETED",
    *,
    artifact: list[dict[str, object]] | None = None,
    status_text: str | None = "Done.",
) -> Script:
    """A script ending in `state`; artifact parts are {"text": ..}, {"data": ..} or {"url": ..}."""

    async def script(context: RequestContext, queue: EventQueue) -> None:
        updater = await _updater(context, queue)
        if artifact:
            await updater.add_artifact([_part(p) for p in artifact])
        message = new_text_message(status_text) if status_text is not None else None
        terminal = {
            "COMPLETED": updater.complete,
            "INPUT_REQUIRED": updater.requires_input,
            "AUTH_REQUIRED": updater.requires_auth,
            "FAILED": updater.failed,
            "REJECTED": updater.reject,
        }[state]
        await terminal(message)

    return script


def _part(spec: dict[str, object]):  # type: ignore[no-untyped-def]
    from a2a.helpers import new_raw_part, new_url_part

    if "text" in spec:
        return new_text_part(str(spec["text"]))
    if "data" in spec:
        return new_data_part(spec["data"])
    if "url" in spec:
        return new_url_part(str(spec["url"]))
    return new_raw_part(b"\x00\x01")


async def reply_bare_message(context: RequestContext, queue: EventQueue) -> None:
    await queue.enqueue_event(new_text_message("Just a message."))


def raising(error: Exception) -> Script:
    async def script(context: RequestContext, queue: EventQueue) -> None:
        raise error

    return script


DATA_ARTIFACT: list[dict[str, object]] = [{"data": {"huecos": 4, "precio": 7.5}}]


def agent_app(
    card: AgentCard,
    recorder: Recorder,
    script: Script,
    *,
    required_header: tuple[str, str] | None = None,
) -> FastAPI:
    handler = DefaultRequestHandler(
        _Executor(recorder, script), task_store=InMemoryTaskStore(), agent_card=card
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=RPC_PATH),
    )

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]):  # type: ignore[no-untyped-def]
        if request.method == "POST":
            recorder.headers.append(dict(request.headers))
            if required_header is not None:
                name, value = required_header
                if request.headers.get(name) != value:
                    return JSONResponse({"error": "unauthorised"}, status_code=401)
        return await call_next(request)

    return app


def data_agent(
    recorder: Recorder,
    *,
    url: str = "http://gym-norte:8000",
    script: Script | None = None,
    credential: str = "k1",
) -> FastAPI:
    """Accepts JSON, needs `X-Api-Key`."""
    card = make_card(
        url=url,
        security=api_key_security(),
        input_modes=("text/plain", "application/json"),
    )
    return agent_app(
        card,
        recorder,
        script or reply_with(artifact=DATA_ARTIFACT),
        required_header=("x-api-key", credential),
    )


def text_agent(
    recorder: Recorder,
    *,
    url: str = "http://gym-sur:8000",
    script: Script | None = None,
    credential: str = "t1",
) -> FastAPI:
    """Takes text only, needs `Authorization: Bearer`."""
    card = make_card(url=url, security=bearer_security(), input_modes=("text/plain",))
    return agent_app(
        card,
        recorder,
        script or reply_with(artifact=[{"text": "Monday 18:00 is free."}]),
        required_header=("authorization", f"Bearer {credential}"),
    )


class HostRouter(httpx.AsyncBaseTransport):
    """Sends each request to the ASGI app registered for its host: a tiny network."""

    def __init__(self, apps: Mapping[str, FastAPI]) -> None:
        self._transports = {h: httpx.ASGITransport(app=a) for h, a in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._transports.get(request.url.host)
        if transport is None:
            raise httpx.ConnectError(f"no route to {request.url.host}", request=request)
        return await transport.handle_async_request(request)


def gateway_for(
    agents: Mapping[str, FastAPI],
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    **settings: object,
) -> FastAPI:
    """The gateway app, registered with `http://<id>:8000` for each agent and a fake network."""
    from finguard_a2a.app import build_app  # late: the card tests run without the app
    from finguard_a2a.config import Settings

    http = httpx.AsyncClient(transport=transport or HostRouter(agents))
    config = Settings(
        agents=",".join(f"http://{i}:8000" for i in agents),
        token=TOKEN,  # type: ignore[arg-type]
        **settings,  # type: ignore[arg-type]
    )
    return build_app(config, http=http, environ=env or {})


AUTH = {"Authorization": f"Bearer {TOKEN}"}
