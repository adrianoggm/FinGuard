"""The HTTP surface: the contract shape, and that no domain object is the body."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from support import BACKUP, PRIMARY, catalogue

from finguard_assistant.application.bus.command_bus import CommandBus
from finguard_assistant.application.bus.query_bus import QueryBus
from finguard_assistant.application.commands.replace_llm_policy import (
    ReplaceLlmPolicyCommand,
    ReplaceLlmPolicyHandler,
    ResetLlmPolicyCommand,
    ResetLlmPolicyHandler,
)
from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.application.queries.get_llm_catalogue import (
    GetLlmCatalogueHandler,
    GetLlmCatalogueQuery,
)
from finguard_assistant.bootstrap.dependencies import get_command_bus, get_query_bus
from finguard_assistant.bootstrap.main import app


@pytest.fixture(autouse=True)
def _wired() -> Iterator[None]:
    """Buses wired to a two-model catalogue, so swapping is observable.

    Only `echo` is a provider this build can talk to; `flaky` is not, which is what the
    readiness test relies on.
    """
    models = catalogue(PRIMARY, BACKUP)
    store = PolicyStore(models, models.default_policy())
    providers = ["echo"]

    queries = QueryBus()
    queries.register(GetLlmCatalogueQuery, GetLlmCatalogueHandler(store, models, providers))
    commands = CommandBus()
    commands.register(ReplaceLlmPolicyCommand, ReplaceLlmPolicyHandler(store, models, providers))
    commands.register(ResetLlmPolicyCommand, ResetLlmPolicyHandler(store, models, providers))

    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_command_bus] = lambda: commands
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


def test_health_answers_without_dependencies() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_policy_reports_the_model_its_provider_and_the_catalogue() -> None:
    response = client.get("/admin/llm-policy")

    assert response.status_code == 200
    body = response.json()
    assert body["active"]["model"] == {"alias": "primary", "provider": "flaky", "model": "big"}
    assert body["fallback"] == "NEXT"
    assert [m["alias"] for m in body["available_models"]] == ["primary", "backup"]
    assert body["available_providers"] == ["echo"]


def test_swapping_takes_only_the_alias() -> None:
    # The provider and the model name come from the catalogue: a caller cannot invent
    # a pairing this deployment does not have.
    response = client.put("/admin/llm-policy", json={"model_alias": "backup"})

    assert response.status_code == 200
    assert response.json()["active"]["model"]["provider"] == "echo"
    assert client.get("/admin/llm-policy").json()["active"]["model"]["alias"] == "backup"


def test_swapping_keeps_the_settings_it_does_not_mention() -> None:
    client.put("/admin/llm-policy", json={"model_alias": "backup", "temperature": 0.9})

    active = client.get("/admin/llm-policy").json()["active"]

    assert active["temperature"] == 0.9
    assert active["max_output_tokens"] == 1024


def test_an_unknown_alias_changes_nothing_and_says_what_exists() -> None:
    response = client.put("/admin/llm-policy", json={"model_alias": "ghost"})

    assert response.status_code == 422
    assert response.json()["code"] == "unknown_model"
    assert response.json()["available"] == ["backup", "primary"]
    assert client.get("/admin/llm-policy").json()["active"]["model"]["alias"] == "primary"


def test_an_out_of_range_temperature_is_rejected() -> None:
    response = client.put("/admin/llm-policy", json={"model_alias": "backup", "temperature": 9})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_input"


def test_an_unknown_field_in_the_body_is_rejected() -> None:
    response = client.put("/admin/llm-policy", json={"model_alias": "backup", "provider": "openai"})

    assert response.status_code == 422


def test_reset_returns_to_the_startup_policy() -> None:
    client.put("/admin/llm-policy", json={"model_alias": "backup"})

    response = client.post("/admin/llm-policy/reset")

    assert response.status_code == 200
    assert response.json()["active"]["model"]["alias"] == "primary"


def test_ready_warns_when_the_active_provider_is_not_built_in() -> None:
    # The catalogue names `flaky`, which this build cannot talk to.
    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["provider"] == "flaky"
    assert body["checks"]["catalogue_provider"] is False


def test_ready_reports_on_what_the_financial_endpoint_needs() -> None:
    """It used to check only the catalogue's provider, which /v1/questions does not use.
    With Ollama pointed at a dead port the service answered 200 ok while every financial
    question would have failed."""
    body = client.get("/ready").json()

    assert set(body["checks"]) == {"catalogue_provider", "planner", "banking"}


def test_the_openapi_document_is_served() -> None:
    document = client.get("/openapi.json").json()

    assert document["info"]["title"] == "FinGuard Assistant API"
    assert "/v1/messages" in document["paths"]
    assert client.get("/docs").status_code == 200
