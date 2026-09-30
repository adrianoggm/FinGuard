import httpx
from a2a.types import AgentCard
from fakes import (
    Recorder,
    agent_app,
    api_key_security,
    bearer_security,
    make_card,
    oauth_security,
    reply_with,
    skill,
)
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from finguard_a2a.adapters.outbound.env_registry import EnvRegistry
from finguard_a2a.application.directory import Directory
from finguard_a2a.domain.agents import REASONS, AgentStatus

CARD_PATH = "/.well-known/agent-card.json"


def _raw(body: object, status: int = 200) -> FastAPI:
    app = FastAPI()

    @app.get(CARD_PATH)
    def card() -> JSONResponse:
        return JSONResponse(body, status_code=status)

    return app


def _served(card: AgentCard) -> FastAPI:
    return agent_app(card, Recorder(), reply_with())


async def _status(
    app: FastAPI | None,
    credential: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    url: str = "http://agent:8000",
) -> AgentStatus:
    assert app is not None or transport is not None
    http = httpx.AsyncClient(transport=transport or httpx.ASGITransport(app=app))  # type: ignore[arg-type]
    directory = Directory(EnvRegistry(url), http, lambda _id: credential, 300)
    (status,) = await directory.statuses()
    return status


async def test_card_unreachable_on_404() -> None:
    status = await _status(_raw({"detail": "no"}, 404))
    assert (status.usable, status.reason, status.card) == (False, "card_unreachable", None)


async def test_card_unreachable_on_a_connection_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    status = await _status(None, transport=httpx.MockTransport(refuse))
    assert status.reason == "card_unreachable"


async def test_card_invalid_when_required_fields_are_missing() -> None:
    assert (await _status(_raw({"foo": 1}))).reason == "card_invalid"


async def test_card_invalid_when_the_card_is_a_json_list() -> None:
    assert (await _status(_raw([1, 2]))).reason == "card_invalid"


async def test_card_invalid_when_a_field_has_the_wrong_type() -> None:
    assert (await _status(_raw({"name": "x", "skills": "x"}))).reason == "card_invalid"


async def test_version_unsupported_for_a_legacy_03_card() -> None:
    legacy = {
        "name": "Old",
        "description": "A 0.3 card.",
        "version": "1",
        "url": "http://agent:8000/rpc",
        "preferredTransport": "JSONRPC",
        "protocolVersion": "0.3.0",
        "capabilities": {},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{"id": "s", "name": "S", "description": "d", "tags": ["t"]}],
    }
    assert (await _status(_raw(legacy))).reason == "version_unsupported"


async def test_binding_unsupported_when_only_grpc() -> None:
    status = await _status(_served(make_card(bindings=("GRPC",))))
    assert status.reason == "binding_unsupported"


async def test_no_skills() -> None:
    assert (await _status(_served(make_card(skills=[])))).reason == "no_skills"


async def test_auth_unsupported_for_oauth() -> None:
    status = await _status(_served(make_card(security=oauth_security())), "secret")
    assert status.reason == "auth_unsupported"


async def test_auth_unsupported_for_an_api_key_outside_the_headers() -> None:
    card = make_card(security=api_key_security(location="query"))
    assert (await _status(_served(card), "secret")).reason == "auth_unsupported"


async def test_credential_missing_when_the_environment_has_none() -> None:
    status = await _status(_served(make_card(security=api_key_security())), None)
    assert (status.usable, status.reason) == (False, "credential_missing")


async def test_a_usable_card_with_both_bindings_and_its_credential_header() -> None:
    status = await _status(_served(make_card(security=api_key_security("X-Api-Key"))), "k1")
    assert (status.usable, status.reason) == (True, None)
    assert status.credential_header == ("X-Api-Key", "k1")
    assert status.card_digest.startswith("sha256:")


async def test_a_bearer_scheme_becomes_an_authorization_header() -> None:
    status = await _status(_served(make_card(security=bearer_security())), "t1")
    assert status.credential_header == ("Authorization", "Bearer t1")


async def test_a_card_without_security_needs_no_credential() -> None:
    status = await _status(_served(make_card(skills=[skill()])))
    assert (status.usable, status.credential_header) == (True, None)


async def test_every_reason_is_covered_by_a_test_here() -> None:
    covered = {
        "card_unreachable",
        "card_invalid",
        "version_unsupported",
        "binding_unsupported",
        "no_skills",
        "auth_unsupported",
        "credential_missing",
    }
    assert set(REASONS) == covered


async def test_link_hosts_are_the_registered_host_and_the_providers() -> None:
    card = make_card(url="http://gym-norte:8000", provider_url="http://127.0.0.1:9101")
    status = await _status(_served(card), url="http://Gym-Norte:8000")
    assert status.link_hosts == ("gym-norte:8000", "127.0.0.1:9101")


async def test_a_provider_url_that_is_not_http_contributes_nothing() -> None:
    card = make_card(provider_url="ftp://elsewhere")
    status = await _status(_served(card))
    assert status.link_hosts == ("agent:8000",)


async def test_the_card_is_refreshed_after_its_ttl_and_an_agent_can_become_unusable() -> None:
    online = {"up": True}
    app = _served(make_card())
    asgi = httpx.ASGITransport(app=app)

    async def handler(request: httpx.Request) -> httpx.Response:
        if not online["up"]:
            return httpx.Response(404)
        return await asgi.handle_async_request(request)

    now = {"t": 0.0}
    directory = Directory(
        EnvRegistry("http://agent:8000"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        lambda _id: None,
        300,
        clock=lambda: now["t"],
    )
    assert (await directory.statuses())[0].usable is True
    online["up"] = False
    now["t"] = 299
    assert (await directory.statuses())[0].usable is True  # still cached
    now["t"] = 301
    (status,) = await directory.statuses()
    assert (status.usable, status.reason) == (False, "card_unreachable")
    assert await directory.status("nobody") is None
