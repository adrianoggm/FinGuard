class DomainError(Exception):
    """Base of every domain error.

    `code` is the stable identifier the inbound adapter maps to a status code, so the
    HTTP layer never has to match on message text.
    """

    code = "domain_error"


class InvalidValueError(DomainError):
    """A value object rejected its own input.

    The domain never raises bare ValueError: an invariant that fails is a domain
    error with a code, so the adapter can map it without matching on message text.
    """

    code = "invalid_input"


class UnknownModelError(DomainError):
    code = "unknown_model"

    def __init__(self, alias: str, available: list[str]) -> None:
        self.alias = alias
        self.available = available
        super().__init__(f"unknown model: {alias!r}; available: {available}")


class NoModelAvailableError(DomainError):
    """Every model in the fallback chain failed."""

    code = "no_model_available"

    def __init__(self, attempted: list[str]) -> None:
        self.attempted = attempted
        super().__init__(f"no model could answer; attempted: {attempted}")


class ProviderFailedError(DomainError):
    """A provider could not answer. With FAIL it reaches the caller; with NEXT it is
    what triggers the next attempt."""

    code = "provider_failed"

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"provider {provider!r} failed: {reason}")


class UnknownProviderError(DomainError):
    code = "unknown_provider"

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(f"unknown LLM provider: {name!r}; available: {available}")


class QueryRejectedError(DomainError):
    """Banking refused the plan.

    It validates independently and it is the authority, so this is where a vocabulary
    that drifted from the contract shows up — an enum value the assistant declares and
    banking does not know arrives here rather than passing silently.
    """

    code = "query_rejected"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"banking rejected the plan: {detail}")


class BankingUnavailableError(DomainError):
    """Banking could not be reached. Not the user's problem to solve, so it is kept
    apart from a rejected plan, which is ours and worth showing."""

    code = "banking_unavailable"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"banking unavailable: {reason}")


class NotAuthenticatedError(DomainError):
    """No credential, or one this deployment does not know.

    Deliberately says nothing about which: telling a caller that a token was well-formed
    but unknown is telling them how to make a better guess.
    """

    code = "not_authenticated"

    def __init__(self, reason: str = "a valid credential is required") -> None:
        super().__init__(reason)
