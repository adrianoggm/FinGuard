"""Filtering by the shop a person names.

"¿Cuánto gasté en Mercadona?" had no answer: the contract could group *by* merchant and
could not narrow *to* one, so the honest outcome was a clarification asking the user to name
a category instead — which is not what they asked. It was the largest single source of unsafe
execution, and every attempt to teach the model around it traded one group's score for
another's.

The name is free text, unlike a category. Two consequences the tests pin: it travels as a
bound parameter and never as SQL, and it is matched case-insensitively so "mercadona" finds
"Mercadona" — a filter that only works when the user capitalises the way the ledger does is a
filter that answers "no movements" to a real question.
"""

import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest
from support import as_pool, create_account, record_movement

from finguard_banking.domain.enums import Direction
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.value_objects.date_range import DateRange
from finguard_banking.infrastructure.dto.banking_dto import FinancialQueryRequest
from finguard_banking.infrastructure.mappers.http_mapper import plan_from_dto
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    PostgresFinancialQueryRepository,
)
from finguard_banking.infrastructure.persistence.query_compiler import compile_aggregate

JULY = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 31))
ACCOUNT = uuid.uuid4()
INJECTION = "x'; DROP TABLE ledger_entries; --"


def plan(**kwargs: object) -> FinancialQuery:
    return FinancialQuery(account_id=ACCOUNT, date_range=JULY, **kwargs)  # type: ignore[arg-type]


def test_a_plan_can_name_the_shops_it_is_about() -> None:
    assert plan(merchants=("Mercadona",)).merchants == ("Mercadona",)


def test_the_contract_carries_them() -> None:
    body = FinancialQueryRequest.model_validate(
        {
            "accountId": str(ACCOUNT),
            "merchants": ["Mercadona", "Carrefour"],
            "dateRange": {"from": "2026-07-01", "to": "2026-07-31"},
        }
    )

    assert plan_from_dto(body, date(2026, 7, 15)).merchants == ("Mercadona", "Carrefour")


def test_partial_name_terms_have_a_separate_contract() -> None:
    body = FinancialQueryRequest.model_validate(
        {
            "accountId": str(ACCOUNT),
            "merchantTerms": ["apuest", "bet"],
            "dateRange": {"from": "2026-07-01", "to": "2026-07-31"},
        }
    )

    assert plan_from_dto(body, date(2026, 7, 15)).merchant_terms == ("apuest", "bet")


def test_partial_name_terms_are_or_alternatives_and_bound_parameters() -> None:
    compiled = compile_aggregate(plan(merchant_terms=("apuest", "bet")), "Europe/Madrid")

    assert "strpos(lower(COALESCE(t.merchant_name, ''))" in compiled.sql
    assert " OR " in compiled.sql
    assert "apuest" not in compiled.sql and "bet" not in compiled.sql
    assert "apuest" in compiled.parameters and "bet" in compiled.parameters


def test_a_name_travels_as_a_parameter_and_never_as_sql() -> None:
    """The one property a free-text filter has to have. A category is a closed enum and this
    is whatever the user typed, so it is the first value in this contract that a model could
    fill with anything at all."""
    compiled = compile_aggregate(plan(merchants=(INJECTION,)), "Europe/Madrid")

    bound = [
        value for row in compiled.parameters for value in (row if isinstance(row, list) else [row])
    ]

    assert INJECTION not in compiled.sql
    assert "DROP TABLE" not in compiled.sql
    # Bound instead, which is the property that matters: the text is a value the database
    # compares, never SQL it parses. Lowered because the comparison is case-insensitive.
    assert INJECTION.lower() in bound


def test_an_empty_name_is_refused() -> None:
    with pytest.raises(InvalidValueError):
        plan(merchants=("",))


def test_too_many_names_are_refused() -> None:
    with pytest.raises(InvalidValueError):
        plan(merchants=tuple(f"shop-{n}" for n in range(50)))


@pytest.mark.integration
async def test_it_only_adds_up_the_named_shop(connection: asyncpg.Connection) -> None:
    account, bank = await create_account(connection), await create_account(connection)
    for merchant, amount in (("Mercadona", -4210), ("Carrefour", -1000)):
        await record_movement(
            connection,
            account=account,
            counterparty=bank,
            amount_minor=amount,
            occurred_at=datetime(2026, 7, 3, 12, tzinfo=UTC),
            merchant=merchant,
        )
    repository = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")

    answer = await repository.run(
        FinancialQuery(
            account_id=account,
            date_range=JULY,
            direction=Direction.EXPENSE,
            merchants=("Mercadona",),
        )
    )

    assert answer.total.amount_minor == 4210
    assert answer.movement_count == 1


@pytest.mark.integration
async def test_the_name_is_matched_however_it_is_capitalised(
    connection: asyncpg.Connection,
) -> None:
    """A filter that only works when the user types the ledger's exact casing answers "no
    movements" to a real question, which reads as "you spent nothing there"."""
    account, bank = await create_account(connection), await create_account(connection)
    await record_movement(
        connection,
        account=account,
        counterparty=bank,
        amount_minor=-4210,
        occurred_at=datetime(2026, 7, 3, 12, tzinfo=UTC),
        merchant="Mercadona",
    )
    repository = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")

    answer = await repository.run(
        FinancialQuery(
            account_id=account,
            date_range=JULY,
            direction=Direction.EXPENSE,
            merchants=("mercadona",),
        )
    )

    assert answer.movement_count == 1
