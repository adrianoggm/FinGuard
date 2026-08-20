from datetime import date
from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.models.card import CardCharge, Wallet


@runtime_checkable
class CardDirectoryPort(Protocol):
    """The cards an account holds, each with what it was charged since a date.

    `since` is a decision, not a default: the screen says which window it is showing, so the window
    has to come from the screen. Raises rather than answering with an empty wallet — an account with
    no cards and an account we could not ask about look the same otherwise.

    `today` and `timezone` are the holder's, and they travel with `since` because banking bins the
    charges by day: a window resolved here and binned in another zone is a real total for the wrong
    days. The adapter used to read its own clock instead, which is the deployment's, not theirs.
    """

    async def all_of(
        self, account_id: UUID, *, since: date, today: date, timezone: str
    ) -> Wallet: ...

    async def charges_on(
        self,
        account_id: UUID,
        card_id: UUID,
        *,
        since: date,
        today: date,
        timezone: str,
        limit: int,
    ) -> tuple[CardCharge, ...]: ...

    """The charges behind a card's total.

    A card that is not this account's answers with an empty list rather than an error, because that
    is what banking answers: the view filters on the declared account, so the id selects nothing.
    Telling a caller that a card exists but is not theirs would be telling them something.
    """
