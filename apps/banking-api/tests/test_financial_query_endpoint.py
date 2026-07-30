"""El endpoint §13.2: forma del contrato y qué cuerpos rechaza."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from finguard_banking.application.run_financial_query import RunFinancialQuery
from finguard_banking.bootstrap.dependencies import run_financial_query
from finguard_banking.bootstrap.main import app
from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.financial_query import FinancialQuery
from finguard_banking.domain.financial_query_result import (
    FinancialQueryResult,
    Movement,
    QueryGroup,
)

CUENTA = uuid.UUID("11111111-1111-1111-1111-111111111111")
TRANSACCION = uuid.UUID("22222222-2222-2222-2222-222222222222")

CUERPO = {
    "accountId": str(CUENTA),
    "metric": "SUM",
    "direction": "EXPENSE",
    "categories": ["TRANSPORT_FUEL"],
    "dateRange": {"from": "2026-07-01", "to": "2026-07-31"},
    "groupBy": "WEEK",
}

RESULTADO = FinancialQueryResult(
    account_id=CUENTA,
    currency="EUR",
    total_minor=12_743,
    movement_count=3,
    groups=[QueryGroup(key="2026-07-06", total_minor=3_420, movement_count=1)],
    supporting_movements=[
        Movement(
            transaction_id=TRANSACCION,
            occurred_at=datetime(2026, 7, 7, 10, 30, tzinfo=UTC),
            description="Compra en Repsol",
            merchant_name="Repsol",
            category="TRANSPORT_FUEL",
            amount_minor=3_420,
            direction="EXPENSE",
        )
    ],
)


class RepositorioFalso:
    def __init__(self, resultado: FinancialQueryResult | None) -> None:
        self._resultado = resultado
        self.recibida: FinancialQuery | None = None

    async def run(self, query: FinancialQuery) -> FinancialQueryResult:
        self.recibida = query
        if self._resultado is None:
            raise AccountNotFoundError(query.account_id)
        return self._resultado


def _con_repositorio(resultado: FinancialQueryResult | None) -> TestClient:
    app.dependency_overrides[run_financial_query] = lambda: RunFinancialQuery(
        RepositorioFalso(resultado)
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _limpiar_overrides() -> None:
    app.dependency_overrides.clear()


def test_responde_con_los_nombres_del_contrato() -> None:
    response = _con_repositorio(RESULTADO).post("/v1/financial-queries", json=CUERPO)

    assert response.status_code == 200
    assert response.json() == {
        "accountId": str(CUENTA),
        "currency": "EUR",
        "totalMinor": 12_743,
        "movementCount": 3,
        "groups": [{"key": "2026-07-06", "totalMinor": 3_420, "movementCount": 1}],
        "supportingMovements": [
            {
                "transactionId": str(TRANSACCION),
                "occurredAt": "2026-07-07T10:30:00Z",
                "description": "Compra en Repsol",
                "merchantName": "Repsol",
                "category": "TRANSPORT_FUEL",
                "amountMinor": 3_420,
                "direction": "EXPENSE",
            }
        ],
        "trace": None,
    }


def test_una_cuenta_inexistente_devuelve_404() -> None:
    response = _con_repositorio(None).post("/v1/financial-queries", json=CUERPO)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("groupBy", "TRIMESTRE"),
        ("direction", "TODO"),
        ("metric", "MEDIANA"),
        ("supportingLimit", 5_000),
        ("categories", ["gasolina; DROP TABLE accounts"]),
    ],
)
def test_un_plan_fuera_del_contrato_se_rechaza(campo: str, valor: object) -> None:
    response = _con_repositorio(RESULTADO).post(
        "/v1/financial-queries", json={**CUERPO, campo: valor}
    )

    assert response.status_code == 422


def test_no_se_puede_colar_sql_como_campo_extra() -> None:
    response = _con_repositorio(RESULTADO).post(
        "/v1/financial-queries", json={**CUERPO, "sql": "SELECT * FROM ledger_entries"}
    )

    assert response.status_code == 422


def test_un_periodo_relativo_se_resuelve_antes_de_llegar_al_dominio() -> None:
    repositorio = RepositorioFalso(RESULTADO)
    app.dependency_overrides[run_financial_query] = lambda: RunFinancialQuery(repositorio)
    cuerpo = {k: v for k, v in CUERPO.items() if k != "dateRange"}

    response = TestClient(app).post(
        "/v1/financial-queries",
        json={**cuerpo, "period": {"period": "LAST_MONTH", "reference": "2026-07-15"}},
    )

    assert response.status_code == 200
    assert repositorio.recibida is not None
    # El dominio solo ve fechas absolutas: la resolución ocurre en el borde.
    assert repositorio.recibida.date_range.desde.isoformat() == "2026-06-01"
    assert repositorio.recibida.date_range.hasta.isoformat() == "2026-06-30"


def test_hay_que_indicar_el_periodo_de_una_sola_forma() -> None:
    cliente = _con_repositorio(RESULTADO)
    sin_rango = {k: v for k, v in CUERPO.items() if k != "dateRange"}

    ambos = cliente.post(
        "/v1/financial-queries", json={**CUERPO, "period": {"period": "LAST_MONTH"}}
    )
    ninguno = cliente.post("/v1/financial-queries", json=sin_rango)

    assert ambos.status_code == 422
    assert ninguno.status_code == 422


def test_un_rango_al_reves_se_rechaza() -> None:
    response = _con_repositorio(RESULTADO).post(
        "/v1/financial-queries",
        json={**CUERPO, "dateRange": {"from": "2026-07-31", "to": "2026-07-01"}},
    )

    assert response.status_code == 422
