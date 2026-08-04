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


# --- the largest single movement ----------------------------------------------------
#
# The worst answer the system has produced. "En los últimos 3 meses, ¿cuál ha sido el mayor
# gasto que he realizado?" came back as *3.977,37 € en los últimos 3 meses, en 56 movimientos*
# — the sum of every expense in the window, presented as the answer to a question about one of
# them. Nothing downstream could tell: the plan was valid, banking executed it, the figure is
# real. It is simply the answer to a different question.
#
# The capability does not exist: `Metric` is SUM or COUNT, so the largest single movement is not
# something a plan can ask for. Until it is, this refuses — because a total in place of a
# maximum is not a rough answer, it is a false one.


@pytest.mark.parametrize(
    "question",
    [
        "En los últimos 3 meses, ¿cuál ha sido el mayor gasto que he realizado?",
        "¿cuál fue mi mayor gasto del mes pasado?",
        "¿cuál ha sido el gasto más alto de este mes?",
        "¿cuál fue la compra más cara?",
        "el movimiento de mayor importe de julio",
        "¿cuál es el pago más grande que he hecho este año?",
        "mi compra más grande en Mercadona",
    ],
)
def test_asking_for_one_extreme_movement_is_refused(question: str) -> None:
    assert unsupported_reason(question) == "LARGEST_MOVEMENT"


@pytest.mark.parametrize(
    "question",
    [
        # A ranking of groups, which `groupBy` expresses. The discriminator is grammatical: a
        # superlative attached to a *noun* asks for one movement, while "gasté más" and "gasto
        # más" are verbs and ask which group is largest.
        "¿en qué categoría he gastado más este mes?",
        "¿qué categoría es la que más he gastado?",
        "¿dónde gasto más?",
        "¿cuál es el comercio donde más gasto este mes?",
        "¿en qué me he gastado más este mes?",
        # Plain aggregations, which must not be caught by a pattern about size.
        "¿cuánto gasté en el supermercado el mes pasado?",
        "¿cuántas compras hice en Mercadona?",
    ],
)
def test_a_ranking_or_a_total_is_not_an_extreme_movement(question: str) -> None:
    """A false refusal here is worse than the miss it prevents: it takes a question the system
    answers correctly today and turns it into a dead end before any model sees it."""
    assert unsupported_reason(question) != "LARGEST_MOVEMENT"
