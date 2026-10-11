class DomainError(Exception):
    """Base of every domain error.

    `code` is the stable identifier the inbound adapter maps to a status code, so the
    HTTP layer never has to match on message text.
    """

    code = "domain_error"


class InvalidValueError(DomainError, ValueError):
    """A value object rejected its own input.

    The domain never raises bare ValueError: an invariant that fails is a domain
    error with a code, so the adapter can map it without matching on message text.
    It is a ValueError as well, so every caller that guards a construction with
    `except ValueError` (and pydantic, which turns one into a validation error) keeps
    catching it.
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


class TurnCheckpointExpiredError(DomainError):
    """A request-local graph checkpoint exceeded retention and cannot be resumed safely."""

    code = "turn_checkpoint_expired"

    def __init__(self) -> None:
        super().__init__(
            "this turn can no longer be resumed; send it again with a new idempotency key"
        )


class CheckpointCleanupUnavailableError(DomainError):
    """A stale checkpoint could not be removed, so its reservation remains fenced."""

    code = "checkpoint_cleanup_unavailable"

    def __init__(self) -> None:
        super().__init__("conversation recovery is temporarily unavailable; retry this request")


class ProviderFailedError(DomainError):
    """A provider could not answer. With FAIL it reaches the caller; with NEXT it is
    what triggers the next attempt."""

    code = "provider_failed"

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"provider {provider!r} failed: {reason}")


class AnswerRefusedError(ProviderFailedError):
    """The provider generated an answer and then refused it against the schema it was given.

    Groq's strict mode validates after generating, so a wrong shape arrives as an error rather
    than as text. It is still an answer, not an outage: a caller that can ask again with what was
    wrong may do so. `generation` is what the model wrote, when the provider returned it. It can
    quote the person's sentence, so it is never in the message and never logged.
    """

    def __init__(self, provider: str, reason: str, *, generation: str | None = None) -> None:
        super().__init__(provider, reason)
        self.generation = generation


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


class NotAuthorisedError(DomainError):
    """Authenticated, but not for this. Distinct from NotAuthenticatedError because the
    answers differ: one is "log in", the other is "this is not yours"."""

    code = "not_authorised"

    def __init__(self, reason: str = "not authorised for this account") -> None:
        super().__init__(reason)


class FeatureDisabledError(DomainError):
    """The deployment has not turned this on. A 503 rather than a 404: the endpoint
    exists, it is simply not being served here."""

    code = "feature_disabled"


class RecipientHasNoAccountError(DomainError):
    """A contact this account can Bizum and cannot transfer to.

    Its own error rather than a not-found, because the two need different sentences. "No tengo a
    Elena en tus contactos" is wrong about somebody in the holder's own list, and the thing that is
    actually missing — an account number — is a thing they can supply.
    """

    code = "contact_has_no_account"

    def __init__(self, name: str) -> None:
        super().__init__(f"no account number saved for {name}")
        self.name = name


class RecipientNotFoundError(DomainError):
    """Nobody in this account's contacts answers to that name.

    Deliberately the same answer whether the contact belongs to somebody else or to nobody: banking
    makes that choice and this carries it, because a caller who could tell the two apart could
    enumerate other people's contacts one name at a time.
    """

    code = "recipient_not_found"

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"no contact matching {name!r}")


class AmbiguousRecipientError(DomainError):
    """More than one contact answers to that name.

    Not a failure — it is the answer. Choosing between two Martas would be the assistant deciding
    who gets the money, and the candidates travel with it because the user cannot resolve it
    without them.
    """

    code = "ambiguous_recipient"

    def __init__(self, name: str, candidates: tuple[str, ...]) -> None:
        self.name = name
        self.candidates = candidates
        super().__init__(f"{name!r} matches several contacts: {', '.join(candidates)}")


class PaymentRefusedError(DomainError):
    """Banking would not make the payment, and said why in a code.

    `reason` is banking's code rather than its sentence: the sentence the user reads is written
    here, in Spanish, and matching on prose across a service boundary is how two services start
    disagreeing about what happened.
    """

    code = "payment_refused"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"the bank refused the payment: {reason}")


class IntentGoneError(DomainError):
    """The intent is no longer a live offer: expired, cancelled, or already paid.

    All three mean the same thing to whoever is looking at the preview, which is why they are one
    error. What they must never mean is a second payment.
    """

    code = "intent_gone"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"the payment intent is no longer live: {reason}")
