"""Gemini's OpenAI-compatible registration and FinGuard's schema boundary."""

import asyncio
from typing import Any

import pytest

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.infrastructure.engine.operations_schema import (
    UnreadableError,
    read,
    schema_for,
)
from finguard_orchestrator.infrastructure.llm import openai_compatible_provider
from finguard_orchestrator.infrastructure.llm.counting_provider import counting_calls
from finguard_orchestrator.infrastructure.llm.gemini import (
    GEMINI,
    GeminiProvider,
    _shallow_operations_schema,
)

KEY = "AIza-test-key-not-real"
ANSWER = '{"operations":[{"smalltalk":"GREETING"}]}'


class _Response:
    def __init__(
        self,
        status_code: int = 200,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise openai_compatible_provider.httpx.HTTPStatusError(  # type: ignore[call-arg]
                "upstream failure", request=None, response=self
            )

    def json(self) -> Any:
        return self.body or {"choices": [{"message": {"content": ANSWER}}]}


class _Endpoint:
    request: dict[str, Any] | None = None
    responses: list[_Response] | None = None
    calls: int = 0
    starts: list[float] | None = None
    models: list[str] | None = None


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> _Endpoint:
    seen = _Endpoint()

    class FakeClient:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def post(self, url: str, *, json: Any, headers: Any) -> _Response:
            seen.request = {"url": url, "json": json, "headers": headers}
            seen.calls += 1
            if seen.starts is None:
                seen.starts = []
            seen.starts.append(asyncio.get_running_loop().time())
            if seen.models is None:
                seen.models = []
            seen.models.append(json["model"])
            return (seen.responses or [_Response()]).pop(0)

    monkeypatch.setattr(openai_compatible_provider.httpx, "AsyncClient", FakeClient)
    return seen


def test_gemini_is_an_openai_compatible_provider() -> None:
    provider = GeminiProvider(api_key=KEY)

    assert provider.name == GEMINI == "gemini"
    assert provider._base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert (
        provider._readiness_url == "https://generativelanguage.googleapis.com/v1beta/openai/models"
    )
    assert provider._min_request_interval_seconds == 12.0


async def test_gemini_spaces_concurrent_generation_requests(endpoint: _Endpoint) -> None:
    provider = GeminiProvider(api_key=KEY, min_request_interval_seconds=0.1)

    async def ask() -> str:
        return await provider.complete(
            "hello", model="gemini-test", temperature=0, max_output_tokens=12
        )

    await asyncio.gather(ask(), ask(), ask())

    assert endpoint.starts is not None
    assert len(endpoint.starts) == 3
    assert all(
        later - earlier >= 0.08
        for earlier, later in zip(endpoint.starts, endpoint.starts[1:], strict=False)
    ), endpoint.starts


async def test_gemini_fails_over_on_429_and_keeps_the_working_model(endpoint: _Endpoint) -> None:
    endpoint.responses = [_Response(429), _Response(), _Response()]
    provider = GeminiProvider(
        api_key=KEY,
        min_request_interval_seconds=0,
        fallback_models=("gemini-3.6-flash", "gemini-3.7-flash"),
    )

    for _ in range(2):
        await provider.complete(
            "hello", model="gemini-3.5-flash-lite", temperature=0, max_output_tokens=12
        )

    assert endpoint.models == [
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.6-flash",
    ]


async def test_gemini_does_not_fail_over_for_a_non_rate_limit_error(
    endpoint: _Endpoint,
) -> None:
    endpoint.responses = [_Response(400, body={"error": {"message": "invalid request"}})]
    provider = GeminiProvider(
        api_key=KEY,
        min_request_interval_seconds=0,
        fallback_models=("gemini-3.6-flash",),
    )

    with pytest.raises(ProviderFailedError, match="HTTP 400"):
        await provider.complete(
            "hello", model="gemini-3.5-flash-lite", temperature=0, max_output_tokens=12
        )

    assert endpoint.models == ["gemini-3.5-flash-lite"]


def test_operation_schema_is_compacted_but_keeps_closed_operation_tags() -> None:
    original = schema_for(["FOOD", "TRANSPORT"])

    compact = _shallow_operations_schema(original)
    operation_item = compact["properties"]["operations"]["items"]
    operation_variants = operation_item["anyOf"]
    operations = {
        next(iter(variant["properties"])): next(iter(variant["properties"].values()))
        for variant in operation_variants
    }
    names = set(operations)

    assert names == {
        "ask",
        "ask_correction",
        "patch",
        "compare",
        "compare_previous",
        "investigate_comparison",
        "measure",
        "select_operand",
        "reset",
        "restore_previous",
        "resume_focus",
        "balance",
        "list_contacts",
        "list_categories",
        "list_movements",
        "patch_movement_list",
        "smalltalk",
        "delegate",
        "create_visualization",
        "update_visualization",
        "edit_visualization",
        "prepare_bizum",
        "prepare_transfer",
        "prepare_own_transfer",
    }
    assert all(variant["additionalProperties"] is False for variant in operation_variants)
    assert all(len(variant["properties"]) == 1 for variant in operation_variants)
    assert "$defs" not in compact
    assert "anyOf" in original["properties"]["operations"]["items"]

    smalltalk = operations["smalltalk"]
    assert smalltalk["enum"] == [
        "GREETING",
        "THANKS",
        "IDENTITY",
        "HELP",
    ]

    bizum = operations["prepare_bizum"]
    assert bizum["properties"] == {
        "recipient": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "amount_minor": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    }
    assert bizum["required"] == ["recipient", "amount_minor"]
    assert bizum["additionalProperties"] is False

    transfer = operations["prepare_transfer"]
    assert transfer == bizum

    own_transfer = operations["prepare_own_transfer"]
    assert own_transfer == {"anyOf": [{"type": "string"}, {"type": "null"}]}

    chart_edit = operations["edit_visualization"]
    assert set(chart_edit["properties"]) == {
        "mark",
        "orientation",
        "sort",
        "limit",
        "show_all",
        "stack",
    }
    assert chart_edit["additionalProperties"] is False

    movement_list = operations["list_movements"]
    movement_fields = movement_list["properties"]
    assert set(movement_fields) == {
        "direction",
        "accounts",
        "categories",
        "merchant_terms",
        "window",
        "page_size",
        "continue_current",
    }
    assert movement_fields["direction"]["enum"] == ["EXPENSE", "INCOME", "ALL"]
    assert movement_fields["accounts"] == {"type": "array", "items": {"type": "string"}}
    assert movement_fields["merchant_terms"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    # Null keeps the shown list's page size.
    assert movement_fields["page_size"] == {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert movement_fields["continue_current"] == {"type": "boolean"}
    assert movement_list["additionalProperties"] is False

    patch = operations["patch"]
    assert patch["properties"]["target"]["enum"] == [
        "focus",
        "earlier",
        "latest",
        "pending",
        "suspended",
    ]
    change_variants = patch["properties"]["changes"]["items"]["anyOf"]
    change_fields = {
        next(iter(variant["properties"])): next(iter(variant["properties"].values()))
        for variant in change_variants
    }
    assert all(len(variant["properties"]) == 1 for variant in change_variants)
    assert change_fields["merchant_terms"]["type"] == "array"
    assert change_fields["window"]["properties"].keys() == {"named", "counted", "range"}
    assert change_fields["window"]["properties"]["named"]["enum"][-1] == "THIS_YEAR"
    assert change_fields["window"]["properties"]["counted"]["properties"]["period"]["enum"] == [
        "LAST_N_DAYS",
        "LAST_N_MONTHS",
    ]


def test_non_operation_schemas_pass_through_unchanged() -> None:
    schema = {"type": "object", "properties": {"intent": {"type": "string"}}}

    assert _shallow_operations_schema(schema) is schema


async def test_complete_sends_gemini_schema_and_bearer_key(
    endpoint: _Endpoint,
) -> None:
    provider = GeminiProvider(api_key=KEY)
    answer = await provider.complete(
        "Buenas tardes",
        model="gemini-3.5-flash-lite",
        temperature=0,
        max_output_tokens=128,
        schema=schema_for(["FOOD"]),
    )

    assert answer == ANSWER
    assert endpoint.request is not None
    assert endpoint.request["url"].endswith("/chat/completions")
    assert endpoint.request["headers"]["Authorization"] == f"Bearer {KEY}"
    # Flash models think, and the thinking counts against max_tokens: a budget sized for the
    # answer (128 here, 600 for the interpreter) truncated their JSON.
    assert endpoint.request["json"]["max_tokens"] == 4096
    sent_schema = endpoint.request["json"]["response_format"]["json_schema"]["schema"]
    assert "$defs" not in sent_schema
    operations = sent_schema["properties"]["operations"]["items"]["anyOf"]
    # Compacted, but every operation the schema defines is still offered.
    defined = schema_for(["FOOD"])["properties"]["operations"]["items"]["anyOf"]
    assert len(operations) == len(defined)
    patch = next(v["properties"]["patch"] for v in operations if "patch" in v["properties"])
    assert "changes" in patch["properties"]


async def test_complete_retries_once_on_provider_503(endpoint: _Endpoint) -> None:
    endpoint.responses = [_Response(status_code=503), _Response()]
    provider = GeminiProvider(api_key=KEY)

    answer = await provider.complete(
        "Buenas tardes",
        model="gemini-3.5-flash-lite",
        temperature=0,
        max_output_tokens=128,
        schema=schema_for(["FOOD"]),
    )

    assert answer == ANSWER
    assert endpoint.calls == 2


async def test_complete_uses_google_retry_info_for_a_short_429(
    endpoint: _Endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    waited: list[float] = []

    async def no_sleep(seconds: float) -> None:
        waited.append(seconds)

    endpoint.responses = [
        _Response(
            429,
            body={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "3.5s",
                        }
                    ],
                }
            },
        ),
        _Response(),
    ]
    monkeypatch.setattr(openai_compatible_provider.asyncio, "sleep", no_sleep)
    answer = await GeminiProvider(api_key=KEY, min_request_interval_seconds=0).complete(
        "Buenas tardes",
        model="gemini-3.5-flash-lite",
        temperature=0,
        max_output_tokens=128,
        schema=schema_for(["FOOD"]),
    )

    assert answer == ANSWER
    assert endpoint.calls == 2
    assert waited == [3.5]


async def test_complete_does_not_retry_daily_quota_without_short_retry_info(
    endpoint: _Endpoint,
) -> None:
    endpoint.responses = [
        _Response(
            429,
            body={
                "error": {
                    "code": "quota_exceeded",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                            ],
                        }
                    ],
                }
            },
        )
    ]

    with pytest.raises(ProviderFailedError):
        await GeminiProvider(api_key=KEY, fallback_models=()).complete(
            "Buenas tardes",
            model="gemini-3.5-flash-lite",
            temperature=0,
            max_output_tokens=128,
            schema=schema_for(["FOOD"]),
        )

    assert endpoint.calls == 1


