"""Domain error → HTTP status. The rule, in one place.

Translating a domain error into a status code is an inbound-adapter responsibility, not
the composition root's. Handlers match on `DomainError.code`, never on message text.

    not found        404  the account does not exist
    invalid input    422  the domain rejected the values
    currency clash   409  a request that combines currencies is a conflict, not a typo
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
}
_DEFAULT_STATUS = 400


def register(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
        code = exc.code if isinstance(exc, DomainError) else "domain_error"
        return JSONResponse(
            {"code": code, "detail": str(exc)},
            status_code=_STATUS_BY_CODE.get(code, _DEFAULT_STATUS),
        )

    @app.exception_handler(ValidationError)
    async def _invalid_input(request: Request, exc: Exception) -> JSONResponse:
        """A plan the domain rejects is a 422, not a server error."""
        detail = exc.errors() if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        return JSONResponse(
            {"code": "invalid_input", "detail": jsonable_encoder(detail)}, status_code=422
        )
