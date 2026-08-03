from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_banking.domain.models.contact import Contact


@runtime_checkable
class ContactDirectoryPort(Protocol):
    """Turns a name into a person.

    Raises rather than returning None or a list: `ContactNotFoundError` and
    `AmbiguousContactError` are different answers to the user, and a caller that received an
    empty list would have to decide which — a decision that belongs here, where it can be
    made once.
    """

    async def resolve(self, account_id: UUID, name: str) -> Contact: ...
