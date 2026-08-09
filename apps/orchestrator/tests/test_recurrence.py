"""Recurring charges are discovered from complete dates and survive follow-ups."""

import uuid
from datetime import UTC, date, datetime

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.constants import RECURRING_EXPENSE_CATEGORIES
from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    Metric,
    Period,
    PlanningOutcome,
    ReceiptCategory,
    TransactionType,
)
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.services import recurrence_analyzer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 9)


class ModelMustNotPlan:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, *_: object, **__: object) -> PlanningResult:
        self.calls += 1
        raise AssertionError("a grounded recurrence question must not reach the model")


class Ledger:
    def __init__(self, answer: QueryAnswer) -> None:
        self.answer = answer
        self.plans: list[FinancialPlan] = []
        self.movement_limits: list[int | None] = []

    async def run(
        self, plan: FinancialPlan, *, movements: int | None = None, **_: object
    ) -> QueryAnswer:
        self.plans.append(plan)
        self.movement_limits.append(movements)
        return self.answer


class Accounts:
    async def of(self, *_: object, **__: object) -> AccountSummary:
        return AccountSummary(
            currency="EUR",
            booked_minor=100_000,
            available_minor=100_000,
            as_of=datetime(2026, 8, 9, 10, 0),
        )


class Contacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


class Cards:
    async def all_of(self, *_: object, **__: object) -> Wallet:
        return Wallet(since=TODAY)

    async def charges_on(self, *_: object, **__: object) -> tuple[()]:
        return ()


async def ask(sentence: str, ledger: Ledger, *, conversation: Conversation = IDLE):  # type: ignore[no-untyped-def]
    planner = ModelMustNotPlan()
    result = await AskFinancialQuestionHandler(
        planner,
        ledger,  # type: ignore[arg-type]
        accounts=Accounts(),
        contacts=Contacts(),
        cards=Cards(),
    ).handle(
        AskFinancialQuestionQuery(
            sentence=sentence,
            account_id=ACCOUNT,
            today=TODAY,
            conversation=conversation,
        )
    )
    return result, planner


def movement(
    when: datetime,
    merchant: str,
    amount_minor: int,
    category: str = "SUBSCRIPTIONS",
    *,
    transaction_type: TransactionType | None = None,
    counterparty_type: CounterpartyType = CounterpartyType.UNKNOWN,
    receipt_category: ReceiptCategory | None = None,
) -> Movement:
    return Movement(
        when,
        f"Cargo de {merchant}",
        amount_minor,
        "EXPENSE",
        merchant_name=merchant,
        category=category,
        transaction_type=transaction_type,
        counterparty_name=merchant,
        counterparty_type=counterparty_type,
        receipt_category=receipt_category,
    )


def answer(*movements: Movement, movement_count: int | None = None) -> QueryAnswer:
    return QueryAnswer(
        currency="EUR",
        total_minor=sum(item.amount_minor for item in movements),
        movement_count=len(movements) if movement_count is None else movement_count,
        movements=movements,
    )


async def test_discovers_monthly_commitments_without_calling_the_model() -> None:
    ledger = Ledger(
        answer(
            *(
                movement(datetime(2026, month, 2), "Arrendamiento Centro", 92_000, "HOUSING_RENT")
                for month in range(1, 5)
            ),
            *(movement(datetime(2026, month, 8), "Netflix", 1_399) for month in range(1, 5)),
            movement(datetime(2026, 1, 3), "Mercadona", 4_200, "GROCERIES"),
            movement(datetime(2026, 1, 17), "Mercadona", 5_100, "GROCERIES"),
            movement(datetime(2026, 3, 28), "Mercadona", 6_300, "GROCERIES"),
        )
    )

    result, planner = await ask("¿Cuáles son mis gastos recurrentes?", ledger)

    assert result.outcome == PlanningOutcome.PLANNED.value
    assert "2 patrones de gasto recurrente" in result.text
    assert "Arrendamiento Centro: mensual" in result.text
    assert "Netflix: mensual" in result.text
    assert "Mercadona" not in result.text
    assert ledger.plans[0].metric is Metric.RECURRENCE
    assert ledger.plans[0].categories == RECURRING_EXPENSE_CATEGORIES
    assert ledger.plans[0].period == RelativePeriod(Period.LAST_N_MONTHS, n=12)
    assert ledger.movement_limits == [100]
    assert planner.calls == 0


async def test_named_category_and_period_are_preserved_in_a_follow_up() -> None:
    charges = answer(
        *(movement(datetime(2026, month, 7), "Spotify", 1_099) for month in range(1, 5))
    )
    ledger = Ledger(charges)
    first, _ = await ask("¿Qué suscripciones pago todos los meses?", ledger)

    second, planner = await ask("¿Y este año?", ledger, conversation=first.conversation)

    assert ledger.plans[0].categories == ("SUBSCRIPTIONS",)
    assert ledger.plans[-1].metric is Metric.RECURRENCE
    assert ledger.plans[-1].period == RelativePeriod(Period.THIS_YEAR)
    assert "Spotify: mensual" in second.text
    assert planner.calls == 0


