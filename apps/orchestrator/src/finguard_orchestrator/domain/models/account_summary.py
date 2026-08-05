"""The account as a bank app's home screen needs it: a balance, the last few movements, and the
holder's other accounts beside it.

Not a query result. It answers a fixed question that nobody phrased — "what does this account look
like right now" — and no model is involved in producing it. That is the point: the first figure a
user reads when they open the app comes from the ledger through a plan built in code, so the home
screen and the conversation cannot show two different balances.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from finguard_orchestrator.domain.models.query_answer import Movement


@dataclass(frozen=True)
class Holding:
    """One of the holder's accounts, as the home screen lists it.

    An account and not a movement: it has no date, no counterparty and no amount that happened. A
    movement is something that occurred, and this is something that is.

    Three kinds (migration 0014). CURRENT is money to spend, SAVINGS is money set aside, ASSET is a
    holding whose worth is not its cost. All three are here, including the current account — a
    reader that took this for "the non-cash ones" would leave the account most of the money is in
    out of the total.

    On an ASSET, `booked_minor` is therefore the cost basis and not a valuation. There is no price
    source yet, and a balance that looks like a valuation and is not is worse than no balance, so
    whatever renders it has to say which of the two it is showing.
    """

    account_id: UUID
    kind: str
    display_name: str
    currency: str
    booked_minor: int


@dataclass(frozen=True)
class AccountSummary:
    # Of the account the credential names, not of the holder. `holdings` is all of them, so the sum
    # of those is a different number from this one and no screen may label the two alike.
    #
    # There is deliberately no total field. Accounts in different currencies have no total, and a
    # required field would force one to be invented; whoever writes the sentence decides what to do
    # when they differ, and that is the one place that already knows how.
    currency: str
    # Booked is what the ledger has settled; available is what can be spent. They differ when
    # something is pending, and a screen that shows one labelled as the other is a screen that
    # tells somebody they have money they do not.
    booked_minor: int
    available_minor: int
    as_of: datetime
    movements: tuple[Movement, ...] = ()
    holdings: tuple[Holding, ...] = ()
