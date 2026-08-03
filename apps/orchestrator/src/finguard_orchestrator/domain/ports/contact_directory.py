from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.models.contact import Contact


@runtime_checkable
class ContactDirectoryPort(Protocol):
    """Everybody an account can pay.

    A list rather than a search, because the caller is a screen. Resolving a *name* stays where it
    belongs: inside banking, which owns the ambiguity rule and will not choose between two Martas.
    """

    async def all_of(self, account_id: UUID) -> tuple[Contact, ...]: ...
