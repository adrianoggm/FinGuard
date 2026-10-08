"""A year said over months in view moves them, months said with their year are those months, and
«como antes» brings the year in view.

Validation fix 3, G5. On 2026-10-08:

- «2026», said with nothing open after a comparison of August and September (comparar-meses 5),
  was asked «¿De qué año te refieres?» — the year asked straight after it was said;
- «agosto y septiembre de 2026» after a comparison of both (comparar-meses 8) answered September
  alone: the follow-up reader read «septiembre de 2026» as the one window and dropped August;
- «junio y julio, como antes», the reply to «¿De qué periodo…?» (impro-comparar-meses 5), was asked
  the year although «como antes» was 2026.

Now a bare year over months in view moves them to that year, both sides of a comparison; months
said with their year, as a reply or a follow-up, are those months (compared, R54, or one span when
a range word joins them); and «como antes» brings the year of the window in view, or that window
itself when no month is said.

Driven over HTTP on the episodes stack (today 2026-08-05), with a planner that asks the window and
never reads the months (`AsksForTheWindow`), so only the deterministic reading can pass.
"""

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from support.pending import SETTLED, AsksForTheWindow, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import category_words, window_words
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
)

JUNE_AND_JULY = "compara mis gastos en el super de junio y julio"


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _after(*turns: str) -> tuple[Any, dict[str, Any]]:
    stack = asking_for_the_window_stack()
    conversation: dict[str, Any] | None = SETTLED
    for turn in turns[:-1]:
        conversation = ask(stack, turn, conversation)["conversation"]
    stack.ledger.plans.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, turns[-1], conversation)


def _ran(stack: Any, *windows: tuple[date, date], category: str) -> None:
    """These windows ran, earliest first, in `category`, with no model asked."""
    plans: list[FinancialPlan] = stack.ledger.plans
    assert sorted(
        (plan.date_range.start, plan.date_range.end) for plan in plans if plan.date_range
    ) == list(windows), plans
    assert len(plans) == len(windows), plans
    assert all(plan.categories == (category,) for plan in plans), plans
    assert stack.planner.asked == [], "no model asked, and no year asked"


def _month(year: int, month: int) -> tuple[date, date]:
    window = window_words.whole_month(year, month)
    return window.start, window.end


# --- a bare year over the months in view ----------------------------------------------------------


@pytest.mark.parametrize(("said", "year"), [("2026", 2026), ("2025", 2025), ("¿y el 2025?", 2025)])
def test_a_bare_year_moves_both_sides_of_a_comparison(said: str, year: int) -> None:
    """comparar-meses 5: the same months, in the year said, never «¿De qué año…?»."""
    stack, answer = _after(JUNE_AND_JULY, said)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, _month(year, 6), _month(year, 7), category="GROCERIES")


def test_a_bare_year_moves_one_month() -> None:
    stack, answer = _after("¿cuánto gasté en restaurantes en julio?", "2025")

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, _month(2025, 7), category="RESTAURANTS")


def test_a_bare_year_moves_a_range_of_months() -> None:
    stack, answer = _after("¿cuánto gasté en restaurantes de mayo a julio de 2026?", "2025")

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, (date(2025, 5, 1), date(2025, 7, 31)), category="RESTAURANTS")


ACROSS_NEW_YEAR = "compara diciembre de 2025 con enero de 2026 en restaurantes"


@pytest.mark.parametrize(
    ("said", "december", "january"), [("2024", 2023, 2024), ("2025", 2024, 2025)]
)
def test_a_bare_year_moves_a_comparison_across_new_year_whole(
    said: str, december: int, january: int
) -> None:
    """Fix round 1, I-3: one shift for the pair, taken from its later side, so December stays the
    month before January — never December and January of one year, the later one written first."""
    stack, answer = _after(ACROSS_NEW_YEAR, said)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, _month(december, 12), _month(january, 1), category="RESTAURANTS")
    assert answer["text"].startswith(f"En diciembre de {december}: "), answer["text"]


def test_the_percentage_of_a_moved_comparison_runs_from_december() -> None:
    """«En porcentaje» after it reads the change from December to the January after it."""
    _, answer = _after(ACROSS_NEW_YEAR, "2025", "en porcentaje")

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert "en diciembre de 2024 a " in answer["text"], answer["text"]
    assert answer["text"].rstrip().endswith("en enero de 2025."), answer["text"]


def test_a_bare_year_after_a_percentage_is_that_percentage_in_the_year() -> None:
    """Fix round 1, M-2: after the percentage of June to July, «2025» is the percentage of June to
    July 2025, never a plain comparison of them."""
    stack, answer = _after(JUNE_AND_JULY, "en porcentaje", "2025")

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert answer["text"].startswith("El gasto "), answer["text"]
    assert "en junio de 2025 a " in answer["text"], answer["text"]
    _ran(stack, _month(2025, 6), _month(2025, 7), category="GROCERIES")


def test_a_year_that_has_not_reached_the_months_moves_nothing() -> None:
    """July 2027 has not happened: never a figure for it."""
    stack, _ = _after("¿cuánto gasté en restaurantes en julio?", "2027")

    assert not any(
        plan.date_range is not None and plan.date_range.start.year == 2027
        for plan in stack.ledger.plans
    )


