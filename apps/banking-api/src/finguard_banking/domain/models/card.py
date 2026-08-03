"""A card an account holds, and what has been charged to it.

`last_four` and never a number: a PAN belongs in a vault, and nothing here needs one — a screen
shows four digits so a person recognises which card, and the payment goes through the account.

`spent_minor` is what the ledger says was charged in the window the caller asked for. It travels
with the card because a card without a figure beside it is a picture, and the point of the screen is
to say which card the money went out of.
"""

from dataclasses import dataclass
from datetime import datetime
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
    # None means the balance is the limit, which is a debit card. Not the same as a limit of zero.
    spending_limit_minor: int | None
    spent_minor: int
    charge_count: int


@dataclass(frozen=True)
class CardCharge:
    """One charge on a card, as a person reads it.

    A card screen that shows "437,92 € en 10 cargos" and cannot then show the ten is a screen asking
    to be trusted, which is the objection this whole product is built against.

    `amount_minor` is unsigned, deliberately: every row is a debit on the holder's side, so a sign
    would be decoration on a list where nothing can be positive. The screen writes the minus.
    """

    transaction_id: UUID
    occurred_at: datetime
    description: str
    merchant_name: str | None
    category: str | None
    amount_minor: int
