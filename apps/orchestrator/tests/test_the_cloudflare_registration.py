"""Cloudflare Workers AI, against a fake transport. No token, no network, no bill.

What this file holds is a **registration** and not an adapter. Cloudflare speaks the OpenAI
chat-completions protocol, so the entire vendor is two URLs built from an account id, and the
first test says so out loud: if somebody later adds a `CloudflareLlmProvider` class, that test
is the one that should make them explain what the native envelope bought them.

The second thing it holds is the one accommodation that registration needed. Cloudflare's
`/ai/v1` prefix serves `chat/completions` and `embeddings` and nothing else, so the OpenAI
convention `ping` defaults to would report a perfectly healthy account as down for ever. That
was measured against the live API — a routing error needs no credential to provoke — and it is
asserted here so it cannot quietly decay back into a convention nobody rechecked.

What is deliberately **not** tested is whether a JSON Schema constrains the decoder on that
endpoint. Nothing here can know: it needs a token and somebody's account. The module docstring
of the code under test says why that is the first thing to find out and what it costs if the
answer is no. The schema test below asserts what is *sent*, which is the part that is ours.

Neither is whether Cloudflare is any good at reading a turn. That is
`scripts/evaluate_interpreting.py --provider cloudflare`, and it needs a token and somebody's
money.
"""

from typing import Any

import pytest

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.infrastructure.llm import openai_compatible_provider
from finguard_orchestrator.infrastructure.llm.cloudflare_workers_ai import (
    CLOUDFLARE,
    cloudflare_workers_ai,
)
from finguard_orchestrator.infrastructure.llm.openai_compatible_provider import (
    OpenAiCompatibleProvider,
)

TOKEN = "not-a-real-cloudflare-token"
# Obviously not anybody's. A real Cloudflare account id is a 32-character hex string and it
# identifies whose account is billed, so the one this was set up against lives in `.env` and
# never in a versioned file — not even a test fixture, where it would read as a default.
ACCOUNT = "00000000000000000000000000000000"
API = "https://api.example/client/v4"
MODEL = "@cf/qwen/qwen3-30b-a3b-fp8"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"intent": {"type": "string"}},
    "required": ["intent"],
    "additionalProperties": False,
}
AN_ANSWER: dict[str, Any] = {"choices": [{"message": {"content": '{"intent":"COURTESY"}'}}]}


class _Response:
    def __init__(self, payload: Any, status: int) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise openai_compatible_provider.httpx.HTTPStatusError(  # type: ignore[call-arg]
                "boom", request=None, response=self
            )

    def json(self) -> Any:
        return self._payload


