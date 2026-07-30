from typing import Protocol
from uuid import UUID

from finguard_banking.domain.balance import Balance


class LedgerRepository(Protocol):
    """Lectura del libro mayor. La implementación decide cómo, no qué."""

    async def balance_of(self, account_id: UUID) -> Balance:
        """Saldo de la cuenta derivado de sus asientos contabilizados.

        Lanza AccountNotFoundError si la cuenta no existe.
        """
        ...
