"""Which of the holder's accounts a question is about: every one, or a chosen few."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from finguard_orchestrator.domain.constants import MAX_SELECTED_ACCOUNTS
from finguard_orchestrator.domain.errors import InvalidValueError


class AccountSelectionMode(StrEnum):
    ALL = "ALL"
    SELECTED = "SELECTED"


@dataclass(frozen=True)
class AccountSelection:
    """Persistent, holder-grounded account scope for subsequent questions."""

    mode: AccountSelectionMode
    account_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is AccountSelectionMode.ALL and self.account_ids:
            raise InvalidValueError("ALL account selection cannot contain account ids")
        if self.mode is AccountSelectionMode.SELECTED and not self.account_ids:
            raise InvalidValueError("SELECTED account selection needs account ids")
        if len(set(self.account_ids)) != len(self.account_ids):
            raise InvalidValueError("account selection cannot contain duplicates")
        if len(self.account_ids) > MAX_SELECTED_ACCOUNTS:
            raise InvalidValueError(f"at most {MAX_SELECTED_ACCOUNTS} selected accounts")
