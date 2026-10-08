"""A date that exists in no year is said so, and the nearest real day is offered.

Validation fix 4 (ruling R58). «¿Cuánto gasté el 31 de septiembre?» reached the model, which asked
«¿De qué año?», and every year the person gave was asked again: the 31st of September exists in no
year, so asking the year was the wrong question and the conversation could not get out of it. The
date is caught where the sentence is first read, before any year is asked: the turn says the date
does not exist and offers the nearest real day. «Sí» or «el 30» then asks about that day — in the
year of the answer in view, the way a month takes it (F2), or asking the year when there is none.

«El 29 de febrero» exists in leap years, so it is not this: its year is asked, and a year without
it is asked again, saying how many days February has that year.

Driven over HTTP on the episodes stack, with the planner that asks the year the way the real model
does (`AsksTheYear`) and with the deterministic stub (`AnyPlan`).
"""

import re
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from support import episodes
from support.pending import SETTLED, AsksForTheWindow, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.dependencies import reporting_today
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import category_words, spanish, window_words
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)

REAL_RUN = date(2026, 10, 8)
_A_MONTH = re.compile(r"\b(?:" + "|".join(window_words._MONTHS) + r")\b")
_A_YEAR = re.compile(r"\b20\d{2}\b")

NO_31_SEPTEMBER = (
    "El 31 de septiembre no existe: septiembre tiene 30 días. ¿Te refieres al 30 de septiembre?"
)
NO_30_FEBRUARY = (
    "El 30 de febrero no existe: febrero tiene 28 días, 29 en los años bisiestos. "
    "¿Te refieres al 28 de febrero?"
)


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class AsksTheYear(AsksForTheWindow):
    """The real model: a month named with no year is asked about — «¿De qué año?»."""

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        folded = spanish.folded(sentence)
        if _A_MONTH.search(folded) is None or _A_YEAR.search(folded) is not None:
            return await super().plan(sentence, context=context)
        self.asked.append(sentence)
        return PlanningResult.needs_clarification(
            "¿De qué año? Por ejemplo 2025 o 2026.",
            FinancialPlanDraft(
                direction=Direction.EXPENSE,
                categories=tuple(category_words.precisely_named_in(sentence)),
            ),
            Slot.YEAR,
        )


def _model_stack() -> episodes.Stack:
    stack = asking_for_the_window_stack(planner=AsksTheYear())
    app.dependency_overrides[reporting_today] = lambda: REAL_RUN
    return stack


def _stub_stack() -> episodes.Stack:
    stack = episodes.a_stack()
    app.dependency_overrides[reporting_today] = lambda: REAL_RUN
    return stack


def _say(stack: episodes.Stack, *turns: str) -> dict[str, Any]:
    conversation: dict[str, Any] | None = SETTLED
    answer: dict[str, Any] = {}
    for turn in turns:
        stack.ledger.ran.clear()
        stack.planner.asked.clear()
        answer = ask(stack, turn, conversation)
        conversation = answer["conversation"]
    return answer


def _the_day(stack: episodes.Stack) -> tuple[date, date]:
    (plan,) = stack.ledger.ran
    assert isinstance(plan, FinancialPlan) and plan.date_range is not None
    return plan.date_range.start, plan.date_range.end


# --- said where the sentence is first read --------------------------------------------------------


@pytest.mark.parametrize("stack", [_model_stack, _stub_stack], ids=["asks-the-year", "stub"])
@pytest.mark.parametrize(
    ("sentence", "said"),
    [
        ("¿cuánto gasté el 31 de septiembre?", NO_31_SEPTEMBER),
        ("gastos del 30 de febrero", NO_30_FEBRUARY),
    ],
)
def test_a_date_no_year_has_is_said_and_the_nearest_day_offered(
    stack: Any, sentence: str, said: str
) -> None:
    built = stack()
    answer = _say(built, sentence)

    assert answer["outcome"] == "NEEDS_CLARIFICATION", answer["text"]
    assert answer["text"] == said
    assert built.planner.asked == [] and built.ledger.ran == [], "no year asked, no figure"


