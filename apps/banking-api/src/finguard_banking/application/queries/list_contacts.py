"""ListContactsQuery · Result · Handler — everybody an account can pay.

A read with no plan and no figures: the contact list is the one thing a payment screen cannot work
without, because a name typed from memory is how money reaches the wrong person.
"""

from dataclasses import dataclass, field
from uuid import UUID

from finguard_banking.application.bus.query_bus import Query
from finguard_banking.domain.ports.contact_directory import ContactDirectoryPort


@dataclass(frozen=True)
class ContactRow:
    """Flat, like every Result piece: no domain object leaves this layer."""

    contact_id: UUID
    display_name: str
    phone: str


@dataclass(frozen=True)
class ListContactsResult:
    contacts: list[ContactRow] = field(default_factory=list)


@dataclass(frozen=True)
class ListContactsQuery(Query[ListContactsResult]):
    account_id: UUID


class ListContactsHandler:
    def __init__(self, contacts: ContactDirectoryPort) -> None:
        self._contacts = contacts

    async def handle(self, query: ListContactsQuery) -> ListContactsResult:
        found = await self._contacts.all_of(query.account_id)
        return ListContactsResult(
            contacts=[
                ContactRow(
                    contact_id=contact.contact_id,
                    display_name=contact.display_name,
                    phone=contact.phone,
                )
                for contact in found
            ]
        )
