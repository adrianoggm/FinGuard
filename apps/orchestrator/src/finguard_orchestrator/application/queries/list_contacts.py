"""ListContactsQuery · Result · Handler — everybody this account can pay.

The list a payment screen cannot work without. Asking somebody to type a name they can only get
right from a list they have not seen is how money reaches the wrong Marta. It is also what makes a
form-driven payment safe with no grounding check: the recipient was chosen, not read.
"""

from dataclasses import dataclass, field
from uuid import UUID

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort


@dataclass(frozen=True)
class ContactRow:
    contact_id: str
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
                    contact_id=str(contact.contact_id),
                    display_name=contact.display_name,
                    phone=contact.phone,
                )
                for contact in found
            ]
        )
