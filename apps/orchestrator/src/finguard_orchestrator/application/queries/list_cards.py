"""ListCardsQuery · Result · Handler — the wallet a card screen draws.

`since` is computed at the edge from the window the caller asked for, like `today` on every other
path: a handler that reads the clock cannot be replayed.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.domain.ports.card_directory import CardDirectoryPort


@dataclass(frozen=True)
class CardRow:
    card_id: str
    display_name: str
    brand: str
    kind: str
    last_four: str
    expires: str
    status: str
    spending_limit_minor: int | None
    spent_minor: int
    charge_count: int


@dataclass(frozen=True)
class WalletResult:
    since: str
    cards: list[CardRow] = field(default_factory=list)


@dataclass(frozen=True)
class ListCardsQuery(Query[WalletResult]):
    account_id: UUID
    since: date


class ListCardsHandler:
    def __init__(self, cards: CardDirectoryPort) -> None:
        self._cards = cards

    async def handle(self, query: ListCardsQuery) -> WalletResult:
        wallet = await self._cards.all_of(query.account_id, since=query.since)
        return WalletResult(
            since=wallet.since.isoformat(),
            cards=[
                CardRow(
                    card_id=str(card.card_id),
                    display_name=card.display_name,
                    brand=card.brand,
                    kind=card.kind,
                    last_four=card.last_four,
                    # Formatted here because "09/28" is how an expiry is written on the card itself,
                    # and a screen that had to assemble it from two numbers would assemble it
                    # differently in each place it appeared.
                    expires=f"{card.expires_month:02d}/{card.expires_year % 100:02d}",
                    status=card.status,
                    spending_limit_minor=card.spending_limit_minor,
                    spent_minor=card.spent_minor,
                    charge_count=card.charge_count,
                )
                for card in wallet.cards
            ],
        )
