"""History of own-account transfers is a query, not spending and not a new payment."""

import uuid
from datetime import date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation, Slot
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
SAVINGS = uuid.UUID(int=2)
ASSET = uuid.UUID(int=3)
TODAY = date(2026, 8, 9)


class Planner:
    def __init__(self, fallback: FinancialPlan | None = None) -> None:
        self.calls = 0
        self.fallback = fallback

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.calls += 1
        if self.fallback is None:
            raise AssertionError("a typed internal query must not reach the model")
        return PlanningResult.planned(self.fallback)


class Ledger:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        return QueryAnswer(currency="EUR", total_minor=60_000, movement_count=3)


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=100_000,
            available_minor=100_000,
            as_of=datetime(2026, 8, 9, 10, 0),
            holdings=(
                Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 100_000),
                Holding(SAVINGS, "SAVINGS", "Hucha", "EUR", 50_000),
                Holding(ASSET, "ASSET", "Fondo indexado", "EUR", 25_000),
            ),
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
    planner: Planner | None = None,
):  # type: ignore[no-untyped-def]
    used = planner or Planner()
    result = await AskFinancialQuestionHandler(
        used,
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
    return result, used


async def test_movements_between_my_accounts_are_an_internal_only_read() -> None:
    ledger = Ledger()

    result, planner = await ask("¿Qué movimientos he tenido entre mis cuentas este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "600,00 € entre tus cuentas" in result.text
    assert planner.calls == 0
    assert len(ledger.plans) == 1
    plan = ledger.plans[0]
    assert plan.include_internal is True
    assert plan.internal_only is True
    assert plan.direction is Direction.EXPENSE
    assert plan.across_accounts is True


async def test_the_period_can_arrive_in_the_next_natural_turn() -> None:
    ledger = Ledger()

    first, _ = await ask("¿Qué movimientos he tenido entre mis cuentas?", ledger)

    assert first.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert first.conversation.awaiting is Slot.WINDOW
    assert first.conversation.active is not None
    assert ledger.plans == []

    second, planner = await ask("Los últimos 3 meses", ledger, conversation=first.conversation)

    assert second.outcome == PlanningOutcome.PLANNED.value
    assert planner.calls == 0
    assert ledger.plans[0].period == RelativePeriod(Period.LAST_N_MONTHS, n=3)
    assert ledger.plans[0].internal_only is True


async def test_invested_means_internal_contributions_into_the_asset_account() -> None:
    ledger = Ledger()

    result, planner = await ask("¿Cuánto he invertido este año?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "hacia la cuenta consultada" in result.text
    assert planner.calls == 0
    plan = ledger.plans[0]
    assert plan.direction is Direction.INCOME
    assert plan.only_account_id == ASSET
    assert plan.internal_only is True


async def test_common_typo_and_spending_wording_still_mean_fund_contributions() -> None:
    for sentence in (
        "Caunto he invertido en el ultimo año?",
        "¿Cuánto he gastado en inversiones este mes?",
    ):
        ledger = Ledger()

        result, planner = await ask(sentence, ledger)

        assert result.outcome == PlanningOutcome.PLANNED.value
        assert planner.calls == 0
        assert ledger.plans[0].direction is Direction.INCOME
        assert ledger.plans[0].only_account_id == ASSET
        assert ledger.plans[0].internal_only is True


async def test_money_moved_to_the_piggy_bank_uses_only_its_incoming_leg() -> None:
    ledger = Ledger()

    result, _ = await ask("¿Cuánto he movido a la hucha este mes?", ledger)

    assert "hacia la cuenta consultada" in result.text
    plan = ledger.plans[0]
    assert plan.direction is Direction.INCOME
    assert plan.only_account_id == SAVINGS
    assert plan.internal_only is True


async def test_an_ordinary_account_movement_list_is_not_narrowed_to_transfers() -> None:
    ledger = Ledger()
    fallback = FinancialPlan(
        direction=Direction.ALL,
        period=RelativePeriod(Period.THIS_MONTH),
    )
    planner = Planner(fallback)

    _, used = await ask("¿Qué movimientos tengo en la hucha este mes?", ledger, planner=planner)

    assert used.calls == 1
    assert ledger.plans[0].internal_only is False


async def test_singular_present_tense_traspaso_is_not_mistaken_for_transfer_history() -> None:
    ledger = Ledger()
    fallback = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_MONTH),
    )
    planner = Planner(fallback)

    _, used = await ask("¿Cuánto traspaso al fondo?", ledger, planner=planner)

    assert used.calls == 1
    assert ledger.plans[0].internal_only is False


async def test_last_month_in_a_fund_query_is_not_read_as_money_passed_to_it() -> None:
    ledger = Ledger()
    fallback = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )
    planner = Planner(fallback)

    _, used = await ask("¿Cuánto gasté el mes pasado en el fondo?", ledger, planner=planner)

    assert used.calls == 1
    assert ledger.plans[0].internal_only is False
