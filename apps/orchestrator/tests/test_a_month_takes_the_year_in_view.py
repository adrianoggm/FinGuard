"""A month named without a year takes the year of the answer in view, and a year said to
«¿De qué año?» completes the question it was asked about.

Validation fix 1, F2. In the real conversations of 2026-10-08 the model asked «¿De qué año?» four
times about a month the comparison in view had already placed in 2026 — «ahora solo julio»
(comparacion-gasolina 4), «los mismos meses, agosto y septiembre» (comparar-meses 4), «pero de
julio y agosto» (impro-comparar-meses 4), «en julio y agosto como antes» (impro-comparar-meses 8).
No reader took those turns: a bare month («solo julio») is no window to `window_words`, and two
months with no comparison word are no comparison to the comparison readers. And the replies were
lost: «2026» is no window to `window_words` even as the answer to «¿De qué año?», so the arbitration
saw no fill, the planner was asked again and the turn got «Sigo sin tener claro el periodo»
(comparar-meses 5); «este año» filled the year of a draft that never held the months, so it ran the
whole year (impro-comparar-meses 5). «Los mismos 3 meses» to «¿De qué periodo?» named no window
either (impro-mandado-recibido 4).

Driven over HTTP on the episodes stack, on 2026-10-08 as the real run was, with a planner that asks
the year the way the real model did, so only the deterministic path can make these pass.
"""

import re
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from support import episodes
from support.pending import AsksForTheWindow, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.dependencies import reporting_today
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import category_words, spanish, window_words
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)

# The day of the real run, so September is a month that has happened.
REAL_RUN = date(2026, 10, 8)
_A_MONTH: re.Pattern[str] = re.compile(r"\b(?:" + "|".join(window_words._MONTHS) + r")\b")
_A_YEAR = re.compile(r"\b20\d{2}\b")


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class AsksTheYear(AsksForTheWindow):
    """The real model on 2026-10-08: a month named with no year is asked about — «¿De qué año? Por
    ejemplo 2025 o 2026.» — with a draft of what else it read and no window. Everything else is
    `AsksForTheWindow`'s."""

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


def _stack() -> episodes.Stack:
    stack = asking_for_the_window_stack(planner=AsksTheYear())
    app.dependency_overrides[reporting_today] = lambda: REAL_RUN
    return stack


def _walk(stack: episodes.Stack, *turns: str) -> dict[str, Any]:
    """Every turn but the last, each answered; the last one's answer, with the ledger and the
    planner's log cleared before it so only its own reads and model calls are left."""
    conversation: dict[str, Any] | None = None
    for turn in turns[:-1]:
        answered = ask(stack, turn, conversation)
        conversation = answered["conversation"]
    stack.ledger.ran.clear()
    stack.planner.asked.clear()
    return ask(stack, turns[-1], conversation)


def _months(stack: episodes.Stack) -> list[tuple[int, int]]:
    """The months the turn read, as (year, month), in the order it read them."""
    read = []
    for plan in stack.ledger.ran:
        assert isinstance(plan, FinancialPlan) and plan.date_range is not None
        assert plan.date_range == window_words.whole_month(
            plan.date_range.start.year, plan.date_range.start.month
        )
        read.append((plan.date_range.start.year, plan.date_range.start.month))
    return read


# --- a month after an answer: the year in view ----------------------------------------------------


def test_only_one_month_of_a_comparison_is_that_month_of_it() -> None:
    """comparacion-gasolina 4: fuel in July 2026, one figure."""
    stack = _stack()
    answer = _walk(stack, "junio vs julio en restaurantes", "y en gasolina?", "ahora solo julio")

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == [], "no «¿De qué año?»"
    assert _months(stack) == [(2026, 7)]
    assert stack.ledger.ran[0].categories == ("TRANSPORT_FUEL",)


