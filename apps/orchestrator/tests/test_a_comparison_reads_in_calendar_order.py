"""A comparison runs from the period that came first, and its subject can be corrected.

Validation fix 1, F3, from the real conversations of 2026-10-08:

- comparacion-gasolina 3. «En porcentaje» on «junio vs julio» compared July to June and said
  «disminuyó un 35,53 %» when July was 55,12 % higher: a false statement.
  `_direct_period_comparison` built its operands in reverse mention order (P-1), so «junio vs
  julio» had July as the reference and June as the side whose change is reported.
- impro-comparar-meses 3. «No, digo gasolina» over a comparison was planned from scratch: fuel last
  month, both months dropped. The comparison could be continued by «¿y en X?» and its siblings, not
  by a correction of its subject.
- impro-comparar-meses 7. «¿Y de salud?» went to the model and lost both months; «¿y en
  restaurantes?» kept them. «De» opens the same ellipsis «en» does.

Driven over HTTP on the episodes stack, with what the real model returned where it read the turn.
"""

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from support import episodes
from support.pending import SETTLED, _with_planner, ask

from finguard_orchestrator.application import AskFinancialQuestionQuery
from finguard_orchestrator.application.queries.readers import comparisons
from finguard_orchestrator.bootstrap.dependencies import turn_interpreter
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Period
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.services import spanish, window_words
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class FuelByMonth(episodes.Ledger):
    """The real figures of comparacion-gasolina: June 102,80 € in 2, July 159,46 € in 3."""

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        self.ran.append(plan)
        assert plan.date_range is not None
        june = plan.date_range.start.month == 6
        return QueryAnswer(
            currency="EUR",
            total_minor=10_280 if june else 15_946,
            movement_count=2 if june else 3,
        )


class ReadsACorrection:
    """The interpreter of a deployment that reads «no, …» as a correction, as the model can."""

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        if spanish.folded(sentence).startswith(("no ", "no,", "queria", "me referia")):
            return Intent.CORRECTION
        return await Deterministic().interpret(sentence, conversation=conversation)


def _fuel_stack() -> episodes.Stack:
    stack = episodes.a_stack()
    stack.ledger = FuelByMonth()
    _with_planner(stack, stack.planner)
    return stack


def _months(stack: episodes.Stack) -> list[int]:
    return [plan.date_range.start.month for plan in stack.ledger.ran if plan.date_range]


# --- the order the windows happened in ----------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "junio vs julio en gasolina",
        "julio vs junio en gasolina",
        "¿Gasté más en gasolina en junio o en julio?",
        "compara mis gastos de gasolina de junio y julio",
    ],
)
def test_the_percentage_runs_from_the_month_that_came_first(said: str) -> None:
    """comparacion-gasolina 3: «aumentó un 55,12 %» from June to July, whichever month was said
    first — never «disminuyó» about a month that went up."""
    stack = _fuel_stack()
    compared = ask(stack, said, SETTLED)
    assert compared["outcome"] == "PLANNED", compared["text"]
    assert _months(stack) == [6, 7], "June is the reference, July the side that changed"
    assert compared["difference_minor"] == 15_946 - 10_280

    answer = ask(stack, "en porcentaje", compared["conversation"])

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert "aumentó un 55,12" in answer["text"], answer["text"]
    assert "disminuyó" not in answer["text"]


def test_the_comparison_reader_builds_its_operands_in_calendar_order() -> None:
    """P-1 at the reader: «junio vs julio» and «compara … de junio y julio» are June, then July."""
    for sentence in ("junio vs julio en restaurantes", "compara mis gastos de junio y julio"):
        compared = comparisons._direct_period_comparison(
            AskFinancialQuestionQuery(sentence, episodes.ACCOUNT, episodes.TODAY)
        )
        assert compared is not None
        assert compared.earlier.date_range == window_words.whole_month(2026, 6)
        assert compared.latest.date_range == window_words.whole_month(2026, 7)


def test_two_years_and_two_named_periods_run_in_calendar_order_too() -> None:
    for sentence, earlier, latest in (
        (
            "Compara mis ingresos de 2025 vs 2026",
            window_words.whole_year(2025),
            window_words.whole_year(2026),
        ),
        (
            "Compara mis ingresos de 2026 vs 2025",
            window_words.whole_year(2025),
            window_words.whole_year(2026),
        ),
        (
            "compara el trimestre pasado con este trimestre",
            RelativePeriod(Period.LAST_QUARTER),
            RelativePeriod(Period.THIS_QUARTER),
        ),
        (
            "compara este trimestre con el trimestre pasado",
            RelativePeriod(Period.LAST_QUARTER),
            RelativePeriod(Period.THIS_QUARTER),
        ),
    ):
        compared = comparisons._direct_period_comparison(
            AskFinancialQuestionQuery(sentence, episodes.ACCOUNT, date(2026, 8, 5))
        )
        assert compared is not None, sentence
        assert (compared.earlier.period or compared.earlier.date_range) == earlier, sentence
        assert (compared.latest.period or compared.latest.date_range) == latest, sentence


# --- the subject corrected over a comparison ----------------------------------------------------

SUPER = ("gaste mas en el super en julio o en agosto?", "y en restaurantes")


