from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.models.account_summary import Holding


@runtime_checkable
class AccountDirectoryPort(Protocol):
    """The holder's real account names, without loading balances or recent movements."""

    async def all_of(self, account_id: UUID) -> tuple[Holding, ...]: ...