def test_only_one_month_after_the_percentage_is_that_month_too() -> None:
    """comparacion-gasolina 3 and 4 as they were said: «en porcentaje» in between."""
    stack = _stack()
    answer = _walk(
        stack,
        "junio vs julio en restaurantes",
        "y en gasolina?",
        "en porcentaje",
        "ahora solo julio",
    )

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert _months(stack) == [(2026, 7)]
    assert stack.ledger.ran[0].categories == ("TRANSPORT_FUEL",)


def test_the_month_takes_the_comparisons_own_year() -> None:
    """A comparison of 2025: «solo julio» is July 2025, never July of this year."""
    stack = _stack()
    _walk(stack, "compara restaurantes en junio de 2025 y julio de 2025", "ahora solo julio")

    assert _months(stack) == [(2025, 7)]
    assert stack.planner.asked == []


@pytest.mark.parametrize(
    ("turns", "category"),
    [
        (
            (
                "compara lo q gaste en restaurantes en agosto y en septiembre",
                "y en el super?",
                "los mismos meses, agosto y septiembre",
            ),
            "GROCERIES",
        ),
        (
            (
                "gaste mas en el super en julio o en agosto?",
                "y en gasolina",
                "pero de julio y agosto",
            ),
            "TRANSPORT_FUEL",
        ),
        (
            ("gasolina julio vs agosto", "y en salud?", "en julio y agosto como antes"),
            "HEALTH",
        ),
    ],
)
def test_two_months_after_a_comparison_are_that_comparison(
    turns: tuple[str, ...], category: str
) -> None:
    """comparar-meses 4, impro-comparar-meses 4 and 8: the two months, in the year in view, of
    the subject in view — no «¿De qué año?»."""
    stack = _stack()
    answer = _walk(stack, *turns)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert sorted(_months(stack)) == sorted(
        (2026, window_words._MONTH_NUMBER[month])
        for month in window_words._MONTHS
        if re.search(rf"\b{month}\b", spanish.folded(turns[-1]))
    )
    assert all(plan.categories == (category,) for plan in stack.ledger.ran)
    assert answer["conversation"]["last_comparison_earlier"] is not None


def test_two_months_after_one_answer_compare_its_subject() -> None:
    """impro-comparar-meses 4 as the real run reached it: fuel last month in view."""
    stack = _stack()
    answer = _walk(stack, "¿cuánto gasté en gasolina el mes pasado?", "pero de julio y agosto")

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert sorted(_months(stack)) == [(2026, 7), (2026, 8)]
    assert all(plan.categories == ("TRANSPORT_FUEL",) for plan in stack.ledger.ran)


def test_a_month_after_an_answer_of_another_year_is_that_years() -> None:
    """«¿Y en julio?» after March 2025 is July 2025: the year in view, not this one."""
    stack = _stack()
    _walk(stack, "¿cuánto gasté en restaurantes en marzo de 2025?", "¿y en julio?")

    assert _months(stack) == [(2025, 7)]
    assert stack.ledger.ran[0].categories == ("RESTAURANTS",)


def test_a_month_the_year_in_view_has_not_reached_keeps_todays_reading() -> None:
    """November of this year has not happened: the month is the last November, as before."""
    stack = _stack()
    _walk(stack, "¿cuánto gasté en restaurantes este año?", "¿y en noviembre?")

    assert _months(stack) == [(2025, 11)]


def test_with_nothing_in_view_a_month_is_read_as_it_always_was() -> None:
    """No base, no year to take: the turn reaches the planner exactly as before."""
    stack = _stack()
    answer = _walk(stack, "pero de julio y agosto")

    assert stack.planner.asked == ["pero de julio y agosto"]
    assert answer["conversation"]["last_asked"] == "YEAR"


# --- the reply to «¿De qué año?» ------------------------------------------------------------------


