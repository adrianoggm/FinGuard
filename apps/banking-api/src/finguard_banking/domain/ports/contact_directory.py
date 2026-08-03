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

    async def all_of(self, account_id: UUID) -> tuple[Contact, ...]: ...

    """Everybody this account can pay.

    A list, not a search: an app that offers "send money" has to show who to, and asking somebody to
    type a name they can only get right from a list they cannot see is how a payment goes to the
    wrong Marta. Empty is an answer — an account with no contacts is not an error.
    """
