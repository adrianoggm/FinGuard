from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.models.payment_result import PaymentPreview, PaymentReceipt
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest


@runtime_checkable
class PaymentGatewayPort(Protocol):
    """The bank, as the assistant needs it: prepare, confirm, cancel.

    Every refusal is a typed error rather than a status code, because the sentence the user reads is
    written in Spanish in the application layer and matching on prose across a service boundary is
    how two services start disagreeing about what happened.

    `key` is the caller's idempotency key. Preparing is safe to repeat and moves nothing; what must
    never happen twice is a confirmation, and that is keyed on the intent itself.
    """

    async def prepare(
        self, request: PaymentRequest, *, account_id: UUID, key: str
    ) -> PaymentPreview: ...

    async def confirm(self, *, account_id: UUID, intent_id: UUID) -> PaymentReceipt: ...

    async def cancel(self, *, account_id: UUID, intent_id: UUID) -> None: ...
