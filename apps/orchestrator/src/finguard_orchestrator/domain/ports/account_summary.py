from datetime import date
from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.models.account_summary import AccountSummary


@runtime_checkable
class AccountSummaryPort(Protocol):
    """The account's current state, for a screen rather than for a question.

    `today` and the zone travel together for the same reason they do on the query path: a window is
    a date *and* a zone, and between midnight and 01:00 peninsular time a holder in the Canaries is
    on the previous day.

    Raises rather than answering with zeros. A balance of 0,00 € is a figure, and showing one for
    "we could not ask" is the worst kind of wrong on a bank's home screen.
    """

    async def of(
        self, account_id: UUID, *, today: date, reporting_timezone: str
    ) -> AccountSummary: ...
