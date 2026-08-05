"""Somebody this account can pay.

The assistant's own shape, not banking's wire format. It carries the phone number because a name is
not an identity: two contacts can share one, and the number is what lets a person recognise which of
them they meant.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Contact:
    contact_id: UUID
    display_name: str
    phone: str
