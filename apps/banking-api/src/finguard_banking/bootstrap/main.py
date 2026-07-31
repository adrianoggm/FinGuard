from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from finguard_banking.bootstrap.config import get_settings
from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.infrastructure.controllers.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Two pools with different roles (vision.md §8.6): the owner writes and checks
    # health, a read-only one serves everything that is queried. The read path being
    # unable to write does not depend on this code being correct.
    #
    # ponytail: if the database is down, startup fails and the error shows up at once.
    # To start without a database, move pool creation to /ready.
    settings = get_settings()
    pool: asyncpg.Pool[Any] = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=5
    )
    query_pool: asyncpg.Pool[Any] = await asyncpg.create_pool(
        settings.query_database_url, min_size=1, max_size=5
    )
    app.state.pool = pool
    app.state.query_pool = query_pool
    try:
        yield
    finally:
        await query_pool.close()
        await pool.close()


app = FastAPI(title="FinGuard Banking API", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(AccountNotFoundError)
async def account_not_found(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(ValidationError)
async def invalid_query(request: Request, exc: Exception) -> JSONResponse:
    """A plan the domain rejects is a 422, not a server error.

    Limits are validated once, in the domain; the adapter only translates.
    """
    detail = exc.errors() if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
    return JSONResponse({"detail": jsonable_encoder(detail)}, status_code=422)
