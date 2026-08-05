"""A refusal is this product's decision, not the model's mood.

`capability_guard` is what declines a question — averages, ratios, comparisons, forecasts — and it
runs *before* the model, deterministically. So an `UNSUPPORTED` naming a family the guard does not
have is not a refusal this service makes. It is the model failing, wearing the authority of a rule.

That is the worst kind of wrong answer this pipeline can give. A wrong figure can be checked against
a statement; "no puedo" about something the product does teaches somebody not to ask again, and
nothing ever contradicts it.

Ollama returned exactly that for "¿qué intereses me ha dado la hucha?" — a question whose every part
is in a closed vocabulary this service reads itself.

The recovery is bounded on purpose. The sentence is re-read deterministically and a plan is built
only from what it states outright: a precise category, and a window. It never guesses at a plan the
sentence does not contain, and it never overrides a refusal the guard actually made.
"""

import uuid
from datetime import UTC, date, datetime

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 5)


class GivesUp:
    """A planner that refuses without naming a family the guard owns."""

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason
        self.asked = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.asked += 1
        return PlanningResult(outcome=PlanningOutcome.UNSUPPORTED, reason=self.reason)


class Ledger:
    def __init__(self) -> None:
        self.plan: FinancialPlan | None = None

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plan = plan
        return QueryAnswer(
            currency="EUR",
            total_minor=5_250,
            movement_count=7,
            groups=(Group(key="total", total_minor=5_250, movement_count=7),),
            movements=(),
        )


class Bank:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=227_042,
            available_minor=227_042,
            as_of=datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
            holdings=(
                Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),
                Holding(uuid.UUID(int=2), "SAVINGS", "Hucha", "EUR", 548_550),
            ),
        )


async def asked(sentence: str, planner: GivesUp, ledger: Ledger | None = None):  # type: ignore[no-untyped-def]
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        ledger or Ledger(),  # type: ignore[arg-type]
        accounts=Bank(),  # type: ignore[arg-type]
        contacts=None,  # type: ignore[arg-type]
    )
    return await handler.handle(
        AskFinancialQuestionQuery(sentence=sentence, account_id=ACCOUNT, today=TODAY)
    )


# --- what gets rescued ------------------------------------------------------------------


async def test_a_sentence_that_states_a_plan_is_answered_despite_the_model() -> None:
    """The reported case. Every part of it is in a vocabulary this service owns, so a refusal was
    never the truth about it."""
    ledger = Ledger()

    answer = await asked("¿qué intereses me ha dado la hucha este año?", GivesUp(), ledger)

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert answer.total_minor == 5_250
    assert ledger.plan is not None
    assert ledger.plan.categories == ("INTEREST",)


async def test_interest_is_read_as_money_coming_in() -> None:
    """From what the category *is*, not from a verb. "¿Qué intereses me ha dado?" carries no word
    of spending or earning that this service reads, and filing interest under EXPENSE would answer
    zero — a real figure, about the opposite of what was asked."""
    ledger = Ledger()

    await asked("¿qué intereses me ha dado la hucha este año?", GivesUp(), ledger)

    assert ledger.plan is not None
    assert ledger.plan.direction is Direction.INCOME


async def test_the_window_the_sentence_named_is_the_window_it_runs_over() -> None:
    ledger = Ledger()

    await asked("¿cuánto gasté en gasolina el mes pasado?", GivesUp(), ledger)

    assert ledger.plan is not None
    assert ledger.plan.period is not None
    assert ledger.plan.period.period is Period.LAST_MONTH
    assert ledger.plan.direction is Direction.EXPENSE


async def test_a_category_with_no_period_is_asked_about_rather_than_refused() -> None:
    """What is missing is a window, and saying "no puedo calcularlo" instead is a capability limit
    that does not exist — it teaches somebody the product cannot do something it does."""
    answer = await asked("¿qué intereses me ha dado la hucha?", GivesUp())

    assert answer.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert "periodo" in answer.text
    assert answer.conversation.awaiting is Slot.WINDOW


# --- what must still be refused ----------------------------------------------------------


@pytest.mark.parametrize("family", ["AVERAGE", "RATIO", "COMPARISON"])
async def test_a_refusal_the_guard_owns_is_left_alone(family: str) -> None:
    """The rescue reads the model's reason and stops when it names a real family. Overriding one of
    these would be the pipeline talking itself out of a decision the product made on purpose."""
    answer = await asked("¿cuánto gasté en gasolina el mes pasado?", GivesUp(family))

    assert answer.outcome == PlanningOutcome.UNSUPPORTED.value


async def test_the_guard_still_refuses_before_the_model_is_asked() -> None:
    """An average is declined by the deterministic gate, so the planner is never called and there
    is nothing to rescue. None of this weakened that."""
    planner = GivesUp()

    answer = await asked("¿cuál es mi gasto medio en gasolina el mes pasado?", planner)

    assert answer.outcome == PlanningOutcome.UNSUPPORTED.value
    assert planner.asked == 0, "the guard let an average reach the model"


async def test_a_sentence_stating_nothing_certain_keeps_the_refusal() -> None:
    """No category this service is sure of, so there is no plan in the sentence to rescue. The
    decline stands, which is the honest outcome — this recovers a plan the words contain and never
    invents one."""
    answer = await asked("¿qué tal va la cosa este mes?", GivesUp())

    assert answer.outcome == PlanningOutcome.UNSUPPORTED.value
