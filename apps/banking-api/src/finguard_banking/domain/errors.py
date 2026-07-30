from uuid import UUID


class BankingError(Exception):
    """Error de dominio del núcleo bancario."""


class AccountNotFoundError(BankingError):
    def __init__(self, account_id: UUID) -> None:
        self.account_id = account_id
        super().__init__(f"la cuenta {account_id} no existe")
