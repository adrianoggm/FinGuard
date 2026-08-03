from datetime import date
from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan


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
    ) -> QueryAnswer: ...
