"""The HTTP surface: the contract shape, and that no domain object is the body."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from support import BACKUP, PRIMARY, catalogue

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.commands.replace_llm_policy import (
    ReplaceLlmPolicyCommand,
    ReplaceLlmPolicyHandler,
    ResetLlmPolicyCommand,
    ResetLlmPolicyHandler,
)
from finguard_orchestrator.application.policy_store import PolicyStore
from finguard_orchestrator.application.queries.get_llm_catalogue import (
    GetLlmCatalogueHandler,
    GetLlmCatalogueQuery,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.dto.llm_dto import QuestionRequest
from finguard_orchestrator.infrastructure.http.dependencies import (
    authenticated_caller,
    get_command_bus,
    get_query_bus,
)


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
    # /admin now needs a scope, so these tests present a credential that has it. Overriding
    # the identity rather than a real token keeps the test about the endpoints; that the
    # scope is enforced at all is test_scopes.py's job.
    app.dependency_overrides[authenticated_caller] = lambda: ADMIN
    yield
    app.dependency_overrides.clear()


ADMIN = Caller(
    holder_reference="ES-0001",
    account_id=uuid.UUID("08aba04f-2b30-4639-88a6-eac741153ce0"),
    scopes=frozenset({Scope.QUESTIONS_READ, Scope.ASSISTANT_USE, Scope.ASSISTANT_ADMIN}),
)

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

    assert set(body["checks"]) == {
        "questions_enabled",
        "catalogue_provider",
        "planner",
        "banking",
        "conversation_runtime",
        "persistence",
    }
    settings = app.state.settings
    assert body["financial_path"]["provider"] == settings.planner_provider
    assert body["financial_path"]["model"] == (settings.engine_model or settings.planner_model)


def test_ready_is_false_while_financial_questions_are_disabled() -> None:
    class ReadyProbe:
        async def ping(self) -> bool:
            return True

    previous_enabled = app.state.questions_enabled
    previous_planner = app.state.planner_probe
    previous_banking = app.state.banking_probe
    previous_engine = getattr(app.state, "engine", None)
    previous_persistence = getattr(app.state, "persistence_probe", None)
    # Swapped to the provider this build can talk to, so the only false check is the one
    # under test. It used to rely on an earlier test having done this swap — which is why
    # it started failing the moment /admin needed a scope, several tests away.
    assert client.put("/admin/llm-policy", json={"model_alias": "backup"}).status_code == 200
    app.state.questions_enabled = False
    app.state.planner_probe = ReadyProbe()
    app.state.banking_probe = ReadyProbe()
    # Any non-None graph counts as built; /ready never calls it.
    app.state.engine = object()
    app.state.persistence_probe = ReadyProbe()
    try:
        response = client.get("/ready")
    finally:
        app.state.questions_enabled = previous_enabled
        app.state.planner_probe = previous_planner
        app.state.banking_probe = previous_banking
        app.state.engine = previous_engine
        app.state.persistence_probe = previous_persistence

    assert response.status_code == 503
    assert response.json()["checks"] == {
        "questions_enabled": False,
        "catalogue_provider": True,
        "planner": True,
        "banking": True,
        "conversation_runtime": True,
        "persistence": True,
    }


def test_the_openapi_document_is_served() -> None:
    document = client.get("/openapi.json").json()

    assert document["info"]["title"] == "FinGuard Assistant API"
    assert "/v1/messages" in document["paths"]
    assert client.get("/docs").status_code == 200


def test_ready_says_whether_payments_are_on_without_that_making_it_unready() -> None:
    """Reported, not checked. Payments are off by default, so putting the flag in `checks` would
    make every deployment that answers questions and nothing else report 503 — and a readiness probe
    that is red on a correctly configured service is a probe somebody switches off.

    It is reported at all because a flag that reaches nothing observable is a flag nobody can prove
    arrived: "payments are enabled and the endpoint 404s" is a debugging session this prevents.
    """
    body = client.get("/ready").json()

    assert body["payments"] is app.state.payments_enabled
    assert "payments" not in body["checks"]


def test_a_request_may_state_its_viewport_and_a_request_that_does_not_still_works() -> None:
    """`extra="forbid"` means a field must be *declared* to be accepted, and declared means an
    old client that omits it keeps working.

    Asked of the request contract itself: the route in front of it now needs a built graph and an
    Idempotency-Key, and a missing graph answers 503 before the body's own 422 is ever reported, so
    a status code there could not tell a declared `viewport` from an undeclared one.
    """
    without = QuestionRequest(question="¿cuánto gasté este mes?")
    assert without.viewport == "DESKTOP"

    with_viewport = QuestionRequest(
        question="¿cuánto gasté este mes?", viewport="MOBILE", max_visible_rows=5
    )
    assert (with_viewport.viewport, with_viewport.max_visible_rows) == ("MOBILE", 5)

    with pytest.raises(ValidationError):
        QuestionRequest(question="¿cuánto gasté este mes?", viewport="WATCH")
