from typing import Protocol

from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.models.financial_query_result import (
    FinancialQueryResult,
    Movement,
)


class FinancialQueryRepository(Protocol):
    """Runs an already validated query plan."""

    async def run(self, query: FinancialQuery) -> FinancialQueryResult:
        """Raises AccountNotFoundError if the account does not exist."""
        ...

    async def ranked(self, query: FinancialQuery, *, rank: int) -> Movement | None:
        """The nth largest movement matching the plan, or None when there are fewer than n.

        A separate method rather than a wider `Metric`: a maximum is one row with an identity,
        a date and a shop, not an aggregate. The filters are the same ones, which is why it takes
        the same plan.
        """
        ...
