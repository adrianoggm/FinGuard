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
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan


@dataclass(frozen=True)
class PlanningResult:
    outcome: PlanningOutcome
    plan: FinancialPlan | None = None
    question: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if (self.outcome is PlanningOutcome.PLANNED) != (self.plan is not None):
            raise InvalidValueError(f"{self.outcome} disagrees with the plan it carries")
        needs_question = self.outcome is PlanningOutcome.NEEDS_CLARIFICATION
        if needs_question and not self.question:
            raise InvalidValueError("NEEDS_CLARIFICATION must carry the question to ask")
        if not needs_question and self.question is not None:
            raise InvalidValueError(f"{self.outcome} does not ask a question")

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
    def needs_clarification(cls, question: str) -> "PlanningResult":
        """`question` is shown to the user, so it names the missing piece rather than
        reporting failure: "which month?" tells them what to do, "ambiguous" does not."""
        return cls(PlanningOutcome.NEEDS_CLARIFICATION, question=question)

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
