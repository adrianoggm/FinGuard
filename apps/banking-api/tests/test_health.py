import pytest
from fastapi.testclient import TestClient

from finguard_banking.bootstrap.main import app


def test_health_responde_sin_tocar_la_base_de_datos() -> None:
    # Sin `with` no se ejecuta el lifespan: no hay pool y /health debe contestar igual.
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
def test_ready_confirma_que_la_base_de_datos_contesta() -> None:
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}
