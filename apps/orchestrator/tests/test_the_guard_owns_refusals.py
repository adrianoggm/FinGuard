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
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation, Slot
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod


class NoCards:
    """The card directory. Required like the balance and the address book, so no build can be
    half-wired — a chat that cannot reach the wallet answered "eso no puedo calcularlo todavía"
    about a screen the user can open."""

    async def all_of(self, account_id, **kwargs):
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=__import__("datetime").date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


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


class ListingLedger(Ledger):
    def __init__(self) -> None:
        super().__init__()
        self.requested_movements: int | None = None

    async def run(
        self, plan: FinancialPlan, *, movements: int | None = None, **_: object
    ) -> QueryAnswer:
        self.plan = plan
        self.requested_movements = movements
        available = tuple(
            Movement(
                occurred_at=datetime(2026, 8, day, 10, 0, tzinfo=UTC),
                description=f"Movimiento {day}",
                amount_minor=day * 100,
                direction="EXPENSE",
                merchant_name=f"Comercio {day}",
            )
            for day in range(5, 0, -1)
        )
        return QueryAnswer(
            currency="EUR",
            total_minor=1_500,
            movement_count=100,
            movements=available[:movements],
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


async def asked(
    sentence: str,
    planner: GivesUp,
    ledger: Ledger | None = None,
    conversation: Conversation = IDLE,
):  # type: ignore[no-untyped-def]
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        ledger or Ledger(),  # type: ignore[arg-type]
        accounts=Bank(),  # type: ignore[arg-type]
        contacts=None,
        cards=NoCards(),  # type: ignore[arg-type]
    )
    return await handler.handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=conversation
        )
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


async def test_a_plain_average_is_planned_before_the_legacy_guard_or_model() -> None:
    """A bare category average now has one useful meaning: amount per matching movement."""
    planner = GivesUp()
    ledger = Ledger()

    answer = await asked("¿cuál es mi gasto medio en gasolina el mes pasado?", planner, ledger)

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.AVERAGE


async def test_latest_five_movements_means_five_rows_not_five_days() -> None:
    planner = GivesUp()
    ledger = ListingLedger()

    answer = await asked(
        "Perdona, ¿cuáles son los utlimos 5 movimientos de mis cuentas?", planner, ledger
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.requested_movements == 5
    assert answer.movement_count == 5
    assert len(answer.movements) == 5
    assert "últimos 5 movimientos" in answer.text


@pytest.mark.parametrize(
    "sentence",
    ["Dame un ejemplo de esos movimientos", "Dame un ejemlo de esos movimientos"],
)
async def test_an_example_reuses_the_previous_query(sentence: str) -> None:
    planner = GivesUp()
    ledger = ListingLedger()
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_N_MONTHS, n=3),
        group_by=GroupBy.MERCHANT,
        metric=Metric.COUNT,
    )

    answer = await asked(
        sentence,
        planner,
        ledger,
        Conversation(last_plan=previous),
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert planner.asked == 0
    assert ledger.requested_movements == 1
    assert answer.movement_count == 1
    assert answer.text.startswith("Por ejemplo: Comercio 5")
    assert answer.conversation.last_plan == previous


@pytest.mark.parametrize("sentence", ["Compáralos", "¿En cuál gasté más?"])
async def test_two_consecutive_period_queries_can_be_compared_naturally(sentence: str) -> None:
    class PeriodLedger(Ledger):
        def __init__(self) -> None:
            super().__init__()
            self.plans: list[FinancialPlan] = []

        async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
            self.plans.append(plan)
            assert plan.period is not None
            total = 8_000 if plan.period.period is Period.LAST_MONTH else 11_500
            return QueryAnswer(currency="EUR", total_minor=total, movement_count=4)

    earlier = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("RESTAURANTS",),
        group_by=GroupBy.MERCHANT,
    )
    latest = replace(earlier, period=RelativePeriod(Period.THIS_MONTH), group_by=GroupBy.NONE)
    conversation = Conversation(last_plan=latest, prior_plan=earlier)
    planner = GivesUp()
    ledger = PeriodLedger()

    answer = await asked(sentence, planner, ledger, conversation)

    assert planner.asked == 0
    assert len(ledger.plans) == 2
    assert all(plan.metric is Metric.SUM and plan.group_by is GroupBy.NONE for plan in ledger.plans)
    assert "115,00 €" in answer.text
    assert "80,00 €" in answer.text
    assert "35,00 € mayor" in answer.text
    assert answer.conversation.last_plan == latest
    assert answer.conversation.prior_plan == earlier


async def test_a_model_cannot_refuse_an_explicit_supported_average_per_movement() -> None:
    ledger = Ledger()

    answer = await asked(
        "¿Cuál fue el importe medio de las compras de gasolina el mes pasado?",
        GivesUp("AVERAGE"),
        ledger,
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plan is not None
    assert ledger.plan.metric is Metric.AVERAGE


async def test_a_model_cannot_refuse_a_supported_category_comparison() -> None:
    ledger = Ledger()

    answer = await asked(
        "Compara gasolina frente a restaurantes el mes pasado",
        GivesUp("COMPARISON"),
        ledger,
    )

    assert answer.outcome == PlanningOutcome.PLANNED.value
    assert ledger.plan is not None
    assert ledger.plan.categories == ("TRANSPORT_FUEL", "RESTAURANTS")
    assert ledger.plan.group_by is GroupBy.CATEGORY


async def test_a_sentence_stating_nothing_certain_keeps_the_refusal() -> None:
    """No category this service is sure of, so there is no plan in the sentence to rescue. The
    decline stands, which is the honest outcome — this recovers a plan the words contain and never
    invents one."""
    answer = await asked("¿qué tal va la cosa este mes?", GivesUp())

    assert answer.outcome == PlanningOutcome.UNSUPPORTED.value
