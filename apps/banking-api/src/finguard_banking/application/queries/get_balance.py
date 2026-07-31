"""GetBalanceQuery · GetBalanceResult · GetBalanceHandler."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from finguard_banking.application.bus.query_bus import Query
from finguard_banking.domain.models.balance import Balance
from finguard_banking.domain.ports.ledger_repository import LedgerRepository


@dataclass(frozen=True)
class GetBalanceResult:
    """What the query answers with.

    A Result, not the `Balance` model: a query never hands a domain object out of the
    application layer, or the controller ends up depending on the core's shape. Money is
    flattened here because this is a carrier on its way to the wire.
    """

    account_id: UUID
    available_minor: int
    booked_minor: int
    currency: str
    as_of: datetime


@dataclass(frozen=True)
class GetBalanceQuery(Query[GetBalanceResult]):
    account_id: UUID


class GetBalanceHandler:
    """Reads the balance of an account.

    This is where the check that the caller owns the account will go (§12 of the
    vision), which is why the controller never talks to the repository.
    """

    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    async def handle(self, query: GetBalanceQuery) -> GetBalanceResult:
        return _to_result(await self._ledger.balance_of(query.account_id))


def _to_result(balance: Balance) -> GetBalanceResult:
    return GetBalanceResult(
        account_id=balance.account_id,
        available_minor=balance.available.amount_minor,
        booked_minor=balance.booked.amount_minor,
        currency=balance.currency,
        as_of=balance.as_of,
    )
