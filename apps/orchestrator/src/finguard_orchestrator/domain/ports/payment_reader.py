from typing import Protocol, runtime_checkable

from finguard_orchestrator.domain.models.payment_reading import PaymentReading
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy


@runtime_checkable
class PaymentReaderPort(Protocol):
    """Turns one Spanish sentence into a payment request, a question, or a refusal.

    It answers with a PaymentReading and raises nothing: a provider that is down is one of the
    outcomes, because the caller has to say something to the user either way.

    The grounding check belongs to an implementation rather than to the caller, and that is the
    point of it living behind this port: a reading that does not correspond to its sentence never
    leaves. A caller cannot forget to check, because there is nothing ungrounded to be handed.

    `policy` is here so the evaluation can switch a rule off and measure what it was worth. It is
    not a deployment knob for skipping the checks — the default is every rule on.
    """

    async def read(self, sentence: str, *, policy: GuardPolicy = EVERY_RULE) -> PaymentReading: ...