def test_a_year_alone_completes_the_question_and_keeps_its_months() -> None:
    """comparar-meses 3 to 5 as the real run reached them: the supermarket waiting for its period,
    two months said, the year asked — and «2026» answered with «Sigo sin tener claro el periodo»."""
    stack = _stack()
    asked = _walk(stack, "¿cuánto gasté en el super?", "los mismos meses, agosto y septiembre")
    assert asked["conversation"]["last_asked"] == "YEAR", asked["text"]
    stack.planner.asked.clear()

    answer = ask(stack, "2026", asked["conversation"])

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert "Sigo sin tener claro" not in answer["text"]
    assert stack.planner.asked == []
    assert sorted(_months(stack)) == [(2026, 8), (2026, 9)]
    assert all(plan.categories == ("GROCERIES",) for plan in stack.ledger.ran), "its subject"
    assert answer["conversation"]["last_asked"] is None


@pytest.mark.parametrize("reply", ["este año", "de este", "el 2026", "2026"])
def test_this_year_completes_the_question_with_its_months(reply: str) -> None:
    """impro-comparar-meses 5: «este año» ran every expense of the year, months dropped."""
    stack = _stack()
    asked = _walk(stack, "mis gastos de julio y agosto")
    assert asked["conversation"]["last_asked"] == "YEAR", asked["text"]
    stack.planner.asked.clear()

    answer = ask(stack, reply, asked["conversation"])

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert sorted(_months(stack)) == [(2026, 7), (2026, 8)]
    assert all(plan.direction is Direction.EXPENSE for plan in stack.ledger.ran)


def test_a_year_completes_one_month_with_its_subject() -> None:
    """One month asked about: the subject of the draft, in that month of that year."""
    stack = _stack()
    asked = _walk(stack, "mis gastos de diciembre")
    assert asked["conversation"]["last_asked"] == "YEAR", asked["text"]
    stack.planner.asked.clear()

    answer = ask(stack, "2025", asked["conversation"])

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert _months(stack) == [(2025, 12)]


# --- «los mismos meses»: the window of the last answer ----------------------------------------


def test_the_same_months_answer_the_period_question_with_the_last_answers_window() -> None:
    """impro-mandado-recibido 4: «los mismos 3 meses» got «Sigo sin tener claro el periodo»."""
    stack = _stack()
    first = ask(stack, "¿cuánto gasté en gasolina en los últimos 3 meses?")
    asked = ask(stack, "¿cuánto gasté en ocio?", first["conversation"])
    assert asked["conversation"]["awaiting"] == "WINDOW", asked["text"]
    stack.ledger.ran.clear()
    stack.planner.asked.clear()

    answer = ask(stack, "los mismos 3 meses", asked["conversation"])

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    (plan,) = stack.ledger.ran
    assert plan.categories == ("LEISURE",)
    assert plan.period == RelativePeriod(Period.LAST_N_MONTHS, n=3)


def test_the_same_months_after_an_answer_run_it_again() -> None:
    stack = _stack()
    first = ask(stack, "¿cuánto gasté en gasolina en los últimos 3 meses?")
    ran = stack.ledger.ran[-1]
    stack.ledger.ran.clear()
    stack.planner.asked.clear()

    answer = ask(stack, "los mismos 3 meses", first["conversation"])

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert stack.ledger.ran == [ran]


def test_the_same_months_that_contradict_the_last_answer_name_nothing() -> None:
    """«Los mismos 6 meses» after three is not the window of the last answer: not resolved."""
    assert (
        window_words.the_same_window(
            "los mismos 6 meses", RelativePeriod(Period.LAST_N_MONTHS, n=3)
        )
        is None
    )
    assert window_words.the_same_window("los mismos 3 meses", None) is None


