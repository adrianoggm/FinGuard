"""The xAI adapter, against a fake transport. No key, no network, no bill.

Two things are asserted and they are the two that break silently. **The shape of the request**,
because an adapter that sends a subtly wrong payload gets a 200 and an answer to a different
question — and the field that matters most here is `response_format`, since constrained decoding is
what the whole planning pipeline rests on and a schema sent as a suggestion looks identical until
the day a model ignores it. **The shape of a failure**, because reading a malformed response
optimistically raises `TypeError` out of a component whose entire contract is `ProviderFailedError`.

What is deliberately not tested is whether Grok is any good at this. That is
`scripts/evaluate_interpreting.py --provider xai`, and it needs a key and somebody's money.
"""

from typing import Any

import pytest

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.infrastructure.llm import xai_llm_provider
from finguard_orchestrator.infrastructure.llm.xai_llm_provider import XaiLlmProvider

KEY = "xai-not-a-real-key"
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
            raise xai_llm_provider.httpx.HTTPStatusError(  # type: ignore[call-arg]
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

    Patched at `AsyncClient` rather than injected, because the adapter opening its own client per
    request is the shape under test — an adapter that took one would be a different adapter.
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

    monkeypatch.setattr(xai_llm_provider.httpx, "AsyncClient", FakeClient)
    return endpoint


def a_provider() -> XaiLlmProvider:
    return XaiLlmProvider(api_key=KEY, base_url="https://api.example/v1/", timeout_seconds=7.0)


# --- the contract ------------------------------------------------------------------------


def test_it_fulfils_the_port() -> None:
    """Structural, so a signature that drifts from the port fails here rather than on the first
    question a deployment asks."""
    assert isinstance(a_provider(), LlmProviderPort)


def test_the_provider_is_named_after_the_vendor_not_the_model() -> None:
    """`grok-4` and `grok-3-mini` are two models of one provider, which is the catalogue's own
    rule. Naming the provider `grok` would name it after one of the things it serves."""
    assert XaiLlmProvider.name == "xai"


# --- the request --------------------------------------------------------------------------


async def test_the_key_travels_as_a_bearer_token(sent: Endpoint) -> None:
    await a_provider().complete("hola", model="grok-x", temperature=0.0, max_output_tokens=32)

    assert sent[0]["headers"]["Authorization"] == f"Bearer {KEY}"


async def test_the_trailing_slash_does_not_double(sent: Endpoint) -> None:
    """The base URL is configuration and somebody will paste one with a slash on the end."""
    await a_provider().complete("hola", model="grok-x", temperature=0.0, max_output_tokens=32)

    assert sent[0]["url"] == "https://api.example/v1/chat/completions"


async def test_a_schema_is_sent_as_strict_constrained_decoding(
    sent: Endpoint,
) -> None:
    """The assertion this file exists for.

    Without `strict`, a JSON schema on an OpenAI-compatible endpoint is a prompt-level suggestion:
    the answer usually matches, the caller parses it, and nothing looks wrong until a model
    improvises a field. With it the decoder cannot emit a token that breaks the shape, which is the
    property the plan verifier and the payment reader are built on top of.
    """
    await a_provider().complete(
        "hola", model="grok-x", temperature=0.0, max_output_tokens=32, schema=SCHEMA
    )

    fmt = sent[0]["json"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == SCHEMA


async def test_no_schema_means_no_response_format(sent: Endpoint) -> None:
    """A free chat turn has no shape to hold it to, and sending an empty constraint would be
    asking the endpoint to reject something nobody asked for."""
    await a_provider().complete("hola", model="grok-x", temperature=0.0, max_output_tokens=32)

    assert "response_format" not in sent[0]["json"]


async def test_the_sampling_arguments_reach_the_wire(sent: Endpoint) -> None:
    """`max_tokens` and not `num_predict`: this API is OpenAI-shaped and Ollama's is not, and a
    budget under the wrong name is silently ignored — a 32-token classification becomes whatever
    the endpoint's default is."""
    await a_provider().complete("hola", model="grok-x", temperature=0.25, max_output_tokens=99)

    assert sent[0]["json"]["temperature"] == 0.25
    assert sent[0]["json"]["max_tokens"] == 99
    assert sent[0]["json"]["model"] == "grok-x"


async def test_the_ping_authenticates_rather_than_only_connecting(
    sent: Endpoint,
) -> None:
    """A socket that opens says nothing about a key that is missing, wrong, or out of credit —
    which is the failure readiness is for. `/models` answers all three."""
    assert await a_provider().ping() is True
    assert sent[0]["url"] == "https://api.example/v1/models"
    assert sent[0]["headers"]["Authorization"] == f"Bearer {KEY}"


# --- what a failure looks like -------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"choices": []}, "no choices"),
        ({"error": {"message": "insufficient credits"}}, "an error with a 200"),
        ({"choices": [{"message": {"content": None}}]}, "a refusal returns null content"),
        ({"choices": [{"message": {"content": "   "}}]}, "whitespace is not an answer"),
        ([1, 2, 3], "an array parses fine and then has no .get"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_malformed_body_is_a_provider_failure(body: Any, why: str) -> None:
    with pytest.raises(ProviderFailedError):
        xai_llm_provider._answer_in(body, "xai")


def test_a_good_body_returns_its_content() -> None:
    assert xai_llm_provider._answer_in(AN_ANSWER, "xai") == '{"intent":"COURTESY"}'


async def test_an_http_error_does_not_carry_the_body(sent: Endpoint) -> None:
    """The body of a 400 quotes the request, and the request contains the user's sentence. The
    rule that nothing a person typed reaches a log file does not stop at our own logger."""
    sent.body = {"error": {"message": "the sentence was: ¿cuánto gasté?"}}
    sent.status = 400

    with pytest.raises(ProviderFailedError) as raised:
        await a_provider().complete("x", model="m", temperature=0.0, max_output_tokens=8)

    assert "cuánto gasté" not in str(raised.value)
    assert "400" in str(raised.value)


# --- when it exists at all -----------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_key_means_no_provider(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ORCHESTRATOR_XAI_API_KEY=` is what a half-filled .env line produces, and what compose
    passes when a `${VAR:-}` interpolation finds nothing.

    Taken literally it registers a provider whose every request is a 401 — the exact failure the
    "no key, no provider" rule exists to prevent, arriving by the one route the rule does not
    cover, and reported as a model outage rather than as a missing credential.
    """
    from finguard_orchestrator.bootstrap.config import Settings

    monkeypatch.setenv("ORCHESTRATOR_XAI_API_KEY", value)

    assert Settings().xai_api_key is None  # type: ignore[call-arg]


def test_a_real_key_survives_and_still_does_not_print(monkeypatch: pytest.MonkeyPatch) -> None:
    from finguard_orchestrator.bootstrap.config import Settings

    monkeypatch.setenv("ORCHESTRATOR_XAI_API_KEY", KEY)
    settings = Settings()  # type: ignore[call-arg]

    assert settings.xai_api_key is not None
    assert settings.xai_api_key.get_secret_value() == KEY
    # The property the SecretStr is for: a log line, a repr or a dump of the settings object
    # prints the mask and not the credential.
    assert KEY not in repr(settings)
