from finguard_banking.domain.financial_query import FinancialQuery
from finguard_banking.domain.financial_query_result import FinancialQueryResult
from finguard_banking.domain.ports.financial_query_repository import FinancialQueryRepository


class RunFinancialQuery:
    """Resolves a financial query.

    The plan arrives already validated by the domain. The check that the caller owns
    the account will go here (§12 of the vision).
    """

    def __init__(self, queries: FinancialQueryRepository) -> None:
        self._queries = queries

    async def __call__(self, query: FinancialQuery) -> FinancialQueryResult:
        return await self._queries.run(query)
