"""Files a movement under a category, through banking. Hand-written, like the other clients.

The vocabulary is banking's: it refuses a category the ledger does not use, and that refusal reaches
the caller as a rejected request rather than as a label that works on one screen and nowhere else.
"""

from uuid import UUID

import httpx2 as httpx

from finguard_orchestrator.domain.errors import BankingUnavailableError, QueryRejectedError
from finguard_orchestrator.infrastructure.banking.client import banking_client


class HttpMovementCategories:
    """Implements MovementCategoryPort over banking's HTTP API."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def set(self, account_id: UUID, transaction_id: UUID, category: str | None) -> None:
        url = f"{self._base_url}/v1/accounts/{account_id}/movements/{transaction_id}/category"
        try:
            async with banking_client(timeout=self.timeout_seconds) as client:
                response = await client.put(url, json={"category": category})
        except httpx.HTTPError as exc:
            raise BankingUnavailableError(f"{url}: {exc}") from exc

        if response.status_code >= 500:
            raise BankingUnavailableError(f"HTTP {response.status_code} filing the category")
        if response.status_code >= 400:
            # A category the ledger does not use, or a movement this account has no entry in. Both
            # are our request being wrong, which reads very differently to a user than the bank
            # being down — and only one of the two is worth retrying.
            raise QueryRejectedError(f"HTTP {response.status_code}")
