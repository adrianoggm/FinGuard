"""The account as a bank app's home screen needs it: a balance and the last few movements.

Not a query result. It answers a fixed question that nobody phrased — "what does this account look
like right now" — and no model is involved in producing it. That is the point: the first figure a
user reads when they open the app comes from the ledger through a plan built in code, so the home
screen and the conversation cannot show two different balances.
"""

from dataclasses import dataclass
from datetime import datetime

from finguard_orchestrator.domain.models.query_answer import Movement


@dataclass(frozen=True)
class AccountSummary:
    currency: str
    # Booked is what the ledger has settled; available is what can be spent. They differ when
    # something is pending, and a screen that shows one labelled as the other is a screen that
    # tells somebody they have money they do not.
    booked_minor: int
    available_minor: int
    as_of: datetime
    movements: tuple[Movement, ...] = ()
