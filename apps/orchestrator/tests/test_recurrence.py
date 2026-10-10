"""Recurring charges are discovered from complete dates, in the holder's reporting timezone."""

from datetime import UTC, datetime

from finguard_orchestrator.domain.enums import CounterpartyType, ReceiptCategory, TransactionType
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.services import recurrence_analyzer


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
