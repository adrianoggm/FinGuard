"""Four arrangements of the same one decision: whether a model is built at all.

`visual_planner` was wired into `bootstrap/main.py` in Task 6's last commit, once
`LlmVisualPlanner` existed to give the other three rungs an implementation. Asserted through
`create_app` rather than by calling `VisualPlanningPolicy` directly, the same reason
`test_who_reads_a_turn_is_configurable.py` gives for `turn_interpreter`: what could break is the
wiring itself -- a `VisualPlanningPolicy(model=..., fallback=...)` with an adapter built under the
wrong mode type-checks, starts, and quietly puts a model call on every charted turn.
"""

import pytest

from finguard_orchestrator.application import AskFinancialQuestionQuery
from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.main import create_app
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
