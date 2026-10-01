import httpx
from gym_client import auth_headers, settings_for

from finguard_simulated_gym.app import build_app


def _client(style: str, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app(settings_for(style))),
        base_url=f"http://gym-{style}:8000",
        headers=headers or {},
    )


async def test_the_a2a_routes_refuse_a_missing_or_wrong_credential() -> None:
    async with _client("norte") as http:
        assert (await http.post("/a2a/jsonrpc", json={})).status_code == 401
        assert (await http.get("/.well-known/agent-card.json")).status_code == 200
    async with _client("norte", {"X-Gym-Key": "wrong"}) as http:
        assert (await http.post("/a2a/jsonrpc", json={})).status_code == 401
    async with _client("sur", {"Authorization": "Bearer wrong"}) as http:
        assert (await http.post("/a2a/jsonrpc", json={})).status_code == 401
        assert (await http.get("/.well-known/agent-card.json")).status_code == 200
    async with _client("sur") as http:
        assert (await http.post("/a2a/rest/message:send", json={})).status_code == 401


async def test_the_right_credential_reaches_the_a2a_handler() -> None:
    for style in ("norte", "sur"):
        async with _client(style, auth_headers(style)) as http:
            assert (await http.post("/a2a/jsonrpc", json={})).status_code != 401
