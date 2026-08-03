from datetime import date
from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_banking.domain.models.card import Card


@runtime_checkable
class CardDirectoryPort(Protocol):
    """The cards an account holds, each with what has been charged to it since a date.

    `since` and the zone are parameters rather than a clock this reads, for the same reason they are
    everywhere else here: a read that asks the database what time it is cannot be replayed, and a
    window is a date *and* a zone — a charge at 00:30 on the 1st belongs to a different month in
    Madrid than in UTC.
    """

    async def all_of(self, account_id: UUID, *, since: date, timezone: str) -> tuple[Card, ...]: ...
