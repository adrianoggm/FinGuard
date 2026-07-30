from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from finguard_banking.bootstrap.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ponytail: si la base de datos no está, el arranque falla y se ve el error de una vez.
    # Para arrancar sin base de datos, mover la creación del pool a /ready.
    pool: asyncpg.Pool[Any] = await asyncpg.create_pool(
        get_settings().database_url, min_size=1, max_size=5
    )
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="FinGuard Banking API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """El proceso responde. No comprueba dependencias a propósito."""
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Listo para servir: la base de datos contesta."""
    pool: asyncpg.Pool[Any] = app.state.pool
    try:
        async with pool.acquire() as connection:
            await connection.execute("SELECT 1")
    except (OSError, asyncpg.PostgresError):
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "up"})
