from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from finguard_banking.application.get_balance import GetBalance
from finguard_banking.bootstrap.dependencies import database_answers, get_balance

Balances = Annotated[GetBalance, Depends(get_balance)]
DatabaseAnswers = Annotated[bool, Depends(database_answers)]


class BalanceResponse(BaseModel):
    """Contrato de vision.md §13.1: hacia fuera, camelCase."""

    account_id: UUID = Field(serialization_alias="accountId")
    available_minor: int = Field(serialization_alias="availableMinor")
    booked_minor: int = Field(serialization_alias="bookedMinor")
    currency: str
    as_of: datetime = Field(serialization_alias="asOf")


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """El proceso responde. No comprueba dependencias a propósito."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(database_answers: DatabaseAnswers) -> JSONResponse:
    """Listo para servir: la base de datos contesta."""
    if not database_answers:
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "up"})


@router.get("/v1/accounts/{account_id}/balance")
async def read_balance(account_id: UUID, balances: Balances) -> BalanceResponse:
    balance = await balances(account_id)
    return BalanceResponse(
        account_id=balance.account_id,
        available_minor=balance.available_minor,
        booked_minor=balance.booked_minor,
        currency=balance.currency,
        as_of=balance.as_of,
    )
