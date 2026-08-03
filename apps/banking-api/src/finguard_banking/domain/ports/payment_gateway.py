from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_banking.domain.models.payment import PaymentPreview, PaymentReceipt
from finguard_banking.domain.value_objects.money import Money


@runtime_checkable
class PaymentGatewayPort(Protocol):
    """The two steps of §4.1, and the promise that the second one happens at most once.

    `key` is the caller's idempotency key, not one this layer invents: preparing twice with the
    same key is the same intention, and that has to be decidable by whoever retried, not by us.

    Confirming takes `account_id` as well as the intent. The intent already knows which account it
    belongs to, so this is redundant on purpose — it means a caller cannot confirm somebody else's
    intent by guessing an identifier, and the check does not depend on a route remembering to make
    it.
    """

    async def prepare(
        self,
        *,
        account_id: UUID,
        contact_id: UUID,
        amount: Money,
        key: str,
    ) -> PaymentPreview: ...

    async def confirm(self, *, account_id: UUID, intent_id: UUID) -> PaymentReceipt: ...

    async def cancel(self, *, account_id: UUID, intent_id: UUID) -> None: ...
