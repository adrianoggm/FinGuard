from datetime import date
from typing import Protocol, runtime_checkable

from finguard_assistant.domain.models.planning_result import PlanningResult
from finguard_assistant.domain.value_objects.financial_plan import FinancialPlan


@runtime_checkable
class QueryPlannerPort(Protocol):
    """Turns one Spanish sentence into a plan, a question, or a refusal.

    `today` is a parameter and never read from the clock, for the same reason it is a
    parameter in banking: a planner that reads the time cannot be replayed, and an
    evaluation you cannot replay is an anecdote.

    It answers with a PlanningResult and raises nothing — a provider that is down is
    one of the outcomes, not an exception, because the caller has to say something to
    the user either way.

    `previous` is a plan and not a description of one. The sentence is the only free
    text that reaches an implementation; anything else it is told has been through the
    domain's own validation first.
    """

    async def plan(
        self, sentence: str, *, today: date, previous: FinancialPlan | None = None
    ) -> PlanningResult: ...
