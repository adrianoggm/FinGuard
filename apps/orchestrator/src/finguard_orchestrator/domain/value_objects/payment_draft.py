"""A payment being built over several turns.

The conversation this exists for, from the report:

    "Quiero hacerle bizum a marta"   → "¿De cuánto quieres hacerle el bizum a Marta?"
    "15 euros"                       → "¿De qué periodo te refieres?"

The first turn read the recipient and asked for the amount, and then threw the recipient away.
`PaymentReading` can carry a complete request, a question, or a reason — nothing in between — so the
reply arrived as a sentence with no history, and a sentence with money and no payment verb is not a
payment, so it reached the financial planner and was asked which month it was about.

Not a `PaymentRequest`. A request is a recipient *and* an amount, and the invariants that refuse a
payment of nothing are right; this is the honest way to hold a payment that is not one yet.

**Both fields are optional and neither is trusted.** The draft crosses the wire and comes
back, so it is input: the amount is bounded here rather than at `into_request`, because a
value refused at the edge is a 422 and one refused three layers in is an apology.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.payment_request import (
    MAX_CONCEPT,
    MAX_PAYMENT_MINOR,
    MAX_RECIPIENT_NAME,
    PaymentRequest,
)


class PaymentField(StrEnum):
    """The slots a payment needs before it can be previewed.

    A concept is not here: a payment without one is still a payment, and asking for it would make
    the assistant demand a note nobody wanted to write.
    """

    RECIPIENT = "recipient"
    AMOUNT = "amount"


@dataclass(frozen=True)
class PaymentDraft:
    recipient_name: str | None = None
    # The contact the user picked from a list this assistant offered. It is what ends the "Marta
    # Vega" loop: a name that matches two contacts is ambiguous in every spelling, and the choice
    # has already been made by the time it arrives. Resolved against the account's own directory
    # before it is used, because it comes back from a client like everything else here.
    recipient_contact_id: UUID | None = None
    amount_minor: int | None = None
    currency: str = "EUR"
    concept: str | None = None

    def __post_init__(self) -> None:
        # 0 is the schema's sentinel for "no amount in the sentence" and arrives here from the
        # model. Reading it as a value would build a payment of nothing; absent is what it means.
        if self.amount_minor == 0:
            object.__setattr__(self, "amount_minor", None)
        if self.recipient_name is not None and not self.recipient_name.strip():
            object.__setattr__(self, "recipient_name", None)

        if self.amount_minor is not None:
            if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
                raise InvalidValueError("amount_minor must be an integer number of minor units")
            if self.amount_minor < 0:
                raise InvalidValueError(f"a payment cannot be negative: {self.amount_minor}")
            if self.amount_minor > MAX_PAYMENT_MINOR:
                raise InvalidValueError(
                    f"a payment of {self.amount_minor} minor units is beyond what this assistant "
                    f"will offer to make ({MAX_PAYMENT_MINOR})"
                )
        if self.recipient_name is not None and len(self.recipient_name) > MAX_RECIPIENT_NAME:
            raise InvalidValueError(
                f"a recipient's name is at most {MAX_RECIPIENT_NAME} characters"
            )
        if self.concept is not None and len(self.concept) > MAX_CONCEPT:
            raise InvalidValueError(f"a concept is at most {MAX_CONCEPT} characters")

    @property
    def missing(self) -> frozenset[PaymentField]:
        """Which slots still have to be filled before anything can be previewed.

        A set rather than a boolean, unlike the query draft: a payment really can be missing either
        of two things, and which one decides the question to ask.
        """
        absent = set()
        if self.recipient_name is None and self.recipient_contact_id is None:
            # Either names a recipient. Asking "¿a quién?" while holding a chosen contact would be
            # the assistant ignoring the button it drew.
            absent.add(PaymentField.RECIPIENT)
        if self.amount_minor is None:
            absent.add(PaymentField.AMOUNT)
        return frozenset(absent)

    def filled_with(self, turn: "PaymentDraft") -> "PaymentDraft":
        """This draft plus whatever the latest turn said.

        The new turn wins where it says something. That is what lets somebody correct themselves —
        "mejor 20" after a preview of 15, or "Marta Solis Vega" after being asked which Marta — and
        a merge that kept the older value would answer the ambiguity question with the ambiguous
        name again.
        """
        names_somebody_else = (
            turn.recipient_name is not None or turn.recipient_contact_id is not None
        )
        return replace(
            self,
            recipient_name=turn.recipient_name or self.recipient_name,
            # Cleared when the new turn names a recipient any other way. "No, mejor a Aitor" after a
            # tap is a different person, and keeping the identifier would pay the one it replaced.
            recipient_contact_id=(
                turn.recipient_contact_id if names_somebody_else else self.recipient_contact_id
            ),
            amount_minor=turn.amount_minor if turn.amount_minor is not None else self.amount_minor,
            concept=turn.concept or self.concept,
        )

    def into_request(self) -> PaymentRequest | None:
        """The payment this draft becomes, or None while a slot is still empty.

        None rather than a request with a zero in it: `PaymentRequest` refuses a payment of nothing,
        and that invariant is the reason this type exists.
        """
        if self.missing:
            return None
        assert self.recipient_name is not None and self.amount_minor is not None
        return PaymentRequest(
            recipient_name=self.recipient_name,
            recipient_contact_id=self.recipient_contact_id,
            amount_minor=self.amount_minor,
            currency=self.currency,
            concept=self.concept,
        )
