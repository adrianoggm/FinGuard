import pytest
from fastapi.testclient import TestClient

from finguard_banking.bootstrap.main import app


def test_health_answers_without_touching_the_database() -> None:
    # Without `with` the lifespan does not run: there is no pool and /health must
    # answer all the same.
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
def test_ready_confirms_that_the_database_answers() -> None:
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}
