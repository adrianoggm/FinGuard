from datetime import date
from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


@runtime_checkable
class FinancialQueryPort(Protocol):
    """Runs a plan against the ledger and answers with figures.

    The assistant never computes a total, never reads a movement and never sees SQL.
    Everything numeric comes from banking, which is the only service that may say what
    the money did — that separation is the reason a figure in this product can be
    trusted at all.

    `today` travels with the plan because both services resolve relative periods and
    they must resolve them against the same day. Left out, the assistant plans against
    the date it was given and banking answers against its own clock — LAST_MONTH means
    two different months and the answer is a real figure for the wrong window, with
    nothing anywhere reporting a problem.

    Raises QueryRejectedError when the plan is refused and BankingUnavailableError when
    the service cannot be reached. Two errors rather than one because they need
    different answers: the first is our mistake and worth showing, the second is not
    the user's problem to solve.
    """

    async def run(
        self,
        plan: FinancialPlan,
        *,
        account_id: UUID,
        today: date,
        reporting_timezone: str,
        movements: int | None = None,
    ) -> QueryAnswer: ...

    async def ranked(
        self,
        ranking: TopMovements,
        *,
        account_id: UUID,
        today: date,
        reporting_timezone: str,
    ) -> Movement | None:
        """The nth largest movement matching the ranking, or None when the period holds fewer.

        A method of its own rather than a flag, mirroring the two statements underneath it: the
        answers have nothing in common, so one method returning either would carry half a shape.

        None is an answer here, not a failure. "El quinto" of three movements does not exist, and
        raising would make the assistant apologise for a question it understood perfectly.
        """
        ...

    """`movements` overrides how many come back with the figure.

    A parameter rather than only a deployment setting, because the two callers want different
    things: an answer in a chat needs a handful as evidence, and a movement list *is* the
    movements. It was a screen asking for fifty and silently getting the chat's number that made
    this necessary — a limit that is accepted and ignored is worse than one that does not exist.
    """
