"""Income scoped to one account, then compared naturally year over year."""

import uuid
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation, Slot
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
SAVINGS = uuid.UUID("22222222-2222-2222-2222-222222222222")
TODAY = date(2026, 8, 9)


class NoModel:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, *_: object, **__: object):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("this conversation is fully represented by typed plans")


class Ledger:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        if plan.group_by is GroupBy.ACCOUNT:
            current = plan.period is not None and plan.period.period is Period.THIS_YEAR
            groups = (
                (
                    Group("Cuenta corriente", 1_763_692, 13),
                    Group("Hucha", 5_250, 7),
                )
                if current
                else (
                    Group("Cuenta corriente", 1_600_000, 12),
                    Group("Hucha", 4_200, 6),
                )
            )
            return QueryAnswer(
                currency="EUR",
                total_minor=sum(group.total_minor for group in groups),
                movement_count=sum(group.movement_count for group in groups),
                groups=groups,
            )
        assert plan.only_account_id == SAVINGS
        return QueryAnswer(currency="EUR", total_minor=5_250, movement_count=7)


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=227_042,
            available_minor=227_042,
            as_of=datetime(2026, 8, 9, 10, 0),
            holdings=(
                Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),
                Holding(SAVINGS, "SAVINGS", "Hucha", "EUR", 548_550),
            ),
        )


async def say(
    sentence: str,
    *,
    conversation: Conversation = IDLE,
    planner: NoModel,
    ledger: Ledger,
):  # type: ignore[no-untyped-def]
    return await AskFinancialQuestionHandler(
        planner,
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        contacts=None,
        cards=None,
    ).handle(
        AskFinancialQuestionQuery(
            sentence=sentence,
            account_id=ACCOUNT,
            today=TODAY,
            conversation=conversation,
        )
    )


async def test_a_period_reply_keeps_the_piggy_bank_named_before_it() -> None:
    planner, ledger = NoModel(), Ledger()

    first = await say(
        "Cuantos ingresos anuales me da la hucha sacame los movimientos si tienen",
        planner=planner,
        ledger=ledger,
    )

    assert first.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert first.conversation.awaiting is Slot.WINDOW
    assert first.conversation.active is not None
    assert first.conversation.active.draft.direction is Direction.INCOME
    assert first.conversation.active.draft.metric is Metric.SUM
    assert first.conversation.active.draft.only_account_id == SAVINGS

    answered = await say(
        "Este año", conversation=first.conversation, planner=planner, ledger=ledger
    )

    assert answered.outcome == PlanningOutcome.PLANNED.value
    assert "52,50 €" in answered.text
    assert ledger.plans[-1].only_account_id == SAVINGS
    assert ledger.plans[-1].direction is Direction.INCOME
    assert planner.calls == 0


async def test_the_reported_account_breakdown_compares_with_the_previous_year() -> None:
    planner, ledger = NoModel(), Ledger()
    first = await say(
        "Cuantos ingresos anuales me da la hucha sacame los movimientos si tienen",
        planner=planner,
        ledger=ledger,
    )
    income = await say("Este año", conversation=first.conversation, planner=planner, ledger=ledger)
    breakdown = await say(
        "Pero no me lo has desglosado por cuenta",
        conversation=income.conversation,
        planner=planner,
        ledger=ledger,
    )

    compared = await say(
        "Vale genial me lo podrias comparar con el año anterior",
        conversation=breakdown.conversation,
        planner=planner,
        ledger=ledger,
    )

    assert compared.outcome == PlanningOutcome.PLANNED.value
    assert "Comparación del ingreso por cuenta" in compared.text
    assert "Cuenta corriente" in compared.text
    assert "Hucha" in compared.text
    assert "en 2025" in compared.text
    assert len(ledger.plans) == 4
    assert all(plan.group_by is GroupBy.ACCOUNT for plan in ledger.plans[-2:])
    assert all(plan.direction is Direction.INCOME for plan in ledger.plans[-2:])
    assert planner.calls == 0


async def test_a_complete_mixed_language_comparison_keeps_the_account_dimension() -> None:
    planner, ledger = NoModel(), Ledger()

    compared = await say(
        "Compara este año income de cada cuenta con el año anterior",
        planner=planner,
        ledger=ledger,
    )

    assert compared.outcome == PlanningOutcome.PLANNED.value
    assert "Comparación del ingreso por cuenta" in compared.text
    assert all(plan.group_by is GroupBy.ACCOUNT for plan in ledger.plans)
    assert all(plan.direction is Direction.INCOME for plan in ledger.plans)
    assert planner.calls == 0