# --- the vocabulary underneath -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "months"),
    [
        ("ahora solo julio", (7,)),
        ("pero de julio y agosto", (7, 8)),
        ("los mismos meses, agosto y septiembre", (8, 9)),
        ("en julio y agosto como antes", (7, 8)),
        ("¿y en julio?", (7,)),
        ("julio de 2025", ()),
        ("¿cuánto gasté en julio?", ()),
        ("julio, agosto y septiembre", ()),
        ("el 15 de julio", ()),
    ],
)
def test_a_turn_made_of_months_is_read_only_when_months_are_all_it_says(
    sentence: str, months: tuple[int, ...]
) -> None:
    assert window_words.months_alone(sentence) == months


@pytest.mark.parametrize(
    ("window", "today", "year"),
    [
        (RelativePeriod(Period.LAST_MONTH), date(2027, 1, 10), 2026),
        (RelativePeriod(Period.LAST_MONTH), REAL_RUN, 2026),
        (RelativePeriod(Period.LAST_QUARTER), date(2027, 2, 1), 2026),
        (RelativePeriod(Period.THIS_YEAR), REAL_RUN, 2026),
        (window_words.whole_month(2025, 3), REAL_RUN, 2025),
    ],
)
def test_the_year_in_view_is_the_year_the_window_ends_in(
    window: Any, today: date, year: int
) -> None:
    assert window_words.year_in_view(window, today) == year


def test_a_year_alone_is_a_window_only_as_the_answer_to_the_year_question() -> None:
    assert window_words.window_in("2026", asked_for=Slot.YEAR, today=REAL_RUN) == (
        window_words.whole_year(2026)
    )
    assert window_words.window_in("2026", today=REAL_RUN) is None


# --- round 2, I-1: the year never completes a question about months as the whole year ----------
#
# The review of round 1: a day, three or more months, a range or «en total», and a month still to
# come each fell out of the year completion, and `_finish_pending` ran the whole of 2026 for them.


def _the_year_to(question: str, reply: str) -> tuple[episodes.Stack, dict[str, Any]]:
    """`reply` to the year the model asked about `question`, with only the reply's own reads and
    model calls left to read."""
    stack = _stack()
    asked = _walk(stack, question)
    assert asked["conversation"]["last_asked"] == "YEAR", asked["text"]
    stack.planner.asked.clear()
    return stack, ask(stack, reply, asked["conversation"])


def _ran_once(stack: episodes.Stack) -> tuple[date, date]:
    (plan,) = stack.ledger.ran
    assert isinstance(plan, FinancialPlan) and plan.date_range is not None
    assert stack.planner.asked == [], "no model reads the reply"
    return plan.date_range.start, plan.date_range.end


@pytest.mark.parametrize("reply", ["2026", "este año"])
def test_a_day_is_that_day_of_the_year(reply: str) -> None:
    stack, answer = _the_year_to("¿cuánto gasté el 15 de julio?", reply)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert _ran_once(stack) == (date(2026, 7, 15), date(2026, 7, 15))


def test_days_of_one_month_are_those_days() -> None:
    stack, _ = _the_year_to("¿cuánto gasté del 1 al 15 de julio?", "2026")

    assert _ran_once(stack) == (date(2026, 7, 1), date(2026, 7, 15))


def test_three_months_are_the_span_they_cover() -> None:
    """No reader answers several named months one by one, so the span from the first to the last."""
    stack, answer = _the_year_to("mis gastos de julio, agosto y septiembre", "2026")

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert _ran_once(stack) == (date(2026, 7, 1), date(2026, 9, 30))


@pytest.mark.parametrize(
    "question",
    [
        "¿cuánto gasté de julio a agosto?",
        "¿cuánto gasté entre julio y agosto?",
        "¿cuánto gasté desde julio hasta agosto?",
        "¿cuánto gasté en total en julio y agosto?",
    ],
)
def test_a_range_or_a_total_is_one_window_never_a_comparison(question: str) -> None:
    stack, answer = _the_year_to(question, "2026")

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert answer["conversation"]["last_comparison_earlier"] is None
    assert _ran_once(stack) == (date(2026, 7, 1), date(2026, 8, 31))


