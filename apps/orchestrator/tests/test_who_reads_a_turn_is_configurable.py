"""Four arrangements of the same two readers, chosen by one setting.

`Deterministic`, `Shadowed` and `Promoted` were all written before any of them could be selected:
the first ran, the second was reachable through a boolean, and the third — the one that flips the
authority to the model — existed as a class nobody could switch on. A migration that cannot be
turned on is not a migration.

The reason to turn it on is a pair of numbers that do not agree. On the 879-turn corpus the rules
score 100 %, which sounds settled until you notice the corpus labels *are* the rules' answers: it
is fitted to them and can never show them failing. Against 96 sentences written by something that
had never seen this vocabulary, the rules reach 37 of 96 and the model 88 — because the rules match
word sequences, so `de dónde sacas` is understood and `de dónde has sacado` falls to the catch-all.

Asserted through `create_app` rather than by calling the constructors, because what could break is
the wiring: a `Promoted(rules, reading)` with its arguments the wrong way round type-checks, starts,
and quietly gives the model the payments — the one thing `Promoted` exists to prevent.
"""

import pytest

from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.main import create_app
from finguard_orchestrator.domain.services.turn_interpreters import (
    Deterministic,
    Hybrid,
    Promoted,
    Shadowed,
)
from finguard_orchestrator.infrastructure.interpreting.llm_turn_interpreter import (
    LlmTurnInterpreter,
)


@pytest.mark.parametrize(
    ("chosen", "expected"),
    [
        ("router", Deterministic),
        ("shadow", Shadowed),
        ("hybrid", Hybrid),
        ("model", Promoted),
    ],
)
def test_the_setting_chooses_who_reads(monkeypatch, chosen: str, expected: type) -> None:  # noqa: ANN001
    monkeypatch.setenv("ORCHESTRATOR_TURN_INTERPRETER", chosen)
    # Built once and cached, which is right for a service and wrong for a test about what a
    # different deployment would build.
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()

    assert isinstance(app.state.turn_interpreter, expected)


def test_the_model_never_gets_the_payments(monkeypatch) -> None:  # noqa: ANN001
    """`Promoted` reads its two arguments by position, and both satisfy the same protocol.

    Swapped, everything still starts: the model would become the router that keeps payments and
    the rules would become the reading that widens everything else. The service would look
    healthier than ever on this file's other case and would hand `Intent.PAYMENT` to whichever
    model a deployment happened to install — which is the single decision this codebase has
    refused to let a model make.
    """
    monkeypatch.setenv("ORCHESTRATOR_TURN_INTERPRETER", "model")
    get_settings.cache_clear()
    try:
        promoted = create_app().state.turn_interpreter
    finally:
        get_settings.cache_clear()

    assert isinstance(promoted, Promoted)
    assert isinstance(promoted._model, LlmTurnInterpreter)  # noqa: SLF001
    assert isinstance(promoted._router, Deterministic)  # noqa: SLF001


def test_the_rules_are_what_ships() -> None:
    """Asked of the class, not an instance, so a developer who has flipped their own `.env` does
    not fail a test about what the code ships with.

    `model` costs a model call on the critical path of every turn where the rules cost none, and
    how production reads a turn belongs to whoever runs it.
    """
    assert Settings.model_fields["turn_interpreter"].default == "router"
