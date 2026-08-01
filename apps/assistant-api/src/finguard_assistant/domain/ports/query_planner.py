from datetime import date
from typing import Protocol, runtime_checkable

from finguard_assistant.domain.models.planning_result import PlanningResult


@runtime_checkable
class QueryPlannerPort(Protocol):
    """Turns one Spanish sentence into a plan, a question, or a refusal.

    `today` is a parameter and never read from the clock, for the same reason it is a
    parameter in banking: a planner that reads the time cannot be replayed, and an
    evaluation you cannot replay is an anecdote.

    It answers with a PlanningResult and raises nothing — a provider that is down is
    one of the outcomes, not an exception, because the caller has to say something to
    the user either way.
    """

    async def plan(
        self, sentence: str, *, today: date, previous: str | None = None
    ) -> PlanningResult: ...
