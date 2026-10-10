"""A ledger list query, independent of period-bound aggregate plans."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from finguard_orchestrator.domain.constants import (
    MAX_CATEGORIES,
    MAX_MERCHANT_LENGTH,
    MAX_MERCHANTS,
    MAX_SELECTED_ACCOUNTS,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_orchestrator.domain.enums import Direction
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.financial_plan import RelativePeriod


@dataclass(frozen=True)
class MovementCursor:
    occurred_at: datetime
    transaction_id: UUID

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise InvalidValueError("movement cursor timestamp must include a timezone")


@dataclass(frozen=True)
class MovementListPlan:
    direction: Direction = Direction.ALL
    page_size: int = 20
    cursor: MovementCursor | None = None
    period: RelativePeriod | None = None
    period_reference: date | None = None
    date_from: date | None = None
    date_to: date | None = None
    account_ids: tuple[UUID, ...] = ()
    across_accounts: bool = False
    categories: tuple[str, ...] = ()
    merchant_terms: tuple[str, ...] = ()
    include_internal: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= MAX_SUPPORTING_MOVEMENTS
        ):
            raise InvalidValueError("movement page size is outside the supported range")
        if (self.date_from is None) != (self.date_to is None):
            raise InvalidValueError("movement list date range needs both ends")
        if self.period is not None and self.date_from is not None:
            raise InvalidValueError(
                "movement list cannot have both a relative period and date range"
            )
        if (self.period is None) != (self.period_reference is None):
            raise InvalidValueError("movement list period requires its stable reference date")
        if self.date_from is not None and self.date_to is not None:
            if self.date_to < self.date_from:
                raise InvalidValueError("movement list date range ends before it starts")
        if self.across_accounts and self.account_ids:
            raise InvalidValueError("across-account list cannot include account ids")
        if len(set(self.account_ids)) != len(self.account_ids):
            raise InvalidValueError("movement list account ids cannot contain duplicates")
        if len(self.account_ids) > MAX_SELECTED_ACCOUNTS:
            raise InvalidValueError("movement list has too many selected accounts")
        if len(self.categories) > MAX_CATEGORIES or len(self.merchant_terms) > MAX_MERCHANTS:
            raise InvalidValueError("movement list has too many filters")
        if any(not term.strip() or len(term) > MAX_MERCHANT_LENGTH for term in self.merchant_terms):
            raise InvalidValueError(
                f"movement list merchant terms must be 1 to {MAX_MERCHANT_LENGTH} characters"
            )
