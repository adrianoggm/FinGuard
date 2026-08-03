"""A card an account holds, and what has been charged to it.

`last_four` and never a number: a PAN belongs in a vault, and nothing here needs one — a screen
shows four digits so a person recognises which card, and the payment goes through the account.

`spent_minor` is what the ledger says was charged in the window the caller asked for. It travels
with the card because a card without a figure beside it is a picture, and the point of the screen is
to say which card the money went out of.
"""

from dataclasses import dataclass
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
