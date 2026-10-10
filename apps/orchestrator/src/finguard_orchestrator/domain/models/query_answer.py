"""What banking answered, in the assistant's own words.

Not banking's DTO. The assistant depends on the contract, not on the other service's
serialisation, so the wire shape stays in the adapter and only these cross into the
application. When the contract changes, one mapper changes.

Amounts stay in minor units all the way here. Formatting them is a presentation
decision that depends on the currency, and a float in between is how cents go missing.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    ReceiptCategory,
    TransactionType,
)
from finguard_orchestrator.domain.value_objects.movement_list_plan import MovementCursor


@dataclass(frozen=True)
class Movement:
    """One movement behind a figure.

    These are the evidence. An answer that states a total without them asks the user to
    take the number on faith, and a number from a language-model-shaped pipeline is
    exactly the kind nobody should take on faith.
    """

    occurred_at: datetime
    description: str
    amount_minor: int
    direction: str
    merchant_name: str | None = None
    category: str | None = None
    # The ledger's identifier, needed by the one screen that can refile a movement.
    transaction_id: str | None = None
    # Whether the category is the holder's own rather than the bank's guess.
    category_is_custom: bool = False
    # Which of the four layers decided the category: BANK, SYSTEM, USER or NONE.
    category_source: str = "NONE"
    # Money that stayed between the holder's own accounts. Excluded from totals by banking; carried
    # here so a list can say why a 200 € line is not a purchase.
    is_internal: bool = False
    # What the amount is denominated in, filled only when the movement *is* the answer — a ranking.
    # Supporting movements leave it None because their answer already carries the account's
    # currency once, and repeating it per row would be two places for it to disagree.
    currency: str | None = None
    # Ledger-native semantics. `merchant_name` remains for compatibility with existing clients;
    # recurrence uses the explicit counterparty role and direct-debit classification below.
    transaction_type: TransactionType | None = None
    counterparty_name: str | None = None
    counterparty_type: CounterpartyType = CounterpartyType.UNKNOWN
    receipt_category: ReceiptCategory | None = None


@dataclass(frozen=True)
class Group:
    """One row of a breakdown: a category, a merchant, a week."""

    key: str
    total_minor: int
    movement_count: int


@dataclass(frozen=True)
class QueryAnswer:
    currency: str
    total_minor: int
    movement_count: int
    period_start: date | None = None
    period_end: date | None = None
    groups: tuple[Group, ...] = field(default_factory=tuple)
    movements: tuple[Movement, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """No movements matched. Worth its own name because "you spent 0" and "nothing
        matched" read the same to a user and mean different things — the second is
        usually a plan that was wrong."""
        return self.movement_count == 0


@dataclass(frozen=True)
class MovementListAnswer:
    """A paginated row result; unlike QueryAnswer it has no aggregate total or period."""

    account_id: UUID
    movements: tuple[Movement, ...]
    has_more: bool
    next_cursor: MovementCursor | None = None

    def __post_init__(self) -> None:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("a continuing movement page must include exactly one cursor")
        if self.next_cursor is not None and self.movements:
            last = self.movements[-1]
            if (
                last.transaction_id != str(self.next_cursor.transaction_id)
                or last.occurred_at != self.next_cursor.occurred_at
            ):
                raise ValueError("movement cursor must identify the final returned row")
