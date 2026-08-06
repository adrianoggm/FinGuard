"""Somebody the account holder pays."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Contact:
    """A recipient, as the confirmation step shows them.

    `display_name` and `phone` travel together because a preview that shows only the name asks
    the user to trust that the right number was chosen, and one that shows only the number asks
    them to recognise it. Step 10 of the demo shows both for that reason.

    `iban` is what makes a contact a *favourite* for a transfer, and it is optional because most
    are not: a Bizum needs a phone and the bank finds the account, so a contact list built for
    Bizum has no reason to hold one. A contact without an account can be paid by Bizum and not by
    transfer, and saying that out loud is better than a transfer that fails at the gateway.
    """

    contact_id: UUID
    display_name: str
    phone: str
    iban: str | None = None

    @property
    def can_receive_a_transfer(self) -> bool:
        """Whether this contact is a destination a transfer can name."""
        return self.iban is not None
