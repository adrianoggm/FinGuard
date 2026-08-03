"""A card an account holds, and what was charged to it in a window.

The assistant's own shape. Four digits and never a number — a PAN belongs in a vault, and a screen
needs only enough to recognise which card.

`since` travels with the figures rather than being remembered by whoever asked. A total whose window
is implicit is a total somebody eventually labels as a month.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID


@dataclass(frozen=True)
class Card:
    card_id: UUID
    display_name: str
    brand: str
    kind: str
    last_four: str
    expires_month: int
    expires_year: int
    status: str
    # None means the balance is the limit, which is a debit card. Not a limit of zero.
    spending_limit_minor: int | None
    spent_minor: int
    charge_count: int


@dataclass(frozen=True)
class Wallet:
    since: date
    cards: tuple[Card, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CardCharge:
    """One charge on a card.

    Unsigned, because every charge is money out: a sign would be decoration on a list where nothing
    can be positive, and the screen is what writes the minus.
    """

    transaction_id: UUID
    occurred_at: datetime
    description: str
    merchant_name: str | None
    category: str | None
    amount_minor: int
