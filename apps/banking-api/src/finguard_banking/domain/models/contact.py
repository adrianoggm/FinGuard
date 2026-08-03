"""Somebody the account holder pays."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Contact:
    """A recipient, as the confirmation step shows them.

    `display_name` and `phone` travel together because a preview that shows only the name asks
    the user to trust that the right number was chosen, and one that shows only the number asks
    them to recognise it. Step 10 of the demo shows both for that reason.
    """

    contact_id: UUID
    display_name: str
    phone: str
