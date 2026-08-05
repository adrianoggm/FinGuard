from dataclasses import dataclass
from uuid import UUID

from finguard_banking.domain.enums import AccountKind, AccountStatus
from finguard_banking.domain.value_objects.money import Money


@dataclass(frozen=True)
class Holding:
    """One of a holder's accounts, with what is in it.

    An account and not a movement: there is no date, no counterparty and no amount that happened.
    A movement is something that occurred; this is something that is.

    All three kinds are Holdings, the current account included. A reader that took the word for
    "the non-cash ones" would leave out the account most of the money is in and still produce a
    total that looks right, which is this product's cardinal failure — a real figure answering a
    different question.

    On an `ASSET`, `balance` is the cost basis and not a valuation. There is no price source yet
    (ADR-009 §1), and a balance that looks like a valuation and is not is worse than no balance,
    so whatever renders it has to say which of the two it is showing.

    `display_name` is optional because the column is: the clearing account has no holder to name
    it, and an account nobody named is not a bug. A screen falls back to the kind.
    """

    account_id: UUID
    kind: AccountKind
    display_name: str | None
    balance: Money
    status: AccountStatus
