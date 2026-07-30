from fastapi.testclient import TestClient

from finguard_assistant.bootstrap.main import app

client = TestClient(app)


def setup_function() -> None:
    # Cada test empieza desde la política por defecto: el estado es compartido.
    client.post("/admin/llm-policy/reset")


def test_health_responde_sin_dependencias() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_arranca_con_la_politica_por_defecto() -> None:
    response = client.get("/admin/llm-policy")

    assert response.status_code == 200
    assert response.json()["active"]["provider"] == "echo"
    assert response.json()["available_providers"] == ["echo"]


def test_cambiar_la_politica_afecta_a_la_siguiente_respuesta() -> None:
    antes = client.post("/v1/messages", json={"prompt": "hola"}).json()

    cambio = client.put(
        "/admin/llm-policy",
        json={"provider": "echo", "model": "echo-2", "temperature": 0.9},
    )
    despues = client.post("/v1/messages", json={"prompt": "hola"}).json()

    assert cambio.status_code == 200
    assert antes["policy"]["model"] == "echo-1"
    assert despues["policy"]["model"] == "echo-2"
    assert despues["policy"]["temperature"] == 0.9
    # El proveedor devuelve el modelo con el que generó: la política se aplicó ya.
    assert despues["text"] == "[echo:echo-2] hola"


def test_un_proveedor_desconocido_no_cambia_nada() -> None:
    response = client.put(
        "/admin/llm-policy",
        json={"provider": "proveedor-que-no-existe", "model": "x"},
    )

    assert response.status_code == 422
    assert response.json()["available"] == ["echo"]
    assert client.get("/admin/llm-policy").json()["active"]["provider"] == "echo"


def test_reset_vuelve_a_la_politica_por_defecto() -> None:
    client.put("/admin/llm-policy", json={"provider": "echo", "model": "otro"})

    response = client.post("/admin/llm-policy/reset")

    assert response.status_code == 200
    assert response.json()["active"]["model"] == "echo-1"


def test_ready_avisa_si_la_politica_apunta_a_un_proveedor_inexistente() -> None:
    # Se fuerza el estado sin pasar por la validación del endpoint, que es
    # justo el caso que /ready tiene que detectar.
    from finguard_assistant.domain.llm_policy import LlmPolicy

    app.state.policies._current = LlmPolicy(provider="fantasma", model="x")  # noqa: SLF001

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "provider": "fantasma"}
