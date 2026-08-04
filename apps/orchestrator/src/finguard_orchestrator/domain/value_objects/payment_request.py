"""What the model read out of a sentence about paying somebody.

A request, not an order. It says who and how much, and by the time it exists it has not been
checked against anything: the sentence it came from is compared to it in `payment_grounding`, the
recipient is resolved by banking, and the user is shown a preview before a cent moves.

The invariants here are the ones no policy may switch off. A payment of nothing is not a payment,
and a negative one is a withdrawal wearing a payment's clothes.
"""

from dataclasses import dataclass
from uuid import UUID

from finguard_orchestrator.domain.errors import InvalidValueError

CURRENCY_LENGTH = 3

# The ceiling the orchestrator will present. Banking has its own, lower or equal, and enforces it
# regardless of this one — the point of having it here is that an absurd figure never reaches a
# confirmation prompt, where a distracted "sí" is all that stands between it and the ledger.
MAX_PAYMENT_MINOR = 1_000_000

# A recipient's name as the caller wrote or chose it. Long enough for a full Spanish name with two
# surnames, short enough that the field cannot carry a paragraph into the contact lookup.
MAX_RECIPIENT_NAME = 120

# A note the user wrote. Long enough for "cena del viernes con Marta y Aitor", short enough that the
# field cannot carry a paragraph into the ledger's description.
MAX_CONCEPT = 140


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
