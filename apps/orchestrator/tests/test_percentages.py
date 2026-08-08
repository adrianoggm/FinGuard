"""Percentages are relationships between two verified ledger reads, never model arithmetic."""

import uuid
from collections.abc import Callable
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Metric, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation, Slot
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 8)


class ModelMustNotPlan:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.calls += 1
        raise AssertionError("a supported percentage must not be calculated by the model")


class Ledger:
    def __init__(self, answer_for: Callable[[FinancialPlan], QueryAnswer]) -> None:
        self.answer_for = answer_for
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return self.answer_for(plan)


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=100_000,
            available_minor=100_000,
            as_of=datetime(2026, 8, 8, 10, 0),
        )


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class Cards:
    async def all_of(self, *_: object, **__: object):  # type: ignore[no-untyped-def]
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


async def ask(
    sentence: str,
    ledger: Ledger,
    *,
    conversation: Conversation = IDLE,
):  # type: ignore[no-untyped-def]
    planner = ModelMustNotPlan()
    result = await AskFinancialQuestionHandler(
        planner,
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        contacts=Contacts(),
        cards=Cards(),  # type: ignore[arg-type]
    ).handle(
        AskFinancialQuestionQuery(
            sentence=sentence,
            account_id=ACCOUNT,
            today=TODAY,
            conversation=conversation,
        )
    )
    return result, planner


def answer(total: int, count: int = 1) -> QueryAnswer:
    return QueryAnswer(currency="EUR", total_minor=total, movement_count=count)


async def test_a_category_can_be_related_to_salary_in_one_sentence() -> None:
    ledger = Ledger(
        lambda plan: answer(50_000) if plan.direction is Direction.EXPENSE else answer(250_000)
    )

    result, planner = await ask("¿Qué porcentaje de mi nómina se va en alquiler este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "20,00 %" in result.text
    assert "500,00 €" in result.text
    assert "2.500,00 €" in result.text
    assert planner.calls == 0
    assert [plan.categories for plan in ledger.plans] == [("HOUSING_RENT",), ("SALARY",)]
    assert result.conversation.last_ratio is not None
    assert result.conversation.last_ratio.kind is RatioKind.SHARE


async def test_a_category_can_be_related_to_total_expenses() -> None:
    ledger = Ledger(lambda plan: answer(25_000) if plan.categories else answer(100_000))

    result, _ = await ask("¿Qué porcentaje de mis gastos fue supermercado este mes?", ledger)

    assert "25,00 %" in result.text
    assert ledger.plans[0].categories == ("GROCERIES",)
    assert ledger.plans[1].categories == ()
    assert all(plan.direction is Direction.EXPENSE for plan in ledger.plans)


async def test_a_missing_window_is_asked_once_and_the_short_reply_completes_both_reads() -> None:
    ledger = Ledger(
        lambda plan: answer(46_000) if plan.direction is Direction.EXPENSE else answer(230_000)
    )

    first, planner = await ask("¿Qué parte de mi sueldo se va en alquiler?", ledger)

    assert first.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert first.conversation.last_asked is Slot.WINDOW
    assert first.conversation.pending_ratio is not None
    assert ledger.plans == []
    assert planner.calls == 0

    second, _ = await ask("Este año", ledger, conversation=first.conversation)

    assert second.outcome == PlanningOutcome.PLANNED.value
    assert "20,00 %" in second.text
    assert len(ledger.plans) == 2
    assert all(plan.period == RelativePeriod(Period.THIS_YEAR) for plan in ledger.plans)


async def test_a_new_question_with_a_window_does_not_complete_a_stale_ratio() -> None:
    ledger = Ledger(lambda plan: answer(20_000) if plan.categories else answer(100_000))
    pending, _ = await ask("¿Qué parte de mi sueldo se va en alquiler?", ledger)

    result, planner = await ask(
        "¿Qué porcentaje de mis gastos fue supermercado este año?",
        ledger,
        conversation=pending.conversation,
    )

    assert result.conversation.pending_ratio is None
    assert "20,00 %" in result.text
    assert ledger.plans[0].categories == ("GROCERIES",)
    assert planner.calls == 0


async def test_the_savings_rate_uses_total_income_and_total_expenses() -> None:
    ledger = Ledger(
        lambda plan: answer(240_000) if plan.direction is Direction.EXPENSE else answer(300_000)
    )

    result, planner = await ask("¿Cuál fue mi tasa de ahorro este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "20,00 %" in result.text
    assert "3.000,00 €" in result.text
    assert "2.400,00 €" in result.text
    assert planner.calls == 0


async def test_a_percentage_change_can_follow_two_compatible_queries() -> None:
    june = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)),
    )
    july = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
    )
    conversation = Conversation(last_plan=july, prior_plan=june)
    ledger = Ledger(
        lambda plan: (
            answer(125_000)
            if plan.date_range and plan.date_range.start.month == 7
            else answer(100_000)
        )
    )

    result, planner = await ask("¿Y en porcentaje?", ledger, conversation=conversation)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "aumentó un 25,00 %" in result.text
    assert planner.calls == 0
    assert result.conversation.last_ratio is not None
    assert result.conversation.last_ratio.kind is RatioKind.PERCENT_CHANGE


async def test_a_percentage_change_can_name_both_months_directly() -> None:
    ledger = Ledger(
        lambda plan: (
            answer(80_000)
            if plan.date_range and plan.date_range.start.month == 7
            else answer(100_000)
        )
    )

    result, planner = await ask("¿En qué porcentaje gasté menos en julio que en junio?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "disminuyó un 20,00 %" in result.text
    assert planner.calls == 0


async def test_a_percentage_change_preserves_a_requested_movement_count() -> None:
    ledger = Ledger(
        lambda plan: (
            answer(999_999, 6)
            if plan.date_range and plan.date_range.start.month == 7
            else answer(1, 4)
        )
    )

    result, planner = await ask(
        "¿En qué porcentaje hice más movimientos en julio que en junio?", ledger
    )

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "número de movimientos aumentó un 50,00 %" in result.text
    assert all(plan.metric is Metric.COUNT for plan in ledger.plans)
    assert planner.calls == 0


async def test_zero_income_is_explained_instead_of_dividing_by_zero() -> None:
    ledger = Ledger(
        lambda plan: answer(10_000) if plan.direction is Direction.EXPENSE else answer(0, 0)
    )

    result, _ = await ask("¿Cuál fue mi tasa de ahorro este mes?", ledger)

    assert "No hubo ingresos" in result.text
    assert "no está definida" in result.text
