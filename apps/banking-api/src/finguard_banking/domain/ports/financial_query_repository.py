from typing import Protocol

from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.models.financial_query_result import FinancialQueryResult


class FinancialQueryRepository(Protocol):
    """Runs an already validated query plan."""

    async def run(self, query: FinancialQuery) -> FinancialQueryResult:
        """Raises AccountNotFoundError if the account does not exist."""
        ...
