"""What banking answered about a payment: the preview before, the receipt after.

The assistant's own shapes, not banking's wire format — the client that speaks HTTP is the only
thing that knows the contract is camelCase. Stated separately from banking's models on purpose:
generating them from its OpenAPI would make one service's build depend on another's, and the drift
this risks is loud, because banking refuses what it does not recognise.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class PaymentPreview:
    """What the user is asked to approve, and everything they need to answer.

    The recipient by name *and* number, so somebody can recognise who this is, and the balance it
    would leave. A prompt without that last one asks for approval without saying what it costs.
    """

    intent_id: UUID
    recipient: str
    recipient_phone: str
    amount_minor: int
    currency: str
    balance_before_minor: int
    balance_after_minor: int
    expires_at: datetime


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
