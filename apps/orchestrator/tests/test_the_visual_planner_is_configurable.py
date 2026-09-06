"""Four arrangements of the same one decision: whether a model is built at all.

`visual_planner` was wired into `bootstrap/main.py` in Task 6's last commit, once
`LlmVisualPlanner` existed to give the other three rungs an implementation. Asserted through
`create_app` rather than by calling `VisualPlanningPolicy` directly, the same reason
`test_who_reads_a_turn_is_configurable.py` gives for `turn_interpreter`: what could break is the
wiring itself -- a `VisualPlanningPolicy(model=..., fallback=...)` with an adapter built under the
wrong mode type-checks, starts, and quietly puts a model call on every charted turn. And three more
about *which* model and sampling the adapter is built with, and that a blank setting is unset.
"""

import pytest

from finguard_orchestrator.application import AskFinancialQuestionQuery
from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.main import create_app
from finguard_orchestrator.infrastructure.llm.echo_llm_provider import EchoLlmProvider
from finguard_orchestrator.infrastructure.planning.llm_visual_planner import LlmVisualPlanner


@pytest.mark.parametrize(
    ("mode", "builds_adapter"),
    [("deterministic", False), ("shadow", True), ("hybrid", True), ("promoted", True)],
)
def test_the_setting_chooses_whether_a_model_is_built(
    monkeypatch, mode: str, builds_adapter: bool
) -> None:  # noqa: ANN001
    monkeypatch.setenv("ORCHESTRATOR_VISUAL_PLANNER", mode)
    # Built once and cached, which is right for a service and wrong for a test about what a
    # different deployment would build.
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()

    # Asserted through create_app because what could break is the wiring: private attributes read
    # bare rather than through a public accessor nothing else needs.
    handler = app.state.query_bus._handlers[AskFinancialQuestionQuery]
    policy = handler._visual_policy
    assert policy is not None and policy._mode == mode
    assert isinstance(policy._model, LlmVisualPlanner) is builds_adapter


def test_the_rules_are_what_ships() -> None:
    """Asked of the class, not an instance, so a developer who has flipped their own `.env` does
    not fail a test about what the code ships with.

    `shadow`/`hybrid`/`promoted` all put a model call on a turn that reaches a chart; this
    field's own config comment promises it stays `deterministic` until three runs of
    `just evaluate-visual` say otherwise, and nothing else enforces that promise.
    """
    assert Settings.model_fields["visual_planner"].default == "deterministic"


def test_the_visual_planner_falls_back_to_the_financial_planners_model(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ORCHESTRATOR_VISUAL_PLANNER", "shadow")
    for name in ("PROVIDER", "MODEL", "TEMPERATURE", "MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(f"ORCHESTRATOR_VISUAL_PLANNER_{name}", raising=False)
    get_settings.cache_clear()
    try:
        app = create_app()
        settings = get_settings()
        handler = app.state.query_bus._handlers[AskFinancialQuestionQuery]
        visual = handler._visual_policy._model
        assert visual._model == settings.planner_model
        assert (visual._temperature, visual._max_output_tokens) == (
            settings.planner_temperature,
            settings.planner_max_output_tokens,
        )
    finally:
        get_settings.cache_clear()


def test_the_visual_planner_takes_its_own_model_and_sampling_when_named(monkeypatch) -> None:  # noqa: ANN001
    """`is None`, not `or`: the visual temperature 0.0 must win over a planner temperature 0.3."""
    monkeypatch.setenv("ORCHESTRATOR_VISUAL_PLANNER", "shadow")
    monkeypatch.setenv("ORCHESTRATOR_PLANNER_TEMPERATURE", "0.3")
    monkeypatch.setenv("ORCHESTRATOR_VISUAL_PLANNER_MODEL", "other:1b")
    monkeypatch.setenv("ORCHESTRATOR_VISUAL_PLANNER_PROVIDER", "echo")
    monkeypatch.setenv("ORCHESTRATOR_VISUAL_PLANNER_TEMPERATURE", "0.0")
    monkeypatch.setenv("ORCHESTRATOR_VISUAL_PLANNER_MAX_OUTPUT_TOKENS", "512")
    get_settings.cache_clear()
    try:
        app = create_app()
        settings = get_settings()
        handler = app.state.query_bus._handlers[AskFinancialQuestionQuery]
        visual = handler._visual_policy._model
        assert (visual._model, visual._temperature, visual._max_output_tokens) == (
            "other:1b",
            0.0,
            512,
        )
        assert isinstance(visual._provider, EchoLlmProvider)
        assert handler._planner._model == settings.planner_model  # untouched
        assert not isinstance(handler._planner._provider, EchoLlmProvider)
    finally:
        get_settings.cache_clear()


def test_a_blank_visual_planner_setting_is_unset(monkeypatch) -> None:  # noqa: ANN001
    for name in ("PROVIDER", "MODEL", "TEMPERATURE", "MAX_OUTPUT_TOKENS"):
        monkeypatch.setenv(f"ORCHESTRATOR_VISUAL_PLANNER_{name}", "")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert (settings.visual_planner_provider, settings.visual_planner_model) == (None, None)
        assert settings.visual_planner_sampling() == (
            settings.planner_temperature,
            settings.planner_max_output_tokens,
        )
    finally:
        get_settings.cache_clear()
