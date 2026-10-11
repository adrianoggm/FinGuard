"""Reads the contact list from banking. Hand-written, like the other clients.

There is nothing to interpret here, which is the point of it being separate from the payment
client: listing who somebody can pay is a read, it goes through banking's restricted role, and it
cannot move money however wrong it is.
"""

from typing import Any
from uuid import UUID

import httpx2 as httpx

from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.infrastructure.banking.client import banking_client


class HttpContacts(ContactDirectoryPort):
    """Implements ContactDirectoryPort over banking's HTTP API."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def all_of(self, account_id: UUID) -> tuple[Contact, ...]:
        url = f"{self._base_url}/v1/accounts/{account_id}/contacts"
        try:
            async with banking_client(timeout=self.timeout_seconds) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise BankingUnavailableError(f"{url}: {exc}") from exc

        if response.status_code != 200:
            raise BankingUnavailableError(f"HTTP {response.status_code} reading the contacts")

        try:
            body: Any = response.json()
            return tuple(
                Contact(
                    contact_id=UUID(row["contactId"]),
                    display_name=row["displayName"],
                    phone=row["phone"],
                )
                for row in body["contacts"]
            )
        except (ValueError, KeyError, TypeError) as exc:
            # An empty list is an answer; an unreadable one is not. A screen that showed no contacts
            # because the body was malformed would look exactly like an account with none.
            raise BankingUnavailableError(f"unreadable contacts: {exc}") from exc
