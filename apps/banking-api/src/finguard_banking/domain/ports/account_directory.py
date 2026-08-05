from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_banking.domain.models.holding import Holding


@runtime_checkable
class AccountDirectoryPort(Protocol):
    """Which accounts a holder has.

    One method, and no `resolve` beside it. Names are not how an account is found here: the holder
    chose `display_name` and may well have two called "Ahorro", so turning a word into an account
    is a decision about what a person meant. That belongs to whoever reads their sentence, not to
    the store that hands out rows.

    No `one(account_id)` either. Every read already declares an account and the views answer within
    that holder's scope, so a single account is a filter over this list rather than a second query
    with its own chance to forget the declaration.
    """

    async def all_of(self, account_id: UUID) -> tuple[Holding, ...]: ...
