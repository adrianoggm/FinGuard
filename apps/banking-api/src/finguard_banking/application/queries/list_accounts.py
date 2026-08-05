"""ListAccountsQuery · Result · Handler — every account the holder has.

The read that makes "¿cuánto tengo?" answerable once there is more than one answer. Until now a
credential named an account and that was the whole of it; a holder with a current account, a piggy
bank and a fund gets a total with a breakdown under it, and a breakdown needs the parts.

No total here. Accounts in different currencies do not have one, and a Result that carried the
field would have to invent a number to fill it — whoever writes the sentence decides what to do
when they differ, and that is the one place that knows how.
"""

from dataclasses import dataclass, field
from uuid import UUID

from finguard_banking.application.bus.query_bus import Query
from finguard_banking.domain.ports.account_directory import AccountDirectoryPort


@dataclass(frozen=True)
class AccountRow:
    """Flat, like every Result piece: no domain object leaves this layer.

    `booked_minor` and `currency` rather than the `Money` the domain uses. The pair is what crosses
    HTTP, and flattening it here rather than in the mapper keeps the DTO a rename of this.
    """

    account_id: UUID
    kind: str
    display_name: str | None
    currency: str
    booked_minor: int
    status: str


@dataclass(frozen=True)
class ListAccountsResult:
    accounts: list[AccountRow] = field(default_factory=list)


@dataclass(frozen=True)
class ListAccountsQuery(Query[ListAccountsResult]):
    account_id: UUID


class ListAccountsHandler:
    def __init__(self, accounts: AccountDirectoryPort) -> None:
        self._accounts = accounts

    async def handle(self, query: ListAccountsQuery) -> ListAccountsResult:
        found = await self._accounts.all_of(query.account_id)
        return ListAccountsResult(
            accounts=[
                AccountRow(
                    account_id=holding.account_id,
                    kind=holding.kind.value,
                    display_name=holding.display_name,
                    currency=holding.balance.currency,
                    booked_minor=holding.balance.amount_minor,
                    status=holding.status.value,
                )
                for holding in found
            ]
        )
