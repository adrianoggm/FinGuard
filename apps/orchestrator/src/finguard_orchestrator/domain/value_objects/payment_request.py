"""What the model read out of a sentence about paying somebody.

A request, not an order. It says who and how much, and by the time it exists it has not been
checked against anything: the sentence it came from is compared to it in `payment_grounding`, the
recipient is resolved by banking, and the user is shown a preview before a cent moves.

The invariants here are the ones no policy may switch off. A payment of nothing is not a payment,
and a negative one is a withdrawal wearing a payment's clothes.
"""

from dataclasses import dataclass
from uuid import UUID

from finguard_orchestrator.domain.constants import (
    CURRENCY_LENGTH,
    MAX_CONCEPT,
    MAX_PAYMENT_MINOR,
    MAX_RECIPIENT_NAME,
)
from finguard_orchestrator.domain.enums import Rail
from finguard_orchestrator.domain.errors import InvalidValueError


@dataclass(frozen=True)
class PaymentRequest:
    recipient_name: str
    amount_minor: int
    currency: str
    # What the payment is for, in the user's own words. Optional, because a payment without one is
    # still a payment — and it was read out of "con el concepto tapas" and then dropped on the
    # floor, because this field did not exist.
    #
    # **It reaches the confirmation prompt and no further.** Banking's prepare endpoint takes a
    # recipient, an amount and a currency, so the note is shown to the user at the moment they say
    # yes and is not stored on the intent or on the movement. Finishing that means a column, a
    # migration and a field on banking's own contract; until then this is honest about where it
    # stops, rather than implying the ledger remembers it.
    concept: str | None = None
    # Set when the user picked the recipient from a list rather than naming them. Banking prepares
    # against the identifier when it is here, which is the only way two contacts with the same
    # display name are ever told apart. The name stays because it is what the sentence is checked
    # against and what the assistant says while the payment is being built.
    recipient_contact_id: UUID | None = None
    # A recipient account supplied explicitly by the holder. It is never read from an LLM
    # operation; conversational callers must ground it in the current user turn first.
    recipient_iban: str | None = None
    # How the money leaves. Read from the sentence rather than from the model — see
    # `payment_words.rail_in` — so a rail is never something a decoder chose.
    rail: Rail = Rail.BIZUM
    # Another account of this holder, for the INTERNAL rail. Banking checks it is theirs against
    # the view that listed it; this service only carries it, and carries an id rather than a number
    # so nothing here can turn it into a different account.
    to_account_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool):
            raise InvalidValueError("amount_minor must be an integer number of minor units")
        if self.amount_minor <= 0:
            raise InvalidValueError(f"a payment must be positive, got {self.amount_minor}")
        if self.amount_minor > MAX_PAYMENT_MINOR:
            raise InvalidValueError(
                f"a payment of {self.amount_minor} minor units is beyond what this assistant "
                f"will offer to make ({MAX_PAYMENT_MINOR})"
            )
        if self.rail is Rail.INTERNAL:
            # An internal transfer has no recipient, and requiring one would mean inventing a
            # person for money that goes to an account. What it needs instead is the account, and
            # a rail without one would fall through to banking as a payment to nobody.
            if self.to_account_id is None:
                raise InvalidValueError("an internal transfer needs one of your own accounts")
            if (
                self.recipient_name.strip()
                or self.recipient_contact_id is not None
                or self.recipient_iban is not None
            ):
                raise InvalidValueError("an internal transfer goes to an account, not to a person")
            return
        if self.recipient_iban is not None:
            if self.rail is not Rail.TRANSFER:
                raise InvalidValueError("a typed account number is only a transfer destination")
            if self.recipient_contact_id is not None:
                raise InvalidValueError("a transfer uses a saved contact or a typed account")
            if not 15 <= len(self.recipient_iban) <= 34:
                raise InvalidValueError("a typed account number has an invalid length")
            if (
                not self.recipient_iban.isascii()
                or not self.recipient_iban[:2].isalpha()
                or not self.recipient_iban[2:4].isdigit()
                or not self.recipient_iban[4:].isalnum()
                or _iban_remainder(self.recipient_iban) != 1
            ):
                raise InvalidValueError("a typed account number must be alphanumeric")
        if self.to_account_id is not None:
            raise InvalidValueError("one of your own accounts is only an internal destination")
        if not self.recipient_name.strip():
            raise InvalidValueError("a payment needs somebody to pay")
        if len(self.recipient_name) > MAX_RECIPIENT_NAME:
            raise InvalidValueError(
                f"a recipient's name is at most {MAX_RECIPIENT_NAME} characters"
            )
        if self.concept is not None and len(self.concept) > MAX_CONCEPT:
            raise InvalidValueError(f"a concept is at most {MAX_CONCEPT} characters")
        if len(self.currency) != CURRENCY_LENGTH or not self.currency.isalpha():
            raise InvalidValueError(f"currency must be 3 letters, got {self.currency!r}")


def _iban_remainder(compact: str) -> int:
    rearranged = compact[4:] + compact[:4]
    digits = "".join(
        str(ord(character) - (ord("A") - 10)) if character.isalpha() else character
        for character in rearranged
    )
    return int(digits) % 97