def test_full_operation_reader_still_rejects_invalid_nested_values() -> None:
    with pytest.raises(UnreadableError):
        read('{"operations":[{"ask":[{"direction":"NOT_A_DIRECTION"}]}]}', {"FOOD"})


@pytest.mark.parametrize(("api_key", "available"), [("", False), (KEY, True)])
async def test_gemini_is_registered_only_when_its_key_is_set(
    api_key: str, available: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finguard_orchestrator.application.queries.get_llm_catalogue import GetLlmCatalogueQuery
    from finguard_orchestrator.bootstrap.config import get_settings
    from finguard_orchestrator.bootstrap.main import create_app

    monkeypatch.setenv("ORCHESTRATOR_PLANNER_PROVIDER", "ollama")
    monkeypatch.setenv("ORCHESTRATOR_GEMINI_API_KEY", api_key)
    get_settings.cache_clear()
    try:
        app = create_app()
        catalogue = await app.state.query_bus.ask(GetLlmCatalogueQuery())
    finally:
        get_settings.cache_clear()

    assert (GEMINI in catalogue.available_providers) is available


async def test_a_turn_reports_its_pacing_wait_apart_from_its_requests(
    endpoint: _Endpoint,
) -> None:
    """Two requests in one turn 0.2 s apart by pace: the second one's wait is counted as waiting,
    not as the model's time, so a turn's latency can be read without its pacing."""
    provider = GeminiProvider(api_key=KEY, min_request_interval_seconds=0.2)

    with counting_calls() as calls:
        for _ in range(2):
            await provider.complete(
                "hello", model="gemini-test", temperature=0, max_output_tokens=12
            )

    assert calls.provider_requests == 2
    assert 150 <= calls.provider_wait_ms <= 600
    assert calls.provider_request_ms < calls.provider_wait_ms
