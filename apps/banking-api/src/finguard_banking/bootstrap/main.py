from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from finguard_banking.adapters.inbound.http.routes import router
from finguard_banking.bootstrap.config import get_settings
from finguard_banking.domain.errors import AccountNotFoundError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Dos pools con roles distintos (vision.md §8.6): el propietario para escribir y
    # comprobar el estado, y uno de solo lectura para todo lo que se consulta. Que el
    # camino de lectura no pueda escribir no depende de que este código sea correcto.
    #
    # ponytail: si la base de datos no está, el arranque falla y se ve el error de una vez.
    # Para arrancar sin base de datos, mover la creación de los pools a /ready.
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
