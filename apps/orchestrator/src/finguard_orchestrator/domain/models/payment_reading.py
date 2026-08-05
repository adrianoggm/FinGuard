"""What came of reading a payment out of a sentence.

The same six outcomes as a query plan, and deliberately the same enum. "The model read something
that does not correspond to the sentence" is `INVALID_DOMAIN` whether the something was a plan or a
payment, and a parallel taxonomy meaning the same thing would only make the evaluation carry two
vocabularies for one idea.

`PLANNED` therefore means "a payment was read", which reads slightly oddly and is worth the
consistency: every outcome name already has a meaning in the answer path, in the metrics and in the
dataset.

Built through the named constructors, so an outcome cannot disagree with its payload: a PLANNED
with no request, or a question attached to a provider error, are unrepresentable rather than
discouraged.
"""

from dataclasses import dataclass

from finguard_orchestrator.domain.enums import PlanningOutcome
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest


@dataclass(frozen=True)
class PaymentReading:
    outcome: PlanningOutcome
    request: PaymentRequest | None = None
    question: str | None = None
    reason: str | None = None
    # What the model had already read when it stopped to ask. Only a question carries one, and it
    # may be absent even then: a draft is a convenience for the next turn, not a precondition for
    # asking. "¿De cuánto quieres hacerle el bizum a Marta?" knew who Marta was and threw it away.
    draft: PaymentDraft | None = None

    def __post_init__(self) -> None:
        if (self.outcome is PlanningOutcome.PLANNED) != (self.request is not None):
            raise InvalidValueError(f"{self.outcome} disagrees with the request it carries")
        needs_question = self.outcome is PlanningOutcome.NEEDS_CLARIFICATION
        if needs_question and not self.question:
            raise InvalidValueError("NEEDS_CLARIFICATION must carry the question to ask")
        if not needs_question and self.question is not None:
            raise InvalidValueError(f"{self.outcome} does not ask a question")
        if not needs_question and self.draft is not None:
            raise InvalidValueError(f"{self.outcome} has nothing half-built to carry")

    @property
    def is_payable(self) -> bool:
        """Whether banking should be asked to prepare anything. Everything else is answered by the
        assistant on its own, and none of it moves money."""
        return self.outcome is PlanningOutcome.PLANNED

    @classmethod
    def read(cls, request: PaymentRequest) -> "PaymentReading":
        return cls(PlanningOutcome.PLANNED, request=request)

    @classmethod
    def needs_clarification(
        cls, question: str, draft: "PaymentDraft | None" = None
    ) -> "PaymentReading":
        """`question` reaches the user, so it names the missing piece: "¿de cuánto?" tells them what
        to do, "ambiguous" does not.

        `draft` is what the model had already read. Optional, because the question is worth asking
        either way — but when it is here, the reply completes a payment instead of starting one.
        """
        return cls(PlanningOutcome.NEEDS_CLARIFICATION, question=question, draft=draft)

    @classmethod
    def unsupported(cls, reason: str) -> "PaymentReading":
        return cls(PlanningOutcome.UNSUPPORTED, reason=reason)

    @classmethod
    def invalid_structure(cls, reason: str) -> "PaymentReading":
        return cls(PlanningOutcome.INVALID_STRUCTURE, reason=reason)

    @classmethod
    def invalid_domain(cls, reason: str) -> "PaymentReading":
        """The reading is well formed and does not correspond to the sentence, or the domain will
        not take it. `reason` is the rule's name when a guard rejected it, so the evaluation can
        count which one."""
        return cls(PlanningOutcome.INVALID_DOMAIN, reason=reason)

    @classmethod
    def provider_error(cls, reason: str) -> "PaymentReading":
        return cls(PlanningOutcome.PROVIDER_ERROR, reason=reason)
