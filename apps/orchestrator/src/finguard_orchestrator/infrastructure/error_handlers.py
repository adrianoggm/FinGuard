"""Domain error → HTTP status. The rule, in one place.

Translating a domain error into a status code is an inbound-adapter responsibility, not
the composition root's. Handlers match on `DomainError.code`, never on message text.

    no or unknown credential    401  identity, the first layer of the security stack
    not this caller's account   403  authorisation, the second
    unknown model or provider   422  the caller asked for something that does not exist
    invalid input               422  the domain rejected the values
    every model failed          503  the service cannot answer right now
    one provider failed         502  the upstream broke and the policy said FAIL
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from finguard_orchestrator.domain.errors import (
    CheckpointCleanupUnavailableError,
    DomainError,
    FeatureDisabledError,
    GraphUnavailableError,
    NoModelAvailableError,
    NotAuthenticatedError,
    NotAuthorisedError,
    ProviderFailedError,
    TurnCheckpointExpiredError,
)

_STATUS_BY_CODE = {
    FeatureDisabledError.code: 503,
    GraphUnavailableError.code: 503,
    NotAuthenticatedError.code: 401,
    NotAuthorisedError.code: 403,
    "invalid_input": 422,
    "unknown_model": 422,
    "unknown_provider": 422,
    NoModelAvailableError.code: 503,
    ProviderFailedError.code: 502,
    TurnCheckpointExpiredError.code: 410,
    CheckpointCleanupUnavailableError.code: 503,
}
_DEFAULT_STATUS = 400


def register(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
        code = exc.code if isinstance(exc, DomainError) else "domain_error"
        body: dict[str, object] = {"code": code, "detail": str(exc)}
        # The available options are part of the answer: a 422 that does not say what is
        # valid forces the caller to guess.
        for attribute in ("available", "attempted"):
            value = getattr(exc, attribute, None)
            if value is not None:
                body[attribute] = value
        return JSONResponse(body, status_code=_STATUS_BY_CODE.get(code, _DEFAULT_STATUS))

    @app.exception_handler(ValidationError)
    async def _invalid_input(request: Request, exc: Exception) -> JSONResponse:
        """A value the domain rejects is a 422, not a server error."""
        detail = exc.errors() if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        return JSONResponse(
            {"code": "invalid_input", "detail": jsonable_encoder(detail)}, status_code=422
        )
