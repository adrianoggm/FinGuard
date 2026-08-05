"""ListCardsQuery · Result · Handler — the cards, each with what it has been charged.

`since` arrives on the query rather than being computed here, like `today` everywhere else: a
handler that reads the clock cannot be replayed, and the window a screen shows is the caller's
decision.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from finguard_banking.application.bus.query_bus import Query
from finguard_banking.domain.ports.card_directory import CardDirectoryPort


@dataclass(frozen=True)
class CardRow:
    """Flat, like every Result piece: no domain object leaves this layer."""

    card_id: UUID
    display_name: str
    brand: str
    kind: str
    last_four: str
    expires_month: int
    expires_year: int
    status: str
    spending_limit_minor: int | None
    spent_minor: int
    charge_count: int


@dataclass(frozen=True)
class ListCardsResult:
    since: str
    cards: list[CardRow] = field(default_factory=list)


@dataclass(frozen=True)
class ListCardsQuery(Query[ListCardsResult]):
    account_id: UUID
    since: date
    # No default. It was "Europe/Madrid", which the controller never overrode, so every card screen
    # binned its charges in that zone whatever the caller or the deployment had said. A default here
    # cannot be right: the zone belongs to whoever is asking, and a wrong one moves a charge made
    # near midnight into the neighbouring day with a perfectly real figure.
    reporting_timezone: str


class ListCardsHandler:
    def __init__(self, cards: CardDirectoryPort) -> None:
        self._cards = cards

    async def handle(self, query: ListCardsQuery) -> ListCardsResult:
        held = await self._cards.all_of(
            query.account_id, since=query.since, timezone=query.reporting_timezone
        )
        return ListCardsResult(
            # Echoed back, because every figure here is "since this date" and a caller that had to
            # remember what it asked for would eventually label a 30-day total as a month.
            since=query.since.isoformat(),
            cards=[
                CardRow(
                    card_id=card.card_id,
                    display_name=card.display_name,
                    brand=card.brand,
                    kind=card.kind,
                    last_four=card.last_four,
                    expires_month=card.expires_month,
                    expires_year=card.expires_year,
                    status=card.status,
                    spending_limit_minor=card.spending_limit_minor,
                    spent_minor=card.spent_minor,
                    charge_count=card.charge_count,
                )
                for card in held
            ],
        )
