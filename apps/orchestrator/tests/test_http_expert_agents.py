"""The gateway's internal contract (spec §4.3), read from this side.

The gateway is a separate service with its own tests; this is the orchestrator's half of the
contract, stated by hand like the banking clients: camelCase on the wire, domain types past here,
and every refusal a closed code rather than a status to match on.
"""

import json

import httpx2 as httpx
import pytest

from finguard_orchestrator.domain.models.expert_agents import (
    AgentSkill,
    DataReplyPart,
    ExpertAgent,
    ExpertAgentsError,
    Outbound,
    TextReplyPart,
)
from finguard_orchestrator.infrastructure.expert_agents.http_expert_agents import HttpExpertAgents

BASE = "http://a2a-gateway.invalid"
TOKEN = "gateway-test-token"

LISTING = {
    "agents": [
        {
            "id": "gym-norte",
            "name": "Gimnasio Norte",
            "usable": True,
            "reason": None,
            "cardDigest": "sha256:abc",
            "linkHosts": ["gym-norte:8000", "127.0.0.1:9101"],
            "skills": [
                {
                    "id": "timetable",
                    "name": "Horario de clases",
                    "description": "Consulta las clases disponibles.",
                    "tags": ["clases", "horario"],
                    "examples": ["¿Qué clases de yoga hay el martes?"],
                    "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["application/json"],
                }
            ],
        },
        {
            "id": "gym-antiguo",
            "name": "gym-antiguo",
            "usable": False,
            "reason": "version_unsupported",
            "cardDigest": "sha256:def",
            "linkHosts": ["gym-antiguo:8000"],
            "skills": [],
        },
    ]
}

REPLY = {
    "state": "COMPLETED",
    "taskId": "t-1",
    "contextId": "c-1",
    "parts": [
        {"kind": "text", "text": "Spinning a las 19:00"},
        {"kind": "data", "data": {"sesiones": [{"hora_inicio": "19:00", "huecos": 4}]}},
    ],
    "latencyMs": 812,
}

OUTBOUND = Outbound(
    text="Busco clases de spinning el jueves 1 de octubre de 2026 entre las 15:00 y las 21:00.",
    data={"activity": "spinning", "memberRef": "m1"},
)


def _gateway(monkeypatch: pytest.MonkeyPatch, handler) -> HttpExpertAgents:  # type: ignore[no-untyped-def]
    """The adapter with its transport replaced, the way the banking clients are tested: the client
    is built inside the call, so the class is swapped here, keeping every argument it was given."""
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(**{**kw, "transport": httpx.MockTransport(handler)}),
    )
    return HttpExpertAgents(base_url=BASE + "/", token=TOKEN, timeout_seconds=1.0)


async def _send(gateway: HttpExpertAgents, agent_id: str = "gym-norte"):  # type: ignore[no-untyped-def]
    return await gateway.send(
        agent_id, skill_id="timetable", message_id="m-1", outbound=OUTBOUND, context_id=None
    )


async def test_the_listing_becomes_expert_agents_and_carries_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=LISTING)

    agents = await _gateway(monkeypatch, handler).agents()

    assert [(r.method, str(r.url)) for r in seen] == [("GET", f"{BASE}/v1/agents")]
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert agents == (
        ExpertAgent(
            id="gym-norte",
            name="Gimnasio Norte",
            usable=True,
            reason=None,
            card_digest="sha256:abc",
            link_hosts=("gym-norte:8000", "127.0.0.1:9101"),
            skills=(
                AgentSkill(
                    id="timetable",
                    name="Horario de clases",
                    description="Consulta las clases disponibles.",
                    tags=("clases", "horario"),
                    examples=("¿Qué clases de yoga hay el martes?",),
                ),
            ),
        ),
        ExpertAgent(
            id="gym-antiguo",
            name="gym-antiguo",
            usable=False,
            reason="version_unsupported",
            card_digest="sha256:def",
            link_hosts=("gym-antiguo:8000",),
            skills=(),
        ),
    )


async def test_a_message_is_posted_in_the_contract_and_its_parts_come_back_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=REPLY)

    gateway = _gateway(monkeypatch, handler)
    reply = await gateway.send(
        "gym-norte", skill_id="timetable", message_id="m-1", outbound=OUTBOUND, context_id="c-0"
    )

    (request,) = seen
    assert (request.method, str(request.url)) == ("POST", f"{BASE}/v1/agents/gym-norte/messages")
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == {
        "skillId": "timetable",
        "messageId": "m-1",
        "text": OUTBOUND.text,
        "data": {"activity": "spinning", "memberRef": "m1"},
        "contextId": "c-0",
    }
    assert reply.state == "COMPLETED"
    assert (reply.task_id, reply.context_id, reply.latency_ms) == ("t-1", "c-1", 812)
    assert reply.parts == (
        TextReplyPart("Spinning a las 19:00"),
        DataReplyPart({"sesiones": [{"hora_inicio": "19:00", "huecos": 4}]}),
    )


