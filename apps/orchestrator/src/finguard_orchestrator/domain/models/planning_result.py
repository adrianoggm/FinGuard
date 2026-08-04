"""The outcome of turning one sentence into a plan.

Deliberately not `FinancialPlan | None`. A None would mean "no plan" and nothing else,
collapsing six situations that deserve six different answers to the user: asking a
question back is not the same as refusing, and neither is the same as a daemon being
down. The outcome is the answer; the plan is only what one of them carries.

Built through the named constructors rather than the dataclass, so an outcome cannot
disagree with its payload — a PLANNED with no plan, or a question attached to a
provider error, are unrepresentable rather than merely discouraged.
"""

from dataclasses import dataclass

from finguard_orchestrator.domain.enums import PlanningOutcome
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)


@dataclass(frozen=True)
class PlanningResult:
    outcome: PlanningOutcome
    plan: FinancialPlan | None = None
    question: str | None = None
    reason: str | None = None
    # What the model had already read when it stopped to ask. Only a question carries one, and
    # it may be absent even then: a draft is a convenience for the next turn, never a
    # precondition for asking.
    draft: FinancialPlanDraft | None = None
    # Which field the question is about, when it asks one. Emitted by the model rather than read
    # back out of its own Spanish, because "de este" can only be understood against the slot.
    awaiting: Slot = Slot.WINDOW

    def __post_init__(self) -> None:
        if (self.outcome is PlanningOutcome.PLANNED) != (self.plan is not None):
            raise InvalidValueError(f"{self.outcome} disagrees with the plan it carries")
        needs_question = self.outcome is PlanningOutcome.NEEDS_CLARIFICATION
        if needs_question and not self.question:
            raise InvalidValueError("NEEDS_CLARIFICATION must carry the question to ask")
        if not needs_question and self.question is not None:
            raise InvalidValueError(f"{self.outcome} does not ask a question")
        if not needs_question and self.draft is not None:
            raise InvalidValueError(f"{self.outcome} has nothing half-built to carry")

    @property
    def is_answerable(self) -> bool:
        """Whether banking should be called at all. Everything else is answered by the
        assistant on its own — which is the point: five of six outcomes never reach the
        ledger, so a wrong plan is cheaper to catch here than there."""
        return self.outcome is PlanningOutcome.PLANNED

    @classmethod
    def planned(cls, plan: FinancialPlan) -> "PlanningResult":
        return cls(PlanningOutcome.PLANNED, plan=plan)

    @classmethod
    def needs_clarification(
        cls,
        question: str,
        draft: FinancialPlanDraft | None = None,
        awaiting: Slot = Slot.WINDOW,
    ) -> "PlanningResult":
        """`question` is shown to the user, so it names the missing piece rather than
        reporting failure: "which month?" tells them what to do, "ambiguous" does not.

        `draft` is everything the model had already read. Optional, because a question is
        worth asking whether or not the rest of the reading survived — but when it is here,
        the reply can complete it instead of starting the conversation again.
        """
        return cls(
            PlanningOutcome.NEEDS_CLARIFICATION,
            question=question,
            draft=draft,
            awaiting=awaiting,
        )

    @classmethod
    def unsupported(cls, reason: str) -> "PlanningResult":
        return cls(PlanningOutcome.UNSUPPORTED, reason=reason)

    @classmethod
    def invalid_structure(cls, reason: str) -> "PlanningResult":
        return cls(PlanningOutcome.INVALID_STRUCTURE, reason=reason)

    @classmethod
    def invalid_domain(cls, reason: str) -> "PlanningResult":
        return cls(PlanningOutcome.INVALID_DOMAIN, reason=reason)

    @classmethod
    def provider_error(cls, reason: str) -> "PlanningResult":
        return cls(PlanningOutcome.PROVIDER_ERROR, reason=reason)