def test_two_bare_months_joined_by_y_stay_a_comparison() -> None:
    """Ruling R54."""
    stack, answer = _the_year_to("¿cuánto gasté en julio y agosto?", "2026")

    assert answer["conversation"]["last_comparison_earlier"] is not None
    assert sorted(_months(stack)) == [(2026, 7), (2026, 8)]


@pytest.mark.parametrize(
    ("question", "reply"),
    [
        ("mis gastos de diciembre", "2026"),
        ("mis gastos de diciembre", "este año"),
        ("¿cuánto gasté el 15 de diciembre?", "2026"),
        ("mis gastos de septiembre, octubre y noviembre", "2026"),
    ],
)
def test_a_month_still_to_come_asks_the_year_again(question: str, reply: str) -> None:
    """December 2026 has not happened on 2026-10-08: the year is asked again, never run whole."""
    stack, answer = _the_year_to(question, reply)

    assert answer["outcome"] == "NEEDS_CLARIFICATION", answer["text"]
    assert "año" in answer["text"]
    assert stack.ledger.ran == [] and stack.planner.asked == []
    assert answer["conversation"]["last_asked"] == "YEAR"
    assert answer["conversation"]["workflow"] == "FINANCIAL_CLARIFICATION"

    again = ask(stack, "2025", answer["conversation"])

    assert again["outcome"] == "PLANNED", again["text"]
    (plan,) = stack.ledger.ran
    assert plan.date_range is not None and plan.date_range.start.year == 2025


@pytest.mark.parametrize(
    ("sentence", "windows"),
    [
        ("el 15 de julio", ((date(2026, 7, 15), date(2026, 7, 15)),)),
        ("entre el 1 y el 15 de julio", ((date(2026, 7, 1), date(2026, 7, 15)),)),
        ("del 20 de diciembre al 6 de enero", ((date(2026, 12, 20), date(2027, 1, 6)),)),
        ("julio, agosto y septiembre", ((date(2026, 7, 1), date(2026, 9, 30)),)),
        ("noviembre, diciembre y enero", ((date(2026, 11, 1), date(2027, 1, 31)),)),
        ("septiembre, julio y agosto", ((date(2026, 7, 1), date(2026, 9, 30)),)),
        ("de julio a agosto", ((date(2026, 7, 1), date(2026, 8, 31)),)),
        (
            "agosto y julio",
            ((date(2026, 7, 1), date(2026, 7, 31)), (date(2026, 8, 1), date(2026, 8, 31))),
        ),
        ("el 30 de febrero", ()),
        ("este año", ()),
    ],
)
def test_what_a_year_completes_is_placed_in_that_year(
    sentence: str, windows: tuple[tuple[date, date], ...]
) -> None:
    placed = window_words.windows_in_the_year(sentence, 2026)
    assert tuple((window.start, window.end) for window in placed) == windows


# --- round 2, I-2: a month question that names a category reaches the year completion ----------


@pytest.mark.parametrize("reply", ["este año", "2026"])
def test_a_category_and_months_keep_the_year_question_and_both(reply: str) -> None:
    """«¿cuánto gasté en restaurantes en julio y agosto?» got the window question instead of the
    year, and its draft kept the category but not the months: «este año» dropped them, and «2026»
    got «Sigo sin tener claro el periodo»."""
    stack = _stack()
    asked = _walk(stack, "¿cuánto gasté en restaurantes en julio y agosto?")
    assert asked["conversation"]["last_asked"] == "YEAR", asked["text"]
    assert asked["text"].startswith("¿De qué año?"), asked["text"]
    stack.planner.asked.clear()

    answer = ask(stack, reply, asked["conversation"])

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert stack.planner.asked == []
    assert sorted(_months(stack)) == [(2026, 7), (2026, 8)]
    assert all(plan.categories == ("RESTAURANTS",) for plan in stack.ledger.ran)
