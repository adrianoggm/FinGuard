"""Gemini's OpenAI-compatible registration and FinGuard's schema boundary."""

from typing import Any

import pytest

from finguard_orchestrator.infrastructure.engine.operations_schema import (
    UnreadableError,
    read,
    schema_for,
)
from finguard_orchestrator.infrastructure.llm import openai_compatible_provider
from finguard_orchestrator.infrastructure.llm.gemini import (
    GEMINI,
    GeminiProvider,
    _shallow_operations_schema,
)

KEY = "AIza-test-key-not-real"
ANSWER = '{"operations":[{"smalltalk":"GREETING"}]}'


class _Response:
    status_code = 200
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return {"choices": [{"message": {"content": ANSWER}}]}


class _Endpoint:
    request: dict[str, Any] | None = None


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
            return _Response()

    monkeypatch.setattr(openai_compatible_provider.httpx, "AsyncClient", FakeClient)
    return seen


def test_gemini_is_an_openai_compatible_provider() -> None:
    provider = GeminiProvider(api_key=KEY)

    assert provider.name == GEMINI == "gemini"
    assert provider._base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert (
        provider._readiness_url == "https://generativelanguage.googleapis.com/v1beta/openai/models"
    )


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
        "patch",
        "compare",
        "measure",
        "select_operand",
        "reset",
        "balance",
        "clarify",
        "smalltalk",
        "delegate",
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

    patch = operations["patch"]
    assert patch["properties"]["target"]["enum"] == ["focus", "earlier", "latest", "pending"]
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
    sent_schema = endpoint.request["json"]["response_format"]["json_schema"]["schema"]
    assert "$defs" not in sent_schema
    operations = sent_schema["properties"]["operations"]["items"]["anyOf"]
    assert len(operations) == 10
    patch = next(v["properties"]["patch"] for v in operations if "patch" in v["properties"])
    assert "changes" in patch["properties"]


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