@pytest.mark.parametrize("acceptance", ["sí", "el 30", "sí, el 30 de septiembre", "vale"])
def test_the_offered_day_accepted_asks_its_year_and_the_year_completes_it(
    acceptance: str,
) -> None:
    """No answer in view: the year is asked, and the reply completes the day offered."""
    stack = _model_stack()
    asked = _say(stack, "¿cuánto gasté el 31 de septiembre?", acceptance)
    assert asked["conversation"]["last_asked"] == "YEAR", asked["text"]

    answer = _say(stack, "¿cuánto gasté el 31 de septiembre?", acceptance, "2025")

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert _the_day(stack) == (date(2025, 9, 30), date(2025, 9, 30))
    assert stack.planner.asked == []


def test_the_day_offered_for_the_thirtieth_of_february_is_the_twenty_eighth() -> None:
    stack = _model_stack()
    answer = _say(stack, "gastos del 30 de febrero", "sí", "2026")

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert _the_day(stack) == (date(2026, 2, 28), date(2026, 2, 28))


@pytest.mark.parametrize("stack", [_model_stack, _stub_stack], ids=["asks-the-year", "stub"])
def test_the_offered_day_after_an_answer_takes_its_year(stack: Any) -> None:
    """An answer about September 2025 in view: «¿y el 31 de septiembre?» → «sí» is 30 September
    2025 in restaurants, as a month takes the year in view (F2) — no model, no year asked."""
    built = stack()
    answer = _say(
        built,
        "¿cuánto gasté en restaurantes en septiembre de 2025?",
        "¿y el 31 de septiembre?",
        "sí",
    )

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert built.planner.asked == []
    assert _the_day(built) == (date(2025, 9, 30), date(2025, 9, 30))
    assert built.ledger.ran[0].categories == ("RESTAURANTS",)


def test_turning_the_offer_down_reads_the_reply_as_itself() -> None:
    stack = _stub_stack()
    answer = _say(stack, "¿cuánto gasté el 31 de septiembre?", "no, el 29")

    assert answer["text"] != NO_31_SEPTEMBER
    assert not any(
        isinstance(plan, FinancialPlan)
        and plan.date_range is not None
        and plan.date_range.start == date(2026, 9, 30)
        for plan in stack.ledger.ran
    )


# --- the 29th of February exists in some years ----------------------------------------------------


def test_the_twenty_ninth_of_february_asks_the_year_and_says_why_it_asks_again() -> None:
    stack = _model_stack()
    asked = _say(stack, "¿cuánto gasté el 29 de febrero?")
    assert asked["conversation"]["last_asked"] == "YEAR", asked["text"]

    again = _say(stack, "¿cuánto gasté el 29 de febrero?", "2026")
    assert again["outcome"] == "NEEDS_CLARIFICATION", again["text"]
    assert again["text"] == "Febrero de 2026 tiene 28 días. ¿De qué año? Por ejemplo 2024."

    answer = _say(stack, "¿cuánto gasté el 29 de febrero?", "2026", "2024")
    assert answer["outcome"] == "PLANNED", answer["text"]
    assert _the_day(stack) == (date(2024, 2, 29), date(2024, 2, 29))


def test_the_twenty_ninth_of_february_is_no_impossible_date_on_the_stub_either() -> None:
    stack = _stub_stack()
    answer = _say(stack, "¿cuánto gasté el 29 de febrero?")

    assert "no existe" not in answer["text"]


def test_the_twenty_ninth_of_february_after_a_leap_year_in_view_is_that_one() -> None:
    stack = _stub_stack()
    _say(stack, "¿cuánto gasté en restaurantes en febrero de 2024?", "¿y el 29 de febrero?")

    assert _the_day(stack) == (date(2024, 2, 29), date(2024, 2, 29))
    assert stack.planner.asked == []


# --- the vocabulary underneath ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "found"),
    [
        ("el 31 de septiembre", (31, 9, 30)),
        ("el 30 de febrero", (30, 2, 28)),
        ("el 31 de abril", (31, 4, 30)),
        ("el 32 de enero", (32, 1, 31)),
        ("el 29 de febrero", None),
        ("el 31 de octubre", None),
        ("el 31 de septiembre de 2025", (31, 9, 30)),
    ],
)
def test_a_date_no_year_has_is_found_by_its_words(
    sentence: str, found: tuple[int, int, int] | None
) -> None:
    assert window_words.a_day_no_year_has(sentence) == found