def _corrected(sentence: str, interpreter: Any = None) -> tuple[episodes.Stack, dict[str, Any]]:
    stack = episodes.a_stack()
    if interpreter is not None:
        app.dependency_overrides[turn_interpreter] = lambda: interpreter
    conversation: dict[str, Any] | None = SETTLED
    for turn in SUPER:
        conversation = ask(stack, turn, conversation)["conversation"]
    stack.ledger.ran.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, sentence, conversation)


@pytest.mark.parametrize(
    "sentence",
    [
        "no, digo gasolina",
        "digo gasolina",
        "quería decir gasolina",
        "me refería a la gasolina",
        "no, me refería a gasolina",
    ],
)
@pytest.mark.parametrize("interpreter", [None, ReadsACorrection()], ids=["router", "correction"])
def test_a_corrected_subject_keeps_both_months(sentence: str, interpreter: Any) -> None:
    """impro-comparar-meses 3: fuel in July and August, never fuel last month."""
    stack, answer = _corrected(sentence, interpreter)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == [], "no model reads the correction"
    assert sorted(_months(stack)) == [7, 8]
    assert all(plan.categories == ("TRANSPORT_FUEL",) for plan in stack.ledger.ran)


@pytest.mark.parametrize("sentence", ["y de salud?", "¿y de salud?", "de salud"])
def test_de_opens_the_ellipsis_as_en_does(sentence: str) -> None:
    """impro-comparar-meses 7: health in both months."""
    stack, answer = _corrected(sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert sorted(_months(stack)) == [7, 8]
    assert all(plan.categories == ("HEALTH",) for plan in stack.ledger.ran)


@pytest.mark.parametrize(
    "sentence", ["no, digo el mes pasado", "no, digo gasolina en agosto", "digo que no"]
)
def test_a_correction_that_is_not_one_subject_is_not_read_as_one(sentence: str) -> None:
    """A window, a window beside the subject, or no subject at all: not this reading."""
    stack, _ = _corrected(sentence)

    ran = stack.ledger.ran
    assert not (
        len(ran) == 2
        and sorted(_months(stack)) == [7, 8]
        and all(plan.categories == ("TRANSPORT_FUEL",) for plan in ran)
    )


# --- round 2, I-3: the other two readers that build a pair ---------------------------------------
#
# `_comparison_with_a_named_window` («compáralo con julio») made the window named the reference and
# the one in view the side that changed; `_period_comparison_plans` («compáralos») made the earlier
# answer the reference. Either way round, «en porcentaje» could say «disminuyó» about a month that
# went up. July 159,46 €, June 102,80 €, August 100,00 €.


class FuelByThreeMonths(FuelByMonth):
    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        self.ran.append(plan)
        assert plan.date_range is not None
        total = {6: 10_280, 7: 15_946, 8: 10_000}[plan.date_range.start.month]
        return QueryAnswer(currency="EUR", total_minor=total, movement_count=3)


def _percentage_after(*turns: str) -> tuple[episodes.Stack, str]:
    stack = episodes.a_stack()
    stack.ledger = FuelByThreeMonths()
    _with_planner(stack, stack.planner)
    conversation: dict[str, Any] | None = SETTLED
    for turn in turns:
        answer = ask(stack, turn, conversation)
        assert answer["outcome"] == "PLANNED", (turn, answer["text"])
        conversation = answer["conversation"]
    stack.ledger.plans.clear()
    stack.ledger.ran.clear()
    percentage = ask(stack, "en porcentaje", conversation)
    assert percentage["outcome"] == "PLANNED", percentage["text"]
    return stack, percentage["text"]


@pytest.mark.parametrize(
    ("turns", "said"),
    [
        (("¿cuánto gasté en gasolina en junio?", "compáralo con julio"), "aumentó un 55,12"),
        (("¿cuánto gasté en gasolina en julio?", "compáralo con junio"), "aumentó un 55,12"),
        (("¿cuánto gasté en gasolina en julio?", "compáralo con agosto"), "disminuyó un 37,29"),
        (
            ("¿cuánto gasté en gasolina en junio?", "compáralo con julio"),
            "al 2026-06-30 a del 2026-07-01",
        ),
    ],
    ids=["june-then-july", "july-then-june", "july-then-august", "the-direction-said"],
)
def test_a_named_window_compared_runs_from_the_earlier_month(
    turns: tuple[str, ...], said: str
) -> None:
    _, text = _percentage_after(*turns)

    assert said in text, text


@pytest.mark.parametrize(
    ("turns", "said"),
    [
        (
            ("¿cuánto gasté en gasolina en julio?", "¿y en junio?", "compáralos"),
            "aumentó un 55,12",
        ),
        (
            ("¿cuánto gasté en gasolina en junio?", "¿y en julio?", "compáralos"),
            "aumentó un 55,12",
        ),
        (
            ("¿cuánto gasté en gasolina en julio?", "¿y en agosto?", "compáralos"),
            "disminuyó un 37,29",
        ),
        (
            ("¿cuánto gasté en gasolina en agosto?", "¿y en julio?", "compáralos"),
            "disminuyó un 37,29",
        ),
    ],
    ids=["july-then-june", "june-then-july", "july-then-august", "august-then-july"],
)
def test_two_answers_compared_run_from_the_earlier_month(turns: tuple[str, ...], said: str) -> None:
    _, text = _percentage_after(*turns)

    assert said in text, text
    assert ("aumentó" in said) != ("disminuyó" in text)
