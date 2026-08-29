"""Reads the cards from banking. Hand-written, like the other clients.

Nothing is interpreted here and nothing can move money: a card list is a read, through banking's
restricted role and its own views.
"""

from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx2 as httpx

from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.card import Card, CardCharge, Wallet
from finguard_orchestrator.infrastructure.banking.client import banking_client


class HttpCards:
    """Implements CardDirectoryPort over banking's HTTP API."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def all_of(self, account_id: UUID, *, since: date, today: date, timezone: str) -> Wallet:
        url = f"{self._base_url}/v1/accounts/{account_id}/cards?{_window(since, today, timezone)}"
        try:
            async with banking_client(timeout=self.timeout_seconds) as client:
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
        self,
        account_id: UUID,
        card_id: UUID,
        *,
        since: date,
        today: date,
        timezone: str,
        limit: int,
    ) -> tuple[CardCharge, ...]:
        url = (
            f"{self._base_url}/v1/accounts/{account_id}/cards/{card_id}/charges"
            f"?{_window(since, today, timezone)}&limit={limit}"
        )
        try:
            async with banking_client(timeout=self.timeout_seconds) as client:
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


def _window(since: date, today: date, timezone: str) -> str:
    """The window, spelled the way banking spells a relative period.

    Three fields and not one, and the two extra are the fix. Banking takes a day count and resolves
    it into a date, which it used to do against *its* clock and then bin the charges in a zone
    hardcoded one layer below that — so a holder outside the deployment's zone read the chat on
    their day and their cards on somebody else's. `reference` is the day this holder is having and
    `timezone` is what that day is measured in; sending either alone asks the far side to guess the
    other half, and its guess is a deployment setting.

    **`+ 1`, because both ends of the window are inside it.** `since` and `today` are two dates and
    the span between them is one more day than their difference — banking resolves the count back
    into a date the same way, so sending the bare difference asked for a window starting one day
    later than the one this side had computed, and the cards were read over a shorter period than
    the chat beside them.

    urlencode because a zone has a slash in it.
    """
    return urlencode(
        {
            "days": max(1, (today - since).days + 1),
            "reference": today.isoformat(),
            "timezone": timezone,
        }
    )


def _moment(written: str) -> datetime:
    """Banking writes UTC with a Z. Explicit, so a timestamp never silently becomes naive."""
    return datetime.fromisoformat(written.replace("Z", "+00:00"))
