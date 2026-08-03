"""What a payment looks like before and after it happens."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from finguard_banking.domain.value_objects.money import Money


@dataclass(frozen=True)
class PaymentPreview:
    """What the user is asked to confirm.

    Everything here is on screen before anything moves, which is §4.1's point: the recipient by
    name *and* number so the reader can recognise them, the amount, and what the balance becomes.
    A preview missing the resulting balance asks somebody to approve a payment without telling
    them what it leaves.

    `balance_after` is a projection and says so by being named separately from the amount. It is
    recomputed when the payment is confirmed, because the account can be spent from in between.
    """

    intent_id: UUID
    recipient: str
    recipient_phone: str
    amount: Money
    balance_before: Money
    balance_after: Money
    expires_at: datetime


@dataclass(frozen=True)
class PaymentReceipt:
    """Proof that it happened, and where to look it up.

    `transaction_id` is the ledger entry pair, not a reference the payments table invented: the
    receipt points at the movement itself, so the figure on it can be checked against the ledger
    rather than believed.
    """

    intent_id: UUID
    transaction_id: UUID
    recipient: str
    recipient_phone: str
    amount: Money
    balance_after: Money
    confirmed_at: datetime
