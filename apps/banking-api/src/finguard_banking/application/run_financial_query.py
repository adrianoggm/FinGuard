from finguard_banking.domain.financial_query import FinancialQuery
from finguard_banking.domain.financial_query_result import FinancialQueryResult
from finguard_banking.ports.financial_query_repository import FinancialQueryRepository


class RunFinancialQuery:
    """Resuelve una consulta financiera.

    El plan llega ya validado por el dominio. Aquí entrará la comprobación de que
    quien pregunta es el titular de la cuenta (§12 de la visión).
    """

    def __init__(self, queries: FinancialQueryRepository) -> None:
        self._queries = queries

    async def __call__(self, query: FinancialQuery) -> FinancialQueryResult:
        return await self._queries.run(query)
