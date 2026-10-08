"""«¿Y la anterior?» is the period before the one just answered, in the same unit.

Validation fix 1, F6. After «¿y cuánto he gastado esta semana?», «y la anterior?» went to the model,
which asked «¿De qué periodo te refieres?» and offered «esta semana» as an example
(web-saldo-semana 3). «¿Y el mes anterior?» already worked on one answer, through the follow-up
reader's calendar step back; with no unit said, nothing read the turn at all.

Now a turn that only says «the one before» — «y la anterior», «y el anterior», «¿y la semana
anterior?», «¿y el mes anterior?» — after an answer whose window is a calendar week, month or year
(this one or the last) is the period before it, with everything else kept. The reading is the
follow-up patch's (`continuation_patch`), and the shapes that name their unit stay with the reader
that already answered them, which now steps back from last week too.

Driven over HTTP on the episodes stack, with a planner that asks the period the way the real model
did, so only the deterministic path can make these pass.
"""

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from support import episodes
from support.pending import SETTLED, _with_planner, ask

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import continuation_patch, spanish, window_words
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)

# The episodes stack's day, a Wednesday: this week began on Monday 3 August 2026.
MONDAY = episodes.TODAY - timedelta(days=episodes.TODAY.weekday())


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class AsksWhichPeriod(episodes.AnyPlan):
    """The real model on «y la anterior?» (2026-10-08): it asked which period was meant."""

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        if "anterior" not in spanish.folded(sentence):
            return await super().plan(sentence, context=context)
        self.asked.append(sentence)
        return PlanningResult.needs_clarification(
            "¿De qué periodo te refieres? Por ejemplo, el mes pasado, esta semana o los últimos "
            "30 días.",
            FinancialPlanDraft(direction=Direction.EXPENSE),
            Slot.WINDOW,
        )


def _after(*turns: str) -> tuple[episodes.Stack, dict[str, Any]]:
    stack = episodes.a_stack()
    planner = AsksWhichPeriod()
    _with_planner(stack, planner)
    stack.planner = planner
    conversation: dict[str, Any] | None = SETTLED
    for turn in turns[:-1]:
        conversation = ask(stack, turn, conversation)["conversation"]
    stack.ledger.plans.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, turns[-1], conversation)


def _window(stack: episodes.Stack) -> Any:
    (plan,) = stack.ledger.plans
    assert isinstance(plan, FinancialPlan)
    assert stack.planner.asked == [], "no model reads the turn"
    return plan.period or plan.date_range


def test_the_week_before_this_one() -> None:
    """web-saldo-semana 2 and 3, as said: a balance, this week's spending, «y la anterior?»."""
    stack, answer = _after(
        "cuanto me queda en la corriente?", "y cuanto e gastado esta semana?", "y la anterior?"
    )

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert _window(stack) == RelativePeriod(Period.LAST_WEEK)
    assert stack.ledger.plans[0].direction is Direction.EXPENSE


@pytest.mark.parametrize(
    ("opening", "sentence", "before"),
    [
        (
            "¿cuánto gasté en restaurantes este mes?",
            "y el anterior",
            RelativePeriod(Period.LAST_MONTH),
        ),
        (
            "¿cuánto gasté en restaurantes el mes pasado?",
            "¿y el anterior?",
            window_words.whole_month(2026, 6),
        ),
        (
            "¿cuánto gasté en restaurantes en julio?",
            "y el anterior?",
            window_words.whole_month(2026, 6),
        ),
        (
            "¿cuánto gasté en restaurantes este año?",
            "¿y el anterior?",
            window_words.whole_year(2025),
        ),
        (
            "¿cuánto gasté en restaurantes la semana pasada?",
            "¿y la anterior?",
            AbsoluteRange(MONDAY - timedelta(days=14), MONDAY - timedelta(days=8)),
        ),
    ],
)
def test_the_period_before_keeps_everything_else(opening: str, sentence: str, before: Any) -> None:
    stack, answer = _after(opening, sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert _window(stack) == before
    assert stack.ledger.plans[0].categories == ("RESTAURANTS",)


@pytest.mark.parametrize(
    ("opening", "sentence", "before"),
    [
        (
            "¿cuánto gasté en restaurantes la semana pasada?",
            "¿y la semana anterior?",
            AbsoluteRange(MONDAY - timedelta(days=14), MONDAY - timedelta(days=8)),
        ),
        (
            "¿cuánto gasté en restaurantes esta semana?",
            "¿y la semana anterior?",
            RelativePeriod(Period.LAST_WEEK),
        ),
        (
            "¿cuánto gasté en restaurantes el mes pasado?",
            "¿y el mes anterior?",
            window_words.whole_month(2026, 6),
        ),
        (
            "¿cuánto gasté en restaurantes este mes?",
            "¿y el mes anterior?",
            RelativePeriod(Period.LAST_MONTH),
        ),
    ],
)
def test_the_unit_said_steps_back_the_same_way(opening: str, sentence: str, before: Any) -> None:
    """«¿Y la semana anterior?» after last week was last week again: the step back the follow-up
    reader takes did not know last week. «¿Y el mes anterior?» is pinned where it already worked."""
    stack, answer = _after(opening, sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    assert _window(stack) == before


@pytest.mark.parametrize(
    ("opening", "sentence"),
    [
        # No calendar unit to step back in.
        ("¿cuánto gasté en restaurantes en los últimos 30 días?", "y la anterior?"),
        # «La» is no month, «el» no week: the turn may mean something else, read as before.
        ("¿cuánto gasté en restaurantes este mes?", "y la anterior?"),
        ("¿cuánto gasté en restaurantes esta semana?", "y el anterior?"),
    ],
)
def test_no_period_before_where_the_answer_has_no_unit_that_agrees(
    opening: str, sentence: str
) -> None:
    stack, _ = _after(opening, sentence)

    assert stack.planner.asked == [sentence]


def test_the_reading_is_the_patchs_and_needs_the_answer() -> None:
    """Without the answer it continues there is no unit to step back in."""
    base = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_WEEK))
    read = continuation_patch.read("y la anterior?", today=date(2026, 10, 8), base=base)
    assert read.patch is not None and read.patch.window == RelativePeriod(Period.LAST_WEEK)
    assert continuation_patch.read("y la anterior?", today=date(2026, 10, 8)).patch is None
