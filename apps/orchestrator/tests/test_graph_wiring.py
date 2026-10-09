"""The investigation graph's own flag: off by default, built only beside the model engine, and with
it off the model engine is exactly the one Task 7 measured."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from finguard_orchestrator.application.engine.answer_turn import AnswerTurnHandler
from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.main import create_app


def test_the_graph_is_off_by_default_and_as_shipped() -> None:
    assert Settings.model_fields["conversation_graph"].default == "off"
    shipped = (Path(__file__).resolve().parents[1] / ".env.defaults").read_text(encoding="utf-8")
    assert "\nORCHESTRATOR_CONVERSATION_GRAPH=off\n" in shipped
    assert get_settings().conversation_graph == "off"


def built(monkeypatch: pytest.MonkeyPatch, engine: str, graph: str) -> object:
    monkeypatch.setenv("ORCHESTRATOR_CONVERSATION_ENGINE", engine)
    monkeypatch.setenv("ORCHESTRATOR_CONVERSATION_GRAPH", graph)
    monkeypatch.setenv("ORCHESTRATOR_ENGINE_MODEL", "")
    get_settings.cache_clear()
    try:
        return create_app().state.engine
    finally:
        get_settings.cache_clear()


def test_with_the_graph_off_the_model_engine_is_the_measured_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = built(monkeypatch, "model", "off")

    assert type(engine) is AnswerTurnHandler


def test_with_the_graph_on_the_model_engine_answers_through_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from finguard_orchestrator.application.graph.graph_turn import GraphTurnHandler
    from finguard_orchestrator.infrastructure.engine.llm_turn_operations import LlmTurnOperations
    from finguard_orchestrator.infrastructure.graph.llm_assessor import LlmInvestigationAssessor
    from finguard_orchestrator.infrastructure.llm.counting_provider import (
        CountingProvider,
        calls_so_far,
    )

    engine = built(monkeypatch, "model", "on")

    assert isinstance(engine, GraphTurnHandler)
    ports = engine._ports  # noqa: SLF001
    assert isinstance(ports.interpreter, LlmTurnOperations)
    assert isinstance(ports.assessor, LlmInvestigationAssessor)
    assert isinstance(ports.assessor._provider, CountingProvider)  # noqa: SLF001
    assert ports.count_calls is calls_so_far


def test_the_graph_needs_the_model_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    assert built(monkeypatch, "rules", "on") is None


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
