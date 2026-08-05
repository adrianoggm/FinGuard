"""Where a payment goes, as one value.

Two rails and two shapes, and they are not two settings of one operation. A Bizum names a person
by phone and the bank finds the account; a transfer names the account and there may be no person
in the contact list at all. What has to be on the preview differs, what the intent has to remember
differs, and what can go wrong differs.

Written as a union rather than a `rail` field beside four nullable columns, for the reason the
schema's destination check is written per rail: the nullable version admits a Bizum with an IBAN
and a transfer with neither, and something eventually sets them. Here those states cannot be
spelled — `ToPhone` has no account and `ToAccount` has one, always.

`rail_of` is the tag, for the two places that need a name: the column, and a caller switching on
what to render. Deriving it with `isinstance` at every layer would put the same two-way decision in
each of them.
"""

from dataclasses import dataclass
from uuid import UUID

from finguard_banking.domain.enums import Rail
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.value_objects.iban import Iban


@dataclass(frozen=True)
class ToPhone:
    """A Bizum. The contact is the destination — the bank resolves the account behind the number.

    The name and the phone both travel because a preview showing only the name asks the user to
    trust that the right number was chosen, and one showing only the number asks them to recognise
    it.
    """

    contact_id: UUID
    name: str
    phone: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvalidValueError("a payment names who it is going to")


@dataclass(frozen=True)
class ToAccount:
    """A transfer. The account is the destination, and the name is what is printed beside it.

    `contact_id` is present when the account came from a favourite and absent when the holder typed
    it. It is kept for the audit trail rather than for resolution: the IBAN here is the one the
    preview showed, and confirming reads *this*, never the contact row it may have come from. A
    contact edited between preparing and confirming must not move money that was already approved.

    The name is not verified against the account and cannot be. Banks check the number and, at
    best, warn about the name — so the name is what makes the preview readable, and the IBAN is
    what makes it correct.
    """

    name: str
    iban: Iban
    contact_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvalidValueError("a transfer names who it is going to")


@dataclass(frozen=True)
class ToOwnAccount:
    """Another account of the same holder. The money moves and never leaves.

    An account id, not an IBAN. Routing this through a number would mean resolving it back to a row,
    and that lookup can land on somebody else's account; an id the caller was shown cannot become a
    different account than the one they picked. What makes it *theirs* is checked where the intent
    is prepared, against the same view that listed it — a foreign key says an account exists, not
    that it is yours.

    `name` is what the preview prints, and it is the holder's own label for the account ("Hucha").
    Kept on the destination rather than read at confirmation time for the reason `ToAccount` keeps
    its IBAN: the preview is what was approved, and a label edited in between must not change what
    the person agreed to.
    """

    account_id: UUID
    name: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvalidValueError("a transfer names the account it is going to")


type Destination = ToPhone | ToAccount | ToOwnAccount

_RAILS: dict[type, Rail] = {
    ToPhone: Rail.BIZUM,
    ToAccount: Rail.TRANSFER,
    ToOwnAccount: Rail.INTERNAL,
}


def rail_of(destination: Destination) -> Rail:
    """Which rail this destination is paid over. The tag of the union, by name.

    A table rather than a chain of `isinstance`: with three members the chain grows an implicit
    "everything else" branch, and the day a fourth arrives it silently becomes one of the three.
    """
    return _RAILS[type(destination)]