async def test_variable_periodic_bill_is_recurrent_without_claiming_a_fixed_amount() -> None:
    ledger = Ledger(
        answer(
            movement(datetime(2026, 1, 5), "Recibo Energía", 4_100),
            movement(datetime(2026, 2, 6), "Recibo Energía", 7_900),
            movement(datetime(2026, 3, 4), "Recibo Energía", 5_200),
        )
    )

    result, _ = await ask("¿Qué cargos son periódicos?", ledger)

    assert "Recibo Energía: mensual" in result.text
    assert "una media de" in result.text


async def test_incomplete_page_refuses_to_extrapolate_recurrences() -> None:
    charges = tuple(
        movement(datetime(2026, 1 + index % 7, 2), f"Comercio {index}", 1_000)
        for index in range(100)
    )
    ledger = Ledger(answer(*charges, movement_count=101))

    result, _ = await ask("Enséñame mis pagos recurrentes", ledger)

    assert "sin extrapolar" in result.text
    assert "He detectado" not in result.text


def test_monthly_pattern_uses_the_reporting_timezone() -> None:
    data = answer(
        movement(datetime(2026, 1, 31, 23, 30, tzinfo=UTC), "Servicio", 1_000),
        movement(datetime(2026, 2, 28, 23, 30, tzinfo=UTC), "Servicio", 1_000),
        movement(datetime(2026, 3, 31, 22, 30, tzinfo=UTC), "Servicio", 1_000),
    )

    analysis = recurrence_analyzer.analyse(data, reporting_timezone="Europe/Madrid")

    assert analysis.candidates[0].cadence == "MONTHLY"
    assert analysis.candidates[0].day_of_month_min == 1
    assert analysis.candidates[0].day_of_month_max == 1


def test_irregular_repeat_purchases_are_not_recurring() -> None:
    data = answer(
        movement(datetime(2026, 1, 2), "Mercadona", 2_100, "GROCERIES"),
        movement(datetime(2026, 1, 8), "Mercadona", 8_000, "GROCERIES"),
        movement(datetime(2026, 2, 19), "Mercadona", 3_200, "GROCERIES"),
        movement(datetime(2026, 3, 1), "Mercadona", 6_400, "GROCERIES"),
    )

    analysis = recurrence_analyzer.analyse(data, reporting_timezone="Europe/Madrid")

    assert analysis.candidates == ()


async def test_natural_subscription_and_direct_debit_phrases_use_recurrence() -> None:
    charges = answer(
        *(movement(datetime(2026, month, 7), "Spotify", 1_099) for month in range(1, 5))
    )

    for sentence in (
        "¿Qué suscripciones tengo?",
        "¿Cuáles son mis recibos domiciliados?",
        "¿Qué adeudos domiciliados tengo?",
        "Muéstrame mis recibos",
        "¿Qué pagos tengo todos los meses?",
    ):
        ledger = Ledger(charges)
        result, planner = await ask(sentence, ledger)

        assert result.outcome == PlanningOutcome.PLANNED.value
        assert ledger.plans[0].metric is Metric.RECURRENCE
        if "recibos" in sentence or "adeudos" in sentence:
            assert ledger.plans[0].transaction_types == (TransactionType.DIRECT_DEBIT,)
            assert ledger.plans[0].categories == ()
        assert planner.calls == 0


async def test_typed_receipts_and_card_subscriptions_are_named_without_conflating_them() -> None:
    charges = []
    for month in range(1, 5):
        charges.extend(
            (
                movement(
                    datetime(2026, month, 2),
                    "Servicio Hogar",
                    9_200,
                    "HOUSING_RENT",
                    transaction_type=TransactionType.DIRECT_DEBIT,
                    counterparty_type=CounterpartyType.BILLER,
                    receipt_category=ReceiptCategory.RENT,
                ),
                movement(
                    datetime(2026, month, 8),
                    "Servicio Hogar",
                    1_399,
                    transaction_type=TransactionType.CARD_PAYMENT,
                    counterparty_type=CounterpartyType.MERCHANT,
                ),
            )
        )
    ledger = Ledger(answer(*charges))

    result, planner = await ask("¿Qué pagos recurrentes tengo?", ledger)

    assert "2 patrones de gasto recurrente" in result.text
    assert "recibo domiciliado de alquiler" in result.text
    assert "suscripción con tarjeta" in result.text
    assert result.text.count("Servicio Hogar:") == 2
    assert planner.calls == 0


async def test_a_direct_debit_scope_survives_a_natural_period_follow_up() -> None:
    receipts = answer(
        *(
            movement(
                datetime(2026, month, 2),
                "Seguro Hogar",
                3_000,
                "INSURANCE",
                transaction_type=TransactionType.DIRECT_DEBIT,
                counterparty_type=CounterpartyType.BILLER,
                receipt_category=ReceiptCategory.INSURANCE,
            )
            for month in range(1, 5)
        )
    )
    ledger = Ledger(receipts)
    first, _ = await ask("Muéstrame mis recibos", ledger)

    second, planner = await ask("¿Y este año?", ledger, conversation=first.conversation)

    assert ledger.plans[-1].transaction_types == (TransactionType.DIRECT_DEBIT,)
    assert ledger.plans[-1].period == RelativePeriod(Period.THIS_YEAR)
    assert "recibo domiciliado de seguro" in second.text
    assert planner.calls == 0
