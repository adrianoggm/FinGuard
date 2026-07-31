from uuid import UUID

from finguard_banking.domain.balance import Balance
from finguard_banking.ports.ledger_repository import LedgerRepository


class GetBalance:
    """Reads the balance of an account.

    Today it only delegates. It is the place where the check that the caller owns
    the account will go (§12 of the vision), which is why the HTTP layer never talks
    to the repository directly.
    """

    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    async def __call__(self, account_id: UUID) -> Balance:
        return await self._ledger.balance_of(account_id)
