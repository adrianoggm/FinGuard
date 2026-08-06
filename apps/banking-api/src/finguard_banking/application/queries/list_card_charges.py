"""ListCardChargesQuery · Result · Handler — what was bought with one card.

The account travels with the card and is not redundant: the view filters on the declared account, so
another holder's card id selects nothing. Containment does not depend on this handler being careful.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from finguard_banking.application.bus.query_bus import Query
from finguard_banking.domain.ports.card_directory import CardDirectoryPort


@dataclass(frozen=True)
class CardChargeRow:
    transaction_id: UUID
    occurred_at: str
    description: str
    merchant_name: str | None
    category: str | None
    amount_minor: int


@dataclass(frozen=True)
class ListCardChargesResult:
    card_id: UUID
    since: str
    charges: list[CardChargeRow] = field(default_factory=list)


@dataclass(frozen=True)
class ListCardChargesQuery(Query[ListCardChargesResult]):
    account_id: UUID
    card_id: UUID
    since: date
    limit: int
    reporting_timezone: str = "Europe/Madrid"


class ListCardChargesHandler:
    def __init__(self, cards: CardDirectoryPort) -> None:
        self._cards = cards

    async def handle(self, query: ListCardChargesQuery) -> ListCardChargesResult:
        found = await self._cards.charges_on(
            query.account_id,
            query.card_id,
            since=query.since,
            timezone=query.reporting_timezone,
            limit=query.limit,
        )
        return ListCardChargesResult(
            card_id=query.card_id,
            since=query.since.isoformat(),
            charges=[
                CardChargeRow(
                    transaction_id=charge.transaction_id,
                    occurred_at=charge.occurred_at.isoformat(),
                    description=charge.description,
                    merchant_name=charge.merchant_name,
                    category=charge.category,
                    amount_minor=charge.amount_minor,
                )
                for charge in found
            ],
        )
