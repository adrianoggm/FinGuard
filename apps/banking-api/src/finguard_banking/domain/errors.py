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


class ContactHasNoAccountError(DomainError):
    """A contact who can receive a Bizum and not a transfer.

    Its own error rather than a not-found: the person exists and the holder chose them, and telling
    them "no existe" about somebody in their own contact list is a wrong answer. What is missing is
    an account number, which is a thing they can go and add.
    """

    def __init__(self, display_name: str) -> None:
        super().__init__(f"no account number saved for {display_name}")
        self.display_name = display_name


class ContactNotFoundError(DomainError):
    """No contact of this account answers to that name.

    Deliberately the same answer whether the contact belongs to somebody else or to nobody: a
    caller who can tell those apart can enumerate other people's contacts one name at a time.
    """

    code = "contact_not_found"

    def __init__(self, name: str) -> None:
        super().__init__(f"no contact matching {name!r}")


class AmbiguousContactError(DomainError):
    """More than one contact answers to that name.

    Not an internal error — it is the answer. Choosing between two Martas would be a plausible
    reply to a question nobody asked, and this is the one place in the system where that moves
    money. The candidates travel with it because the user cannot resolve it without them.
    """

    code = "ambiguous_contact"

    def __init__(self, name: str, candidates: tuple[str, ...]) -> None:
        self.candidates = candidates
        super().__init__(f"{name!r} matches several contacts: {', '.join(candidates)}")


class InsufficientFundsError(DomainError):
    """The balance does not cover the payment, checked when it is confirmed.

    Checked then and not when the intent was prepared, because the preview showed a figure that
    can be spent by another route before the confirmation arrives. Trusting the prepared figure is
    how an account goes overdrawn from two individually reasonable requests.
    """

    code = "insufficient_funds"

    def __init__(self, available_minor: int, required_minor: int) -> None:
        self.available_minor = available_minor
        self.required_minor = required_minor
        super().__init__(f"balance is {available_minor} and the payment needs {required_minor}")


class IntentNotFoundError(DomainError):
    """No intent of this account with that identifier. Same answer for somebody else's, so a
    caller cannot discover that one exists."""

    code = "intent_not_found"

    def __init__(self, intent_id: object) -> None:
        super().__init__(f"no payment intent {intent_id}")


class IntentExpiredError(DomainError):
    """The intent outlived its preview.

    The preview stated a balance and a recipient, and the user agreed to that. Hours later it is
    no longer the same statement, so the honest answer is to prepare it again rather than to
    execute an agreement nobody would make now.
    """

    code = "intent_expired"

    def __init__(self, intent_id: object) -> None:
        super().__init__(f"payment intent {intent_id} has expired; prepare it again")


class IntentNotConfirmableError(DomainError):
    """It was already confirmed, or cancelled.

    This is what makes confirming idempotent rather than dangerous: the second attempt finds a
    terminal status and stops, so a client that retries a request whose response it never saw
    does not pay twice.
    """

    code = "intent_not_confirmable"

    def __init__(self, intent_id: object, status: str) -> None:
        self.status = status
        super().__init__(f"payment intent {intent_id} is {status}")


class UnknownCategoryError(DomainError):
    """A category this ledger does not use.

    Refused rather than stored. An override the assistant can never name back — because the list it
    is constrained to does not contain it — is a label that works on one screen and nowhere else.
    """

    code = "unknown_category"

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"{category!r} is not a category this ledger uses")


class MovementNotFoundError(DomainError):
    """No movement of this account with that identifier.

    Deliberately the same answer whether the movement belongs to somebody else or to nobody: a
    caller who could tell those apart could enumerate other people's movements one at a time.
    """

    code = "movement_not_found"

    def __init__(self, transaction_id: object) -> None:
        super().__init__(f"no movement {transaction_id}")
