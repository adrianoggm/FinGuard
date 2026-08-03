"""The guard that runs before the model.

Refusing what the domain cannot express is currently a disposition of whichever model is
configured: it moved from 0% to 60% by changing model, and a disposition is not a
control. This is the control.

It does not read the sentence. It recognises a closed list of capabilities we know we do
not have, which is a much smaller job and the only one that can be done with certainty.
"""

import pathlib

import pytest
import yaml

from finguard_orchestrator.domain.services.capability_guard import unsupported_reason

PLANNING = pathlib.Path(__file__).resolve().parents[1] / "evaluation" / "planning"


def cases(*expects: str) -> list[tuple[str, str]]:
    """Dataset cases with the given expectation, as (id, question).

    The guard is measured against the same 235 cases the models are, because a guard
    that disagrees with the dataset is either wrong or the dataset is — and either way
    somebody has to look.
    """
    found = []
    for name in ("dev", "validation", "adversarial"):
        data = yaml.safe_load((PLANNING / f"{name}.yaml").read_text(encoding="utf-8"))
        found += [(c["id"], c["question"]) for c in data["cases"] if c["expect"] in expects]
    return found


def test_an_average_is_refused_before_any_model_sees_it() -> None:
    assert unsupported_reason("¿Cuál es mi gasto medio mensual?") is not None


def test_an_average_is_refused_however_it_is_worded() -> None:
    assert unsupported_reason("¿Cuánto gasto de media a la semana?") is not None


def test_a_comparison_between_periods_is_refused() -> None:
    assert unsupported_reason("¿He gastado más este mes que el anterior?") is not None


def test_a_ranking_is_not_a_comparison() -> None:
    """The pair that decides the whole design. Both say "más"; one asks to compare two
    windows and the other asks which group is largest inside one, which `groupBy` plus
    an ordering expresses perfectly well.

    A guard that refuses this is worse than no guard: it turns a question the system can
    answer into a refusal, and refusals are the failure the model was already making.
    """
    assert unsupported_reason("¿Cuál es el comercio donde más gasto este mes?") is None


# --- against the dataset the models are scored on -----------------------------------


# Three dataset cases are `unsupported` for reasons that are not a missing capability,
# and stating that here is more honest than widening the guard until it swallows them.
NOT_OURS = {
    # A date in the future. The planner already refuses a window that ends after today,
    # and it has the reference date — which this does not, and should not need.
    "adv_future_single_day",
    # Not about money at all. See the module docstring: recognising this needs an
    # allow-list of financial words, and "¿y este mes?" contains none of them.
    "adv_weather",
    "adv_general_knowledge",
}


@pytest.mark.parametrize(("case_id", "question"), cases("unsupported"), ids=lambda v: v)
def test_the_guard_catches_what_the_dataset_calls_unsupported(case_id: str, question: str) -> None:
    if case_id in NOT_OURS:
        pytest.skip("not a missing capability — see NOT_OURS")
    assert unsupported_reason(question) is not None, case_id


@pytest.mark.parametrize(("case_id", "question"), cases("plan", "clarification"), ids=lambda v: v)
def test_the_guard_refuses_nothing_the_system_can_answer(case_id: str, question: str) -> None:
    """The property that matters more than the one above.

    A miss costs what we already have: the model decides, sometimes wrongly. A false
    refusal costs something we do not have yet — it takes a question the pipeline answers
    correctly today and turns it into a dead end, before any model sees it. This runs
    over every answerable case in all three sets so that widening a pattern cannot buy
    recall with silence.
    """
    assert unsupported_reason(question) is None, f"{case_id}: {question}"
