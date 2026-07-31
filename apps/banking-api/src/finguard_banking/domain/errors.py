from uuid import UUID


class BankingError(Exception):
    """A domain error of the banking core."""


class AccountNotFoundError(BankingError):
    def __init__(self, account_id: UUID) -> None:
        self.account_id = account_id
        super().__init__(f"account {account_id} does not exist")
