"""A page of ledger movements, independent of aggregate financial queries."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from finguard_banking.domain.constants import (
    MAX_CATEGORIES,
    MAX_MERCHANTS,
    MAX_SELECTED_ACCOUNTS,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_banking.domain.enums import Direction
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.models.financial_query_result import Movement
from finguard_banking.domain.value_objects.category import Category


@dataclass(frozen=True)
class MovementCursor:
    """Stable keyset position in newest-first movement order."""

    occurred_at: datetime
    transaction_id: UUID

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise InvalidValueError("movement cursor timestamp must include a timezone")


@dataclass(frozen=True)
class MovementListPlan:
    """A list request has no mandatory reporting window or aggregate measure."""

    account_id: UUID
    direction: Direction
    page_size: int = 20
    cursor: MovementCursor | None = None
    date_from: date | None = None
    date_to: date | None = None
    account_ids: tuple[UUID, ...] = ()
    across_accounts: bool = False
    categories: tuple[Category, ...] = ()
    merchant_terms: tuple[str, ...] = ()
    include_internal: bool = False
    reporting_timezone: str = "Europe/Madrid"

    def __post_init__(self) -> None:
        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= MAX_SUPPORTING_MOVEMENTS
        ):
            raise InvalidValueError(f"page size must be between 1 and {MAX_SUPPORTING_MOVEMENTS}")
        if (self.date_from is None) != (self.date_to is None):
            raise InvalidValueError("a movement list date range needs both ends")
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_to < self.date_from
        ):
            raise InvalidValueError("movement list date range ends before it starts")
        if len(set(self.account_ids)) != len(self.account_ids):
            raise InvalidValueError("movement list account ids cannot contain duplicates")
        if len(self.account_ids) > MAX_SELECTED_ACCOUNTS:
            raise InvalidValueError("movement list has too many selected accounts")
        if self.across_accounts and self.account_ids:
            raise InvalidValueError("across-account list cannot include an account selection")
        if len(self.categories) > MAX_CATEGORIES or len(self.merchant_terms) > MAX_MERCHANTS:
            raise InvalidValueError("movement list has too many filters")
        if any(not term.strip() or len(term) > 140 for term in self.merchant_terms):
            raise InvalidValueError("movement list merchant terms must be 1 to 140 characters")


@dataclass(frozen=True)
class MovementListPage:
    account_id: UUID
    movements: tuple[Movement, ...] = ()
    has_more: bool = False
    next_cursor: MovementCursor | None = None

    def __post_init__(self) -> None:
        if self.has_more != (self.next_cursor is not None):
            raise InvalidValueError("a continuing movement page needs exactly one next cursor")
        if self.next_cursor is not None and self.movements:
            last = self.movements[-1]
            if (
                last.transaction_id != self.next_cursor.transaction_id
                or last.occurred_at != self.next_cursor.occurred_at
            ):
                raise InvalidValueError("movement cursor must identify the final returned row")
