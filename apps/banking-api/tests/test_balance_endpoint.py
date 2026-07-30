"""El endpoint del contrato §13.1, sin base de datos: aquí solo se prueba la forma."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from finguard_banking.application.get_balance import GetBalance
from finguard_banking.bootstrap.dependencies import get_balance
from finguard_banking.bootstrap.main import app
from finguard_banking.domain.balance import Balance
from finguard_banking.domain.errors import AccountNotFoundError

CUENTA = uuid.UUID("11111111-1111-1111-1111-111111111111")
INSTANTE = datetime(2026, 7, 30, 9, 30, tzinfo=UTC)


class RepositorioFalso:
    def __init__(self, saldo: Balance | None) -> None:
        self._saldo = saldo

    async def balance_of(self, account_id: uuid.UUID) -> Balance:
        if self._saldo is None:
            raise AccountNotFoundError(account_id)
        return self._saldo


def _con_repositorio(saldo: Balance | None) -> TestClient:
    app.dependency_overrides[get_balance] = lambda: GetBalance(RepositorioFalso(saldo))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _limpiar_overrides() -> None:
    app.dependency_overrides.clear()


def test_devuelve_el_saldo_con_los_nombres_del_contrato() -> None:
    saldo = Balance(
        account_id=CUENTA,
        available_minor=146035,
        booked_minor=146035,
        currency="EUR",
        as_of=INSTANTE,
    )

    response = _con_repositorio(saldo).get(f"/v1/accounts/{CUENTA}/balance")

    assert response.status_code == 200
    assert response.json() == {
        "accountId": str(CUENTA),
        "availableMinor": 146035,
        "bookedMinor": 146035,
        "currency": "EUR",
        "asOf": "2026-07-30T09:30:00Z",
    }


def test_una_cuenta_inexistente_devuelve_404() -> None:
    response = _con_repositorio(None).get(f"/v1/accounts/{CUENTA}/balance")

    assert response.status_code == 404


def test_un_identificador_que_no_es_uuid_no_llega_al_dominio() -> None:
    response = _con_repositorio(None).get("/v1/accounts/no-soy-un-uuid/balance")

    assert response.status_code == 422
