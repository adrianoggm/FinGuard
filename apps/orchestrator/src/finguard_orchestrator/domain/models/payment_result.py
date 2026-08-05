"""What banking answered about a payment: the preview before, the receipt after.

The assistant's own shapes, not banking's wire format — the client that speaks HTTP is the only
thing that knows the contract is camelCase. Stated separately from banking's models on purpose:
generating them from its OpenAPI would make one service's build depend on another's, and the drift
this risks is loud, because banking refuses what it does not recognise.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from finguard_orchestrator.domain.enums import Rail


@dataclass(frozen=True)
class PaymentPreview:
    """What the user is asked to approve, and everything they need to answer.

    The recipient by name *and* by whatever identifies where the money is going, plus the balance it
    would leave. A prompt without that last one asks for approval without saying what it costs.

    **Exactly one of the two identifiers is present**, and `rail` says which: a Bizum names a person
    by phone and the bank finds the account; a transfer names the account. Two optionals rather than
    a union here, deliberately — this mirrors what banking answered and banking's own response
    spells it the same way, so a third shape in between would be a third place for them to disagree.

    The account travels whole and unmasked. Somebody approving a transfer is being asked to
    recognise twenty-four characters, and four of them with dots in between is not something anybody
    can check against a note.
    """

    intent_id: UUID
    recipient: str
    amount_minor: int
    currency: str
    balance_before_minor: int
    balance_after_minor: int
    expires_at: datetime
    rail: Rail = Rail.BIZUM
    recipient_phone: str | None = None
    recipient_iban: str | None = None


@dataclass(frozen=True)
class PaymentReceipt:
    """Proof it happened, pointing at the ledger movement rather than at itself."""

    intent_id: UUID
    transaction_id: UUID
    recipient: str
    amount_minor: int
    currency: str
    balance_after_minor: int
    confirmed_at: datetime
    rail: Rail = Rail.BIZUM
    recipient_phone: str | None = None
    recipient_iban: str | None = None
