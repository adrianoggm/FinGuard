"""Ledger-native movement facts become explicit counterparty and receipt semantics."""

import uuid
from datetime import UTC, datetime

import pytest

from finguard_banking.domain.enums import CounterpartyType, ReceiptCategory, TransactionType
from finguard_banking.domain.services.movement_semantics import (
    counterparty_name_of,
    counterparty_type_of,
    receipt_category_of,
)
from finguard_banking.infrastructure.mappers.row_mapper import to_movement


@pytest.mark.parametrize(
    ("transaction_type", "expected"),
    [
        (TransactionType.CARD_PAYMENT, CounterpartyType.MERCHANT),
        (TransactionType.BIZUM, CounterpartyType.PERSON),
        (TransactionType.SALARY, CounterpartyType.EMPLOYER),
        (TransactionType.DIRECT_DEBIT, CounterpartyType.BILLER),
        (TransactionType.INTERNAL_TRANSFER, CounterpartyType.ACCOUNT),
    ],
)
def test_counterparty_role_comes_from_the_ledger_rail(
    transaction_type: TransactionType, expected: CounterpartyType
) -> None:
    assert counterparty_type_of(transaction_type) is expected


def test_only_real_direct_debits_receive_a_receipt_category() -> None:
    assert receipt_category_of(TransactionType.DIRECT_DEBIT, "HOUSING_RENT") is ReceiptCategory.RENT
    assert (
        receipt_category_of(TransactionType.DIRECT_DEBIT, "UNCLASSIFIED") is ReceiptCategory.OTHER
    )
    assert receipt_category_of(TransactionType.CARD_PAYMENT, "SUBSCRIPTIONS") is None


def test_structured_counterparty_name_wins_with_a_transparent_fallback() -> None:
    assert counterparty_name_of("  Netflix ", "Cargo mensual") == "Netflix"
    assert counterparty_name_of(None, "  Cargo sin comercio ") == "Cargo sin comercio"


def test_database_movement_keeps_its_rail_and_derives_receipt_facts() -> None:
    movement = to_movement(
        {
            "transaction_id": uuid.UUID(int=7),
            "occurred_at": datetime(2026, 7, 2, tzinfo=UTC),
            "description": "Recibo alquiler",
            "merchant_name": "Arrendamiento Centro",
            "category": "HOUSING_RENT",
            "category_is_custom": False,
            "category_source": "BANK",
            "is_internal": False,
            "amount_minor": 92_000,
            "direction": "EXPENSE",
            "transaction_type": "DIRECT_DEBIT",
        },
        "EUR",
    )

    assert movement.transaction_type is TransactionType.DIRECT_DEBIT
    assert movement.counterparty_name == "Arrendamiento Centro"
    assert movement.counterparty_type is CounterpartyType.BILLER
    assert movement.receipt_category is ReceiptCategory.RENT
