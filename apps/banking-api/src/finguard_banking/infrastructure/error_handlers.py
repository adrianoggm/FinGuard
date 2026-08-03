"""Domain error → HTTP status. The rule, in one place.

Translating a domain error into a status code is an inbound-adapter responsibility, not
the composition root's. Handlers match on `DomainError.code`, never on message text.

    not found        404  the account does not exist
    invalid input    422  the domain rejected the values
    currency clash   409  a request that combines currencies is a conflict, not a typo
    gone             410  the payment intent expired; preparing it again is the way forward

The payment refusals get three different statuses because they ask the caller for three different
things: retry with a fuller name (409 ambiguous), prepare a new intent (410 expired), or do nothing
at all because the money already moved (409 not confirmable).
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from finguard_banking.domain.errors import (
    AccountNotFoundError,
    AmbiguousContactError,
    ContactNotFoundError,
    CurrencyMismatchError,
    DomainError,
    InsufficientFundsError,
    IntentExpiredError,
    IntentNotConfirmableError,
    IntentNotFoundError,
    MovementNotFoundError,
    UnknownCategoryError,
)

_STATUS_BY_CODE = {
    AccountNotFoundError.code: 404,
    ContactNotFoundError.code: 404,
    "invalid_input": 422,
    CurrencyMismatchError.code: 409,
    # 409, not 422: the request is well formed and the state is what makes it unanswerable.
    # A caller retrying with a fuller name succeeds, which is exactly what a conflict means,
    # and the candidate names travel in the body so they can.
    AmbiguousContactError.code: 409,
    IntentNotFoundError.code: 404,
    # 410 rather than 409: the intent is gone and no retry of this request can ever succeed. The
    # caller has to prepare a new one, and a status that merely said "conflict" would invite the
    # retry instead of the new intent.
    IntentExpiredError.code: 410,
    # 409, and this is the status a retried confirmation lands on: the money moved and the second
    # attempt found a terminal state. It is the idempotent outcome, not a failure to fix.
    IntentNotConfirmableError.code: 409,
    # 409 rather than 422: the request was well formed and the balance is what refused it.
    InsufficientFundsError.code: 409,
    MovementNotFoundError.code: 404,
    # 422: the request is well formed and the value is not one this ledger uses.
    UnknownCategoryError.code: 422,
}
_DEFAULT_STATUS = 400


def register(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
        code = exc.code if isinstance(exc, DomainError) else "domain_error"
        body: dict[str, object] = {"code": code, "detail": str(exc)}
        if isinstance(exc, AmbiguousContactError):
            # A field, not prose inside the message. A client that has to ask "which Marta?"
            # cannot do it by parsing a sentence, and this is the one refusal the user can answer.
            body["candidates"] = list(exc.candidates)
        return JSONResponse(body, status_code=_STATUS_BY_CODE.get(code, _DEFAULT_STATUS))

    @app.exception_handler(ValidationError)
    async def _invalid_input(request: Request, exc: Exception) -> JSONResponse:
        """A plan the domain rejects is a 422, not a server error."""
        detail = exc.errors() if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        return JSONResponse(
            {"code": "invalid_input", "detail": jsonable_encoder(detail)}, status_code=422
        )
