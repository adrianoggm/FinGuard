"""Reads the holder's accounts from banking. Hand-written, like the other clients.

Its own client rather than a third call inside the summary adapter, because the account list is
about to have a second reader: "¿cuánto tengo en la hucha?" needs it without needing a balance or
the last movements.

`Holding` here is not banking's `Holding`, and that is deliberate — the two services do not share
code. Banking's carries a `Money`; this one carries the `booked_minor` + `currency` pair, because
that pair is what crosses HTTP and rebuilding a value object on this side would be inventing an
agreement neither service checks.
"""

from typing import Any
from uuid import UUID

import httpx2 as httpx

from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.ports.account_directory import AccountDirectoryPort
from finguard_orchestrator.infrastructure.banking.client import banking_client


class HttpAccounts(AccountDirectoryPort):
    """Implements the account directory over banking's HTTP API."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def all_of(self, account_id: UUID) -> tuple[Holding, ...]:
        url = f"{self._base_url}/v1/accounts/{account_id}/holder-accounts"
        try:
            async with banking_client(timeout=self.timeout_seconds) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise BankingUnavailableError(f"{url}: {exc}") from exc

        if response.status_code != 200:
            raise BankingUnavailableError(f"HTTP {response.status_code} reading the accounts")

        try:
            body: Any = response.json()
            return tuple(
                Holding(
                    account_id=UUID(row["accountId"]),
                    kind=str(row["kind"]),
                    display_name=row["displayName"],
                    currency=str(row["currency"]),
                    booked_minor=int(row["bookedMinor"]),
                )
                for row in body["accounts"]
            )
        except (ValueError, KeyError, TypeError) as exc:
            # An empty list is an answer; an unreadable one is not. A screen showing one account
            # because the body was malformed looks exactly like a holder who has one.
            raise BankingUnavailableError(f"unreadable accounts: {exc}") from exc