async def test_a_bare_message_reply_has_no_task_or_context(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {**REPLY, "taskId": None, "contextId": None}
    reply = await _send(_gateway(monkeypatch, lambda _: httpx.Response(200, json=body)))
    assert (reply.task_id, reply.context_id) == (None, None)


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (404, "agent_unknown", None),
        (409, "agent_unusable", "credential_missing"),
        (422, "skill_unknown", None),
        (502, "reply_invalid", None),
        (502, "reply_too_large", None),
        (502, "agent_error", None),
        (504, "timeout", None),
    ],
)
async def test_each_gateway_refusal_is_its_own_code(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str, reason: str | None
) -> None:
    body = {"code": code, "reason": reason}
    gateway = _gateway(monkeypatch, lambda _: httpx.Response(status, json=body))
    with pytest.raises(ExpertAgentsError) as raised:
        await _send(gateway)
    assert (raised.value.code, raised.value.reason) == (code, reason)


async def test_a_wrong_token_is_unauthorised(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _gateway(monkeypatch, lambda _: httpx.Response(401, json={"code": "unauthorised"}))
    with pytest.raises(ExpertAgentsError) as listing:
        await gateway.agents()
    with pytest.raises(ExpertAgentsError) as sending:
        await _send(gateway)
    assert listing.value.code == sending.value.code == "unauthorised"


async def test_a_gateway_that_cannot_be_reached_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    gateway = _gateway(monkeypatch, handler)
    with pytest.raises(ExpertAgentsError) as listing:
        await gateway.agents()
    with pytest.raises(ExpertAgentsError) as sending:
        await _send(gateway)
    assert listing.value.code == sending.value.code == "unreachable"


@pytest.mark.parametrize(
    "body",
    [
        {k: v for k, v in REPLY.items() if k != "latencyMs"},
        {**REPLY, "state": "WORKING"},
        {**REPLY, "parts": [{"kind": "file", "uri": "https://x"}]},
        {**REPLY, "parts": [{"kind": "text", "text": 7}]},
        [REPLY],
    ],
    ids=["missing-field", "unknown-state", "unknown-part", "wrong-type", "not-an-object"],
)
async def test_a_reply_outside_the_contract_is_reply_invalid(
    monkeypatch: pytest.MonkeyPatch, body: object
) -> None:
    gateway = _gateway(monkeypatch, lambda _: httpx.Response(200, json=body))
    with pytest.raises(ExpertAgentsError) as raised:
        await _send(gateway)
    assert raised.value.code == "reply_invalid"


NORTE = LISTING["agents"][0]


@pytest.mark.parametrize(
    "content",
    [
        json.dumps({"agents": [{**NORTE, "cardDigest": None}]}),
        json.dumps({"agents": [{**NORTE, "id": "Gym Norte"}]}),
        json.dumps({"agents": [{**NORTE, "skills": [{"id": "timetable"}]}]}),
        json.dumps({"agent": []}),
        "not json",
    ],
    ids=["null-digest", "not-an-agent-id", "skill-missing-fields", "wrong-key", "not-json"],
)
async def test_a_listing_outside_the_contract_is_reply_invalid(
    monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    gateway = _gateway(monkeypatch, lambda _: httpx.Response(200, content=content.encode()))
    with pytest.raises(ExpertAgentsError) as raised:
        await gateway.agents()
    assert raised.value.code == "reply_invalid"


async def test_an_error_without_a_known_code_is_reply_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _gateway(monkeypatch, lambda _: httpx.Response(500, text="Internal Server Error"))
    with pytest.raises(ExpertAgentsError) as raised:
        await _send(gateway)
    assert raised.value.code == "reply_invalid"


async def test_an_agent_id_that_is_not_one_never_reaches_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=REPLY)

    with pytest.raises(ExpertAgentsError) as raised:
        await _send(_gateway(monkeypatch, handler), agent_id="../admin")
    assert raised.value.code == "agent_unknown" and seen == []


# --- the settings that build it ------------------------------------------------------------------


def test_the_capability_ships_off_and_a_blank_setting_is_no_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the URL the capability does not exist (spec §3.2), and a blank
    `ORCHESTRATOR_A2A_GATEWAY_URL=` -- what `.env.defaults` ships and what compose passes for an
    unset variable -- is that absence, not a URL with nothing in it. The two secrets get the same
    treatment as every other key."""
    from finguard_orchestrator.bootstrap.config import Settings

    fields = Settings.model_fields
    for name in ("a2a_gateway_url", "a2a_gateway_token", "a2a_pairwise_key"):
        assert fields[name].default is None
    for name in ("URL", "TOKEN"):
        monkeypatch.setenv(f"ORCHESTRATOR_A2A_GATEWAY_{name}", "")
    monkeypatch.setenv("ORCHESTRATOR_A2A_PAIRWISE_KEY", "  ")
    settings = Settings()  # type: ignore[call-arg]
    assert (settings.a2a_gateway_url, settings.a2a_gateway_token, settings.a2a_pairwise_key) == (
        None,
        None,
        None,
    )


def test_the_per_agent_budget_is_stated_in_the_defaults_and_nowhere_in_code() -> None:
    from pathlib import Path

    from finguard_orchestrator.bootstrap.config import Settings

    assert Settings.model_fields["a2a_agent_budget_seconds"].is_required()
    defaults = (Path(__file__).resolve().parents[1] / ".env.defaults").read_text(encoding="utf-8")
    assert "\nORCHESTRATOR_A2A_AGENT_BUDGET_SECONDS=15\n" in defaults
    assert "\nORCHESTRATOR_A2A_GATEWAY_URL=\n" in defaults
