"""What the model read out of a sentence about paying somebody.

A request, not an order. It says who and how much, and by the time it exists it has not been
checked against anything: the sentence it came from is compared to it in `payment_grounding`, the
recipient is resolved by banking, and the user is shown a preview before a cent moves.

The invariants here are the ones no policy may switch off. A payment of nothing is not a payment,
and a negative one is a withdrawal wearing a payment's clothes.
"""

from dataclasses import dataclass

from finguard_orchestrator.domain.errors import InvalidValueError

CURRENCY_LENGTH = 3

# The ceiling the orchestrator will present. Banking has its own, lower or equal, and enforces it
# regardless of this one — the point of having it here is that an absurd figure never reaches a
# confirmation prompt, where a distracted "sí" is all that stands between it and the ledger.
MAX_PAYMENT_MINOR = 1_000_000

# A recipient's name as the caller wrote or chose it. Long enough for a full Spanish name with two
# surnames, short enough that the field cannot carry a paragraph into the contact lookup.
MAX_RECIPIENT_NAME = 120


@dataclass(frozen=True)
class PaymentRequest:
    recipient_name: str
    amount_minor: int
    currency: str

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
        if len(self.currency) != CURRENCY_LENGTH or not self.currency.isalpha():
            raise InvalidValueError(f"currency must be 3 letters, got {self.currency!r}")
