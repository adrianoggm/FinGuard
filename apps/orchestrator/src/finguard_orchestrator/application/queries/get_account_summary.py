"""GetAccountSummaryQuery · Result · Handler — the home screen's figures.

A thin handler on purpose. There is no planning to do and no outcome to decide: the question is
fixed, so the only judgment left is which window and how many movements, and both are configuration
in the adapter. It goes through the bus anyway because that is how a controller reaches anything
here, and because the audit and correlation seams will want it.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort


@dataclass(frozen=True)
class SummaryMovement:
    """Flat, like every Result piece: no domain object leaves this layer."""

    occurred_at: str
    description: str
    amount_minor: int
    merchant_name: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class AccountSummaryResult:
    currency: str
    booked_minor: int
    available_minor: int
    as_of: str
    movements: list[SummaryMovement] = field(default_factory=list)


@dataclass(frozen=True)
class GetAccountSummaryQuery(Query[AccountSummaryResult]):
    account_id: UUID
    today: date
    reporting_timezone: str = "Europe/Madrid"


class GetAccountSummaryHandler:
    def __init__(self, bank: AccountSummaryPort) -> None:
        self._bank = bank

    async def handle(self, query: GetAccountSummaryQuery) -> AccountSummaryResult:
        summary = await self._bank.of(
            query.account_id, today=query.today, reporting_timezone=query.reporting_timezone
        )
        return AccountSummaryResult(
            currency=summary.currency,
            booked_minor=summary.booked_minor,
            available_minor=summary.available_minor,
            as_of=summary.as_of.isoformat(),
            movements=[
                SummaryMovement(
                    occurred_at=movement.occurred_at.isoformat(),
                    description=movement.description,
                    # Signed, all the way to the screen. The sign is what makes a movement a charge
                    # or a payment in, and nothing on the way there decides to show it absolute.
                    amount_minor=movement.amount_minor,
                    merchant_name=movement.merchant_name,
                    category=movement.category,
                )
                for movement in summary.movements
            ],
        )