# --- months with their year -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "year"),
    [
        ("junio y julio de 2026", 2026),
        ("junio y julio de 2025", 2025),
        ("junio y julio 2025", 2025),
    ],
)
def test_two_months_with_their_year_after_a_comparison_are_compared(said: str, year: int) -> None:
    """comparar-meses 8: both months, never the second one alone."""
    stack, answer = _after(JUNE_AND_JULY, "y en ocio?", said)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, _month(year, 6), _month(year, 7), category="LEISURE")


@pytest.mark.parametrize(
    "said",
    ["de junio a julio de 2025", "entre junio y julio de 2025", "junio y julio de 2025 en total"],
)
def test_two_months_with_their_year_joined_by_a_range_word_are_one_span(said: str) -> None:
    stack, answer = _after(JUNE_AND_JULY, said)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, (date(2025, 6, 1), date(2025, 7, 31)), category="GROCERIES")


@pytest.mark.parametrize(
    ("said", "windows"),
    [
        ("junio y julio de 2026", (_month(2026, 6), _month(2026, 7))),
        ("entre junio y julio de 2026", ((date(2026, 6, 1), date(2026, 7, 31)),)),
    ],
)
def test_two_months_with_their_year_as_the_reply(
    said: str, windows: tuple[tuple[date, date], ...]
) -> None:
    """The reply to «¿De qué periodo…?»: the open question over both months, or over their span."""
    stack, answer = _after("¿cuánto gasté en ocio?", said)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, *windows, category="LEISURE")
    assert answer["conversation"]["awaiting"] is None


# --- «como antes» -----------------------------------------------------------------------------


def test_the_months_as_before_take_the_year_in_view_as_the_reply() -> None:
    """impro-comparar-meses 5: the months said, in 2026, the year on screen — never «¿De qué
    año?»."""
    stack, answer = _after(
        "junio vs julio en restaurantes", "¿cuánto gasté en ocio?", "junio y julio, como antes"
    )

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, _month(2026, 6), _month(2026, 7), category="LEISURE")
    assert answer["conversation"]["awaiting"] is None


def test_as_before_alone_is_the_pair_in_view_as_the_reply() -> None:
    """«Como antes» and no month: the windows in view, here the two of the comparison."""
    stack, answer = _after("junio vs julio en restaurantes", "¿cuánto gasté en ocio?", "como antes")

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, _month(2026, 6), _month(2026, 7), category="LEISURE")


def test_the_months_as_before_after_a_percentage_take_its_year() -> None:
    """With the percentage of June to July on screen, the reply's months are 2026's."""
    stack, answer = _after(
        "junio vs julio en restaurantes",
        "en porcentaje",
        "¿cuánto gasté en ocio?",
        "junio y julio, como antes",
    )

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, _month(2026, 6), _month(2026, 7), category="LEISURE")


# --- the vocabulary -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "windows"),
    [
        (
            "agosto y septiembre de 2026",
            (window_words.whole_month(2026, 8), window_words.whole_month(2026, 9)),
        ),
        ("julio del 2025", (window_words.whole_month(2025, 7),)),
        (
            "de agosto a septiembre de 2026",
            (AbsoluteRange(start=date(2026, 8, 1), end=date(2026, 9, 30)),),
        ),
        ("agosto y septiembre", ()),
        ("agosto de 2025 y septiembre de 2026", ()),
        ("cuanto gaste en agosto de 2026", ()),
    ],
)
def test_the_months_a_turn_says_with_their_year(
    sentence: str, windows: tuple[AbsoluteRange, ...]
) -> None:
    """Only a whole turn of months and one year: two years, or a question, are not this."""
    assert window_words.the_months_with_their_year(sentence) == windows


@pytest.mark.parametrize(
    ("sentence", "year"),
    [("2026", 2026), ("el 2025", 2025), ("¿y del 2025?", 2025), ("en 2025", None), ("20", None)],
)
def test_a_year_said_alone(sentence: str, year: int | None) -> None:
    """«En 2025» is a window the follow-up reader already reads, so it is not a bare year."""
    assert window_words.a_year_alone(sentence) == year


# --- fix round 1, M-5: «como antes» as the reply to «¿De qué año?» ------------------------------


class AsksTheYear(AsksForTheWindow):
    """The real model: two months named with no year are asked their year — «¿De qué año? Por
    ejemplo 2025 o 2026.» — with the subject on the draft. Anything else is `AsksForTheWindow`'s."""

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        if not window_words.months_without_a_year(sentence) or "20" in sentence:
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


def test_as_before_answers_the_year_with_the_year_in_view() -> None:
    """With an answer about 2025 on screen, «como antes» to «¿De qué año?» is 2025: July and August
    of 2025 in leisure, compared — never the year asked again, never the whole year."""
    stack = asking_for_the_window_stack(planner=AsksTheYear())
    conversation = SETTLED
    for turn in (
        "¿cuánto gasté en restaurantes en julio de 2025?",
        "¿cuánto gasté en ocio en julio y agosto?",
    ):
        conversation = ask(stack, turn, conversation)["conversation"]
    assert conversation["awaiting"] == "YEAR"
    stack.ledger.plans.clear()
    stack.planner.asked.clear()

    answer = ask(stack, "como antes", conversation)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _ran(stack, _month(2025, 7), _month(2025, 8), category="LEISURE")