class Endpoint:
    """What the adapter talked to: the requests it made, and what it was answered."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.body: Any = AN_ANSWER
        self.status = 200

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.requests[index]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Endpoint:
    """Records every request the adapter makes, and answers it locally.

    A copy of the Groq tests' fake rather than a shared one, so that each vendor's contract is
    asserted against a transport it owns. Sharing it would mean a change made to satisfy one
    registration could quietly change what the other one is proving.
    """
    endpoint = Endpoint()

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            endpoint.requests.append({"timeout": kwargs.get("timeout")})

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def post(self, url: str, *, json: Any, headers: Any) -> _Response:
            endpoint.requests[-1] |= {"url": url, "json": json, "headers": headers}
            return _Response(endpoint.body, endpoint.status)

        async def get(self, url: str, *, headers: Any) -> _Response:
            endpoint.requests[-1] |= {"url": url, "headers": headers}
            return _Response(endpoint.body, endpoint.status)

    monkeypatch.setattr(openai_compatible_provider.httpx, "AsyncClient", FakeClient)
    return endpoint


def a_provider(*, api_base: str = API, reasoning_headroom: int = 0) -> OpenAiCompatibleProvider:
    return cloudflare_workers_ai(
        api_token=TOKEN,
        account_id=ACCOUNT,
        api_base=api_base,
        timeout_seconds=7.0,
        reasoning_headroom=reasoning_headroom,
    )


# --- what it is --------------------------------------------------------------------------


def test_it_fulfils_the_port() -> None:
    assert isinstance(a_provider(), LlmProviderPort)


def test_cloudflare_needed_no_adapter_of_its_own() -> None:
    """The result of the whole exercise, asserted so it is a decision and not an accident.

    Cloudflare offers a native surface — the model in the URL, the answer inside a
    `{"success", "result", "errors"}` envelope — and an OpenAI-compatible one. Taking the
    second means the vendor is a base URL, which is what `OpenAiCompatibleProvider` was
    written for. A class appearing here later has to justify the envelope reader and the
    second place for the redaction rule to drift.
    """
    assert type(a_provider()) is OpenAiCompatibleProvider
    assert a_provider().name == CLOUDFLARE == "cloudflare"


# --- the request --------------------------------------------------------------------------


async def test_inference_goes_to_the_openai_compatible_surface(sent: Endpoint) -> None:
    """`/ai/v1/chat/completions` and not `/ai/run/{model}`. The account id is a path segment,
    which is the reason it is configuration rather than a header nobody would notice missing."""
    await a_provider().complete("hola", model=MODEL, temperature=0.0, max_output_tokens=32)

    assert sent[0]["url"] == f"{API}/accounts/{ACCOUNT}/ai/v1/chat/completions"
    assert sent[0]["json"]["model"] == MODEL


async def test_the_token_travels_as_a_bearer_token(sent: Endpoint) -> None:
    await a_provider().complete("hola", model=MODEL, temperature=0.0, max_output_tokens=32)

    assert sent[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


async def test_a_trailing_slash_on_the_api_root_does_not_double(sent: Endpoint) -> None:
    """The API root is configuration and somebody will paste one with a slash on the end. Here
    it matters more than usual: the slash lands in the middle of the URL, not at the end, so a
    doubled one is `/client/v4//accounts/...` and a 404 that names nothing useful."""
    await a_provider(api_base=f"{API}/").complete(
        "hola", model=MODEL, temperature=0.0, max_output_tokens=32
    )

    assert sent[0]["url"] == f"{API}/accounts/{ACCOUNT}/ai/v1/chat/completions"


async def test_a_schema_is_sent_as_strict_constrained_decoding(sent: Endpoint) -> None:
    """What we send, which is all this can honestly assert.

    Cloudflare documents JSON mode three incompatible ways and separately claims compatibility
    with the OpenAI SDK's `response_format`. This sends the OpenAI shape on the strength of that
    claim. Whether the endpoint *honours* it — whether the decoder is actually constrained, or
    the schema is a suggestion the model may ignore, which Cloudflare's own page warns of —
    needs a live token. This test pins the guess so that changing it is deliberate.
    """
    await a_provider().complete(
        "hola", model=MODEL, temperature=0.0, max_output_tokens=32, schema=SCHEMA
    )

    fmt = sent[0]["json"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"] == SCHEMA
    assert fmt["json_schema"]["strict"] is True


async def test_a_reasoning_model_gets_room_to_think(sent: Endpoint) -> None:
    """The registration passes its configured reasoning floor to the shared adapter."""
    await a_provider(reasoning_headroom=2048).complete(
        "hola", model=MODEL, temperature=0.0, max_output_tokens=32
    )

    assert sent[0]["json"]["max_tokens"] == 2048


# --- readiness ------------------------------------------------------------------------------


async def test_readiness_avoids_the_models_route_that_cloudflare_does_not_serve(
    sent: Endpoint,
) -> None:
    """The accommodation, and the only thing the OpenAI-compatible surface could not supply.

    A GET of `.../ai/v1/models` answers `405 GET not supported for requested URI` — measured
    against the live API, which returns a routing error to anyone. Left on the default, `ping`
    would report every correctly configured account as permanently down, and the planning path
    would refuse to start against a provider that works.
    """
    assert await a_provider().ping() is True

    assert "/ai/v1/models" not in sent[0]["url"]
    assert sent[0]["url"].startswith(f"{API}/accounts/{ACCOUNT}/ai/models/search")


async def test_readiness_proves_the_account_and_not_only_the_token(sent: Endpoint) -> None:
    """The account id is in the readiness URL, so a token that is valid for somebody else's
    account fails the check. Without that, a mistyped account id is invisible until the first
    question a person asks."""
    await a_provider().ping()

    assert ACCOUNT in sent[0]["url"]
    assert sent[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


# --- what a failure looks like ---------------------------------------------------------------


async def test_an_http_error_does_not_carry_the_body(sent: Endpoint) -> None:
    """The rule does not stop at our own logger, and it does not stop at a new vendor either.
    Cloudflare's error envelope quotes the request, and the request is somebody's bank
    question."""
    sent.body = {"errors": [{"message": "the sentence was: ¿cuánto gasté?"}], "success": False}
    sent.status = 400

    with pytest.raises(ProviderFailedError) as raised:
        await a_provider().complete("x", model=MODEL, temperature=0.0, max_output_tokens=8)

    assert "cuánto gasté" not in str(raised.value)
    assert "400" in str(raised.value)


# --- when it exists at all ---------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "])
@pytest.mark.parametrize("blank", ["cloudflare_api_token", "cloudflare_account_id"])
def test_a_blank_half_is_no_provider(
    blank: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Either half blank has to read as absent, because the composition root registers this only
    when both are present and `""` is what a half-filled `.env` line produces.

    The account id needs the treatment as much as the token and fails one line lower: not a
    rejected credential but `/accounts//ai/v1`, a 404 that reads like an outage.

    Both variables are set on every run, one of them blank, so the assertion is about the
    validator and not about whatever the machine's own `.env` happens to hold.
    """
    from finguard_orchestrator.bootstrap.config import Settings

    filled = {"cloudflare_api_token": TOKEN, "cloudflare_account_id": ACCOUNT} | {blank: value}
    for field, setting in filled.items():
        monkeypatch.setenv(f"ORCHESTRATOR_{field.upper()}", setting)
    settings = Settings()  # type: ignore[call-arg]

    assert getattr(settings, blank) is None


