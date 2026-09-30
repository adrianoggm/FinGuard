from datetime import date

import httpx
from a2a.client import A2ACardResolver
from a2a.utils.proto_utils import validate_proto_required_fields

from finguard_simulated_gym.app import build_app
from finguard_simulated_gym.config import Settings


def _settings(style: str) -> Settings:
    # Wednesday 30 September 2026: tomorrow, Thursday 1 October, carries the demo anchor.
    return Settings(
        style=style,  # type: ignore[arg-type]
        a2a_url=f"http://gym-{style}:8000",
        public_url="http://127.0.0.1:9101",
        api_key="k",
        bearer_token="b",
        today=date(2026, 9, 30),
    )


async def test_each_style_serves_a_valid_a2a_1_0_card() -> None:
    for style, name, scheme in (
        ("norte", "Gimnasio Norte", "api_key_security_scheme"),
        ("sur", "Gimnasio Sur", "http_auth_security_scheme"),
    ):
        app = build_app(_settings(style))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=f"http://gym-{style}:8000"
        ) as http:
            card = await A2ACardResolver(http, f"http://gym-{style}:8000").get_agent_card()
        validate_proto_required_fields(card)  # raises on a missing required field
        assert card.name == name
        assert {i.protocol_version for i in card.supported_interfaces} == {"1.0"}
        assert {i.protocol_binding for i in card.supported_interfaces} == {"JSONRPC", "HTTP+JSON"}
        assert len(card.skills) == 2
        [only] = card.security_schemes.values()
        assert only.WhichOneof("scheme") == scheme
        assert card.provider.url == "http://127.0.0.1:9101"


async def test_the_two_styles_describe_their_skills_differently() -> None:
    ids = set()
    for style in ("norte", "sur"):
        ids |= {s.id for s in build_app(_settings(style)).state.card.skills}
    assert ids == {"timetable", "reserve", "classes", "book"}


async def test_legacy_serves_a_0_3_card_and_nothing_else() -> None:
    app = build_app(_settings("legacy"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gym-legacy:8000"
    ) as http:
        card = (await http.get("/.well-known/agent-card.json")).json()
        assert card["protocolVersion"] == "0.3.0"
        assert card["preferredTransport"] == "JSONRPC"
        assert card["url"] == "http://gym-legacy:8000/a2a"
        assert "supportedInterfaces" not in card
        assert (await http.get("/health")).json() == {"status": "ok"}
        assert (await http.post("/a2a/jsonrpc", json={})).status_code == 404
        assert (await http.post("/a2a", json={})).status_code in (404, 405)
