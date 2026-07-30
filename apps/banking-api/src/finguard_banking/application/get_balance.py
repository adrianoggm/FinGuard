from uuid import UUID

from finguard_banking.domain.balance import Balance
from finguard_banking.ports.ledger_repository import LedgerRepository


class GetBalance:
    """Consulta el saldo de una cuenta.

    Hoy solo delega. Es el sitio donde entrará la comprobación de que quien
    pregunta es el titular de la cuenta (§12 de la visión), así que la capa HTTP
    nunca habla directamente con el repositorio.
    """

    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    async def __call__(self, account_id: UUID) -> Balance:
        return await self._ledger.balance_of(account_id)
