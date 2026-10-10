"""LangGraph is the only runtime, and the composition root wires it explicitly."""

from typing import Any

import pytest

from finguard_orchestrator.bootstrap.config import get_settings
from finguard_orchestrator.bootstrap.main import create_app


def built(monkeypatch: pytest.MonkeyPatch, database_url: str) -> Any:
    monkeypatch.setenv("ORCHESTRATOR_DATABASE_URL", database_url)
    monkeypatch.setenv(
        "ORCHESTRATOR_DEV_TOKENS",
        '["test-token-long-enough:test-holder:00000000-0000-0000-0000-000000000001"]',
    )
    monkeypatch.setenv("ORCHESTRATOR_ENGINE_MODEL", "")
    get_settings.cache_clear()
    try:
        return create_app()
    finally:
        get_settings.cache_clear()


def test_the_graph_is_built_in_the_lifespan_not_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stores and the checkpointer are opened in lifespan and remain open for requests.
    # App construction must not silently fall back to an in-memory graph.
    app = built(monkeypatch, "postgresql://test:test@127.0.0.1/test")

    assert app.state.engine is None


def test_the_app_refuses_to_start_without_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    # The graph persists its conversations in PostgreSQL; without one there is no runtime at all.
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        built(monkeypatch, "")
