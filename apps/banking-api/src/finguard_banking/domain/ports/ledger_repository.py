from typing import Protocol
from uuid import UUID

from finguard_banking.domain.models.balance import Balance


class LedgerRepository(Protocol):
    """Reads from the ledger. The implementation decides how, not what."""

    async def balance_of(self, account_id: UUID) -> Balance:
        """Balance of the account derived from its booked entries.

        Raises AccountNotFoundError if the account does not exist.
        """
        ...
