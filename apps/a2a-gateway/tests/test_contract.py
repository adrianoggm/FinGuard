import httpx
from fakes import (
    AUTH,
    Recorder,
    data_agent,
    gateway_for,
    reply_bare_message,
    reply_with,
    text_agent,
)
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict

ENV = {"A2A_GATEWAY_CREDENTIAL_GYM_NORTE": "k1", "A2A_GATEWAY_CREDENTIAL_GYM_SUR": "t1"}
BODY = {"skillId": "timetable", "messageId": "m-1", "text": "Free on Monday?", "data": {"day": 1}}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def _both() -> tuple[Recorder, Recorder, httpx.AsyncClient]:
    norte, sur = Recorder(), Recorder()
    app = gateway_for({"gym-norte": data_agent(norte), "gym-sur": text_agent(sur)}, ENV)
    return norte, sur, _client(app)


async def test_the_listing_shows_usable_agents_with_their_skills_and_hides_the_card() -> None:
    _, _, client = _both()
    response = await client.get("/v1/agents", headers=AUTH)
    assert response.status_code == 200
    agents = response.json()["agents"]
    assert [(a["id"], a["usable"], a["reason"]) for a in agents] == [
        ("gym-norte", True, None),
        ("gym-sur", True, None),
    ]
    assert agents[0]["skills"][0] == {
        "id": "timetable",
        "name": "Timetable",
        "description": "The timetable skill.",
        "tags": ["sports", "booking"],
        "examples": ["What is free on Monday?"],
        "inputModes": [],
        "outputModes": [],
    }
    assert agents[0]["cardDigest"].startswith("sha256:")
    assert agents[0]["linkHosts"] == ["gym-norte:8000"]
    for forbidden in ('"card"', "securitySchemes", '"url"', "k1", "t1"):
        assert forbidden not in response.text


async def test_every_route_but_health_needs_the_gateway_token() -> None:
    _, _, client = _both()
    for headers in ({}, {"Authorization": "Bearer nope"}):
        assert (await client.get("/v1/agents", headers=headers)).status_code == 401
        post = await client.post("/v1/agents/gym-norte/messages", json=BODY, headers=headers)
        assert (post.status_code, post.json()["code"]) == (401, "unauthorised")
    assert (await client.get("/health")).status_code == 200


async def test_a_message_reaches_the_agent_with_its_credential_in_a_header_only() -> None:
    norte, _, client = _both()
    response = await client.post("/v1/agents/gym-norte/messages", json=BODY, headers=AUTH)
    assert response.status_code == 200, response.text
    assert norte.headers[-1]["x-api-key"] == "k1"
    assert "k1" not in str(MessageToDict(norte.messages[-1]))


async def test_a_bearer_credential_travels_as_authorization() -> None:
    _, sur, client = _both()
    response = await client.post("/v1/agents/gym-sur/messages", json=BODY, headers=AUTH)
    assert response.status_code == 200, response.text
    assert sur.headers[-1]["authorization"] == "Bearer t1"


async def test_the_skill_id_travels_as_a_metadata_hint() -> None:
    norte, _, client = _both()
    await client.post("/v1/agents/gym-norte/messages", json=BODY, headers=AUTH)
    message = norte.messages[-1]
    assert message.metadata["finguardSkill"] == "timetable"
    assert message.message_id == "m-1"


async def test_a_text_only_agent_receives_no_data_part() -> None:
    norte, sur, client = _both()
    await client.post("/v1/agents/gym-sur/messages", json=BODY, headers=AUTH)
    await client.post("/v1/agents/gym-norte/messages", json=BODY, headers=AUTH)
    assert [p.WhichOneof("content") for p in sur.messages[-1].parts] == ["text"]
    assert [p.WhichOneof("content") for p in norte.messages[-1].parts] == ["text", "data"]


async def test_integral_numbers_come_back_as_integers() -> None:
    _, _, client = _both()
    response = await client.post("/v1/agents/gym-norte/messages", json=BODY, headers=AUTH)
    body = response.json()
    assert body["parts"][0] == {"kind": "data", "data": {"huecos": 4, "precio": 7.5}}
    assert isinstance(body["parts"][0]["data"]["huecos"], int)
    assert body["state"] == "COMPLETED"
    assert body["taskId"] and body["contextId"]
    assert isinstance(body["latencyMs"], int)


async def test_states_are_reported_without_their_prefix() -> None:
    for state in ("COMPLETED", "INPUT_REQUIRED", "AUTH_REQUIRED", "FAILED", "REJECTED"):
        app = gateway_for({"gym-norte": data_agent(Recorder(), script=reply_with(state))}, ENV)
        response = await _client(app).post("/v1/agents/gym-norte/messages", json=BODY, headers=AUTH)
        assert response.json()["state"] == state, response.text


async def test_a_bare_message_reply_is_completed_without_a_task() -> None:
    app = gateway_for({"gym-norte": data_agent(Recorder(), script=reply_bare_message)}, ENV)
    response = await _client(app).post("/v1/agents/gym-norte/messages", json=BODY, headers=AUTH)
    body = response.json()
    assert (body["state"], body["taskId"]) == ("COMPLETED", None)
    assert body["parts"] == [{"kind": "text", "text": "Just a message."}]


async def test_parts_come_from_artifacts_then_the_status_message() -> None:
    script = reply_with(
        artifact=[{"text": "first"}, {"raw": 1}, {"data": {"n": 2}}, {"url": "http://x/y"}],
        status_text="last",
    )
    app = gateway_for({"gym-norte": data_agent(Recorder(), script=script)}, ENV)
    response = await _client(app).post("/v1/agents/gym-norte/messages", json=BODY, headers=AUTH)
    assert response.json()["parts"] == [
        {"kind": "text", "text": "first"},
        {"kind": "data", "data": {"n": 2}},
        {"kind": "text", "text": "last"},
    ]


async def test_unknown_unusable_and_unknown_skill_are_refused_before_sending() -> None:
    norte, sur = Recorder(), Recorder()
    # gym-sur has no credential in this environment, so it is unusable.
    app = gateway_for(
        {"gym-norte": data_agent(norte), "gym-sur": text_agent(sur)},
        {"A2A_GATEWAY_CREDENTIAL_GYM_NORTE": "k1"},
    )
    client = _client(app)
    unknown = await client.post("/v1/agents/nobody/messages", json=BODY, headers=AUTH)
    assert (unknown.status_code, unknown.json()["code"]) == (404, "agent_unknown")
    unusable = await client.post("/v1/agents/gym-sur/messages", json=BODY, headers=AUTH)
    assert (unusable.status_code, unusable.json()) == (
        409,
        {"code": "agent_unusable", "reason": "credential_missing"},
    )
    skill = await client.post(
        "/v1/agents/gym-norte/messages", json={**BODY, "skillId": "nope"}, headers=AUTH
    )
    assert (skill.status_code, skill.json()["code"]) == (422, "skill_unknown")
    assert norte.headers == [] and sur.headers == []
    listing = (await client.get("/v1/agents", headers=AUTH)).json()["agents"]
    assert listing[1]["usable"] is False and listing[1]["skills"] == []
