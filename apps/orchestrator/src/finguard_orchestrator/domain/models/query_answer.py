"""What banking answered, in the assistant's own words.

Not banking's DTO. The assistant depends on the contract, not on the other service's
serialisation, so the wire shape stays in the adapter and only these cross into the
application. When the contract changes, one mapper changes.

Amounts stay in minor units all the way here. Formatting them is a presentation
decision that depends on the currency, and a float in between is how cents go missing.
"""

from dataclasses import dataclass, field
from datetime import datetime


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
    groups: tuple[Group, ...] = field(default_factory=tuple)
    movements: tuple[Movement, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """No movements matched. Worth its own name because "you spent 0" and "nothing
        matched" read the same to a user and mean different things — the second is
        usually a plan that was wrong."""
        return self.movement_count == 0
