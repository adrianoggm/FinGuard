"""Database row → domain. Testable without a database.

It used to be two nested comprehensions inside the repository's `run()`, which meant
the conversion could only be exercised by talking to PostgreSQL.
"""

from typing import Any
from uuid import UUID

from finguard_banking.domain.models.balance import Balance
from finguard_banking.domain.models.financial_query_result import Movement, QueryGroup
from finguard_banking.domain.value_objects.money import Money


def to_balance(account_id: UUID, row: Any) -> Balance:
    booked = Money(int(row["booked_minor"]), row["currency"])
    return Balance(
        account_id=account_id,
        # No holds yet: available and booked are the same figure.
        available=booked,
        booked=booked,
        as_of=row["as_of"],
    )


def to_group(row: Any, currency: str) -> QueryGroup:
    return QueryGroup(
        key=str(row["key"]),
        total=Money(int(row["total_minor"]), currency),
        movement_count=int(row["movement_count"]),
    )


def _optional(row: Any, column: str) -> Any:
    """A column the row may not have.

    asyncpg's Record raises on a missing key rather than returning None, and the three queries that
    build a Movement do not select the same columns. Returning None here is right: a column that was
    not asked for is not a fact about the movement.
    """
    try:
        return row[column]
    except KeyError:
        return None


def to_movement(row: Any, currency: str) -> Movement:
    return Movement(
        transaction_id=row["transaction_id"],
        occurred_at=row["occurred_at"],
        description=row["description"],
        merchant_name=row["merchant_name"],
        category=row["category"],
        category_is_custom=bool(row["category_is_custom"]),
        # `.get`-shaped, because the ranked query selects a narrower row than the movement list and
        # a missing column there is not a movement without a category source.
        category_source=_optional(row, "category_source") or "NONE",
        is_internal=bool(_optional(row, "is_internal")),
        amount=Money(int(row["amount_minor"]), currency),
        direction=row["direction"],
    )
