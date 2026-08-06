from datetime import date
from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_banking.domain.models.card import Card, CardCharge


@runtime_checkable
class CardDirectoryPort(Protocol):
    """The cards an account holds, each with what has been charged to it since a date.

    `since` and the zone are parameters rather than a clock this reads, for the same reason they are
    everywhere else here: a read that asks the database what time it is cannot be replayed, and a
    window is a date *and* a zone — a charge at 00:30 on the 1st belongs to a different month in
    Madrid than in UTC.
    """

    async def all_of(self, account_id: UUID, *, since: date, timezone: str) -> tuple[Card, ...]: ...

    async def charges_on(
        self, account_id: UUID, card_id: UUID, *, since: date, timezone: str, limit: int
    ) -> tuple[CardCharge, ...]: ...

    """What was bought with one card.

    The account is a parameter as well as the card, and it is not redundant: a card id that belongs
    to somebody else must come back empty rather than come back at all, and the view filters on the
    declared account regardless of what card is asked for.
    """
