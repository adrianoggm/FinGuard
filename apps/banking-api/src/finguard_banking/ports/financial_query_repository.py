from typing import Protocol

from finguard_banking.domain.financial_query import FinancialQuery
from finguard_banking.domain.financial_query_result import FinancialQueryResult


class FinancialQueryRepository(Protocol):
    """Ejecuta un plan de consulta ya validado."""

    async def run(self, query: FinancialQuery) -> FinancialQueryResult:
        """Lanza AccountNotFoundError si la cuenta no existe."""
        ...
