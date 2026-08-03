"""Reads the cards from banking. Hand-written, like the other clients.

Nothing is interpreted here and nothing can move money: a card list is a read, through banking's
restricted role and its own views.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

import httpx2 as httpx

from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.card import Card, CardCharge, Wallet


class HttpCards:
    """Implements CardDirectoryPort over banking's HTTP API."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def all_of(self, account_id: UUID, *, since: date) -> Wallet:
        # Banking takes a number of days and resolves the date in its own reporting zone, which is
        # the zone the charges are grouped in. Sending a date instead would mean two services
        # deciding what "since" means, and they would eventually disagree by a day.
        days = max(1, (date.today() - since).days)
        url = f"{self._base_url}/v1/accounts/{account_id}/cards?days={days}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise BankingUnavailableError(f"{url}: {exc}") from exc

        if response.status_code != 200:
            raise BankingUnavailableError(f"HTTP {response.status_code} reading the cards")

        try:
            body: Any = response.json()
            return Wallet(
                since=date.fromisoformat(body["since"]),
                cards=tuple(
                    Card(
                        card_id=UUID(row["cardId"]),
                        display_name=row["displayName"],
                        brand=row["brand"],
                        kind=row["kind"],
                        last_four=row["lastFour"],
                        expires_month=int(row["expiresMonth"]),
                        expires_year=int(row["expiresYear"]),
                        status=row["status"],
                        spending_limit_minor=(
                            int(row["spendingLimitMinor"])
                            if row.get("spendingLimitMinor") is not None
                            else None
                        ),
                        spent_minor=int(row["spentMinor"]),
                        charge_count=int(row["chargeCount"]),
                    )
                    for row in body["cards"]
                ),
            )
        except (ValueError, KeyError, TypeError) as exc:
            # An empty wallet is an answer; an unreadable one is not. A screen showing no cards
            # because the body was malformed looks exactly like an account with none.
            raise BankingUnavailableError(f"unreadable cards: {exc}") from exc

    async def charges_on(
        self, account_id: UUID, card_id: UUID, *, since: date, limit: int
    ) -> tuple[CardCharge, ...]:
        days = max(1, (date.today() - since).days)
        url = (
            f"{self._base_url}/v1/accounts/{account_id}/cards/{card_id}/charges"
            f"?days={days}&limit={limit}"
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise BankingUnavailableError(f"{url}: {exc}") from exc

        if response.status_code != 200:
            raise BankingUnavailableError(f"HTTP {response.status_code} reading the charges")

        try:
            body: Any = response.json()
            return tuple(
                CardCharge(
                    transaction_id=UUID(row["transactionId"]),
                    occurred_at=_moment(row["occurredAt"]),
                    description=row["description"],
                    merchant_name=row.get("merchantName"),
                    category=row.get("category"),
                    amount_minor=int(row["amountMinor"]),
                )
                for row in body["charges"]
            )
        except (ValueError, KeyError, TypeError) as exc:
            # An empty list is an answer — a card of another account, or a card nobody used. An
            # unreadable body is not, and a screen showing no charges for either reason looks
            # identical.
            raise BankingUnavailableError(f"unreadable charges: {exc}") from exc


def _moment(written: str) -> datetime:
    """Banking writes UTC with a Z. Explicit, so a timestamp never silently becomes naive."""
    return datetime.fromisoformat(written.replace("Z", "+00:00"))