def test_both_halves_survive_and_the_token_still_does_not_print(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from finguard_orchestrator.bootstrap.config import Settings

    monkeypatch.setenv("ORCHESTRATOR_CLOUDFLARE_API_TOKEN", TOKEN)
    monkeypatch.setenv("ORCHESTRATOR_CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    settings = Settings()  # type: ignore[call-arg]

    assert settings.cloudflare_api_token is not None
    assert settings.cloudflare_api_token.get_secret_value() == TOKEN
    assert settings.cloudflare_account_id == ACCOUNT
    # The property the SecretStr is for. The account id is not masked and should not be — it is
    # not a credential, and a `/version` dump that cannot say which account is configured is a
    # dump nobody can debug from.
    assert TOKEN not in repr(settings)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ({}, False),
        ({"cloudflare_api_token": TOKEN}, False),
        ({"cloudflare_account_id": ACCOUNT}, False),
        ({"cloudflare_api_token": "", "cloudflare_account_id": ACCOUNT}, False),
        ({"cloudflare_api_token": TOKEN, "cloudflare_account_id": ACCOUNT}, True),
    ],
    ids=["neither", "token only", "account only", "blank token", "both"],
)
async def test_the_provider_exists_only_when_both_halves_are_configured(
    configured: dict[str, str], expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule, asserted through the composition root rather than through the settings that
    feed it, because "a provider nobody configured must not be buildable" is a property of the
    wiring and the settings are only half of it.

    `available_providers` is what /admin reports and what the catalogue is validated against, so
    a provider that appears here appears to an operator and can be named in `config/llm.toml`.
    One that does not cannot be reached at all, which is the point: an unconfigured Cloudflare
    must be an unknown provider, not a provider that 404s every question.
    """
    from finguard_orchestrator.application.queries.get_llm_catalogue import GetLlmCatalogueQuery
    from finguard_orchestrator.bootstrap.config import get_settings
    from finguard_orchestrator.bootstrap.main import create_app

    for field in ("cloudflare_api_token", "cloudflare_account_id"):
        # Set to empty rather than deleted, which is the difference between a test that states the
        # deployment and one that inherits the machine's. `delenv` removes the *process* variable
        # and pydantic then falls through to the `.env` file — so on the first machine that had
        # real Cloudflare credentials, "neither configured" quietly became "both", and three cases
        # here failed for a reason that had nothing to do with the code under test.
        #
        # Empty works because blank counts as absent, which is a rule this service enforces
        # deliberately: a provider with a blank credential 401s every request and reports it as a
        # model outage. Asserting through it is asserting the rule twice, which is free.
        monkeypatch.setenv(f"ORCHESTRATOR_{field.upper()}", configured.get(field, ""))
    # The settings object is built once and cached, which is right for a service and wrong for
    # a test that is about what a different deployment would build. Cleared again afterwards so
    # the next test does not inherit this one's environment through the cache.
    get_settings.cache_clear()
    try:
        app = create_app()
        catalogue = await app.state.query_bus.ask(GetLlmCatalogueQuery())
    finally:
        get_settings.cache_clear()

    assert (CLOUDFLARE in catalogue.available_providers) is expected


def test_the_account_id_has_no_default_and_the_endpoint_does() -> None:
    """The endpoint is the vendor's, so it is a default and a deployment that never uses this
    does not have to state it. The account id is not the vendor's — it is somebody's, and it is
    what gets billed — so it has no default and never will.

    Asked of the class rather than of an instance on purpose: an instance also reads `.env`, and
    a developer who has configured Cloudflare for real would then fail a test about what the
    code ships with.
    """
    from finguard_orchestrator.bootstrap.config import Settings

    defaults = Settings.model_fields

    assert defaults["cloudflare_api_base"].default == "https://api.cloudflare.com/client/v4"
    assert defaults["cloudflare_account_id"].default is None
    assert defaults["cloudflare_api_token"].default is None
    # Measured on a planning exclusion: 2048 produced reasoning only and finish_reason `length`;
    # 4096 completed the constrained JSON plan. This is deliberately not Groq's smaller floor.
    assert defaults["cloudflare_reasoning_headroom"].default == 4096
    assert (
        defaults["cloudflare_reasoning_headroom"].default
        > defaults["groq_reasoning_headroom"].default
    )
