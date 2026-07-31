from uuid import UUID


class DomainError(Exception):
    """Base of every domain error.

    `code` is the stable identifier the inbound adapter maps to a status code, so the
    HTTP layer never has to match on message text.
    """

    code = "domain_error"


class InvalidValueError(DomainError):
    """A value object rejected its own input.

    The domain never raises bare ValueError: an invariant that fails is a domain error
    with a code.
    """

    code = "invalid_input"


class CurrencyMismatchError(DomainError):
    """Two amounts in different currencies were combined.

    The database enforces this per transaction at COMMIT; this catches it in memory,
    where the arithmetic actually happens.
    """

    code = "currency_mismatch"

    def __init__(self, left: str, right: str) -> None:
        self.left = left
        self.right = right
        super().__init__(f"cannot combine {left} with {right}")


class AccountNotFoundError(DomainError):
    code = "account_not_found"

    def __init__(self, account_id: UUID) -> None:
        self.account_id = account_id
        super().__init__(f"account {account_id} does not exist")


# Kept as an alias: BankingError was the previous base name.
BankingError = DomainError
