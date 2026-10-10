"""LangGraph is the shipped runtime; compatibility selection and wiring stay explicit."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from finguard_orchestrator.application.engine.answer_turn import AnswerTurnHandler
from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.main import create_app


def test_langgraph_is_the_code_and_shipped_default(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Path(__file__).resolve().parents[1] / ".env.defaults"
    shipped = defaults.read_text(encoding="utf-8")
    assert Settings.model_fields["conversation_engine"].default == "model"
    assert Settings.model_fields["conversation_graph"].default == "on"
    assert "\nORCHESTRATOR_CONVERSATION_ENGINE=model\n" in shipped
    assert "\nORCHESTRATOR_CONVERSATION_GRAPH=on\n" in shipped
    # Ignore the compatibility suite's process-level override and any developer `.env`.
    monkeypatch.delenv("ORCHESTRATOR_CONVERSATION_ENGINE", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_CONVERSATION_GRAPH", raising=False)
    assert Settings(_env_file=defaults).conversation_engine == "model"
    assert Settings(_env_file=defaults).conversation_graph == "on"


def built(monkeypatch: pytest.MonkeyPatch, engine: str, graph: str) -> Any:
    monkeypatch.setenv("ORCHESTRATOR_CONVERSATION_ENGINE", engine)
    monkeypatch.setenv("ORCHESTRATOR_CONVERSATION_GRAPH", graph)
    monkeypatch.setenv("ORCHESTRATOR_DATABASE_URL", "postgresql://test:test@127.0.0.1/test")
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


def test_with_the_graph_off_the_model_engine_is_the_measured_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = built(monkeypatch, "model", "off")

    assert type(app.state.engine) is AnswerTurnHandler
    assert app.state.ask_handler is not None


def test_with_the_graph_on_the_model_engine_answers_through_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stores and the checkpointer are opened in lifespan and remain open for requests.
    # App construction must not silently fall back to an in-memory graph.
    app = built(monkeypatch, "model", "on")
    assert app.state.engine is None
    assert app.state.ask_handler is None


def test_the_graph_needs_the_model_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="requires ORCHESTRATOR_CONVERSATION_ENGINE=model"):
        built(monkeypatch, "rules", "on")


@pytest.mark.parametrize("graph", ["off", "on"])
def test_the_app_loads_langgraph_only_with_the_graph_on(graph: str) -> None:
    """In a fresh interpreter, because this suite imports the graph itself: with the flag off the
    app is built without loading the graph or LangGraph at all. `on` is the control."""
    probe = (
        "import sys; import finguard_orchestrator.bootstrap.main; "
        "print(sorted(m for m in sys.modules if m.startswith(('langgraph', 'langchain')) "
        "or m.startswith('finguard_orchestrator.application.graph')))"
    )
    env = {**os.environ, "ORCHESTRATOR_CONVERSATION_ENGINE": "model"}
    env["ORCHESTRATOR_CONVERSATION_GRAPH"] = graph
    env["ORCHESTRATOR_DATABASE_URL"] = "postgresql://test:test@127.0.0.1/test"
    env["ORCHESTRATOR_DEV_TOKENS"] = (
        '["test-token-long-enough:test-holder:00000000-0000-0000-0000-000000000001"]'
    )

    done = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=120,
    )

    loaded = done.stdout.strip().splitlines()[-1]
    assert (loaded == "[]") is (graph == "off")
