from fastapi.testclient import TestClient

from finguard_assistant.bootstrap.main import app

client = TestClient(app)


def setup_function() -> None:
    # Every test starts from the default policy: the state is shared.
    client.post("/admin/llm-policy/reset")


def test_health_answers_without_dependencies() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_it_starts_with_the_default_policy() -> None:
    response = client.get("/admin/llm-policy")

    assert response.status_code == 200
    assert response.json()["active"]["provider"] == "echo"
    assert response.json()["available_providers"] == ["echo"]


def test_changing_the_policy_affects_the_next_answer() -> None:
    before = client.post("/v1/messages", json={"prompt": "hola"}).json()

    change = client.put(
        "/admin/llm-policy",
        json={"provider": "echo", "model": "echo-2", "temperature": 0.9},
    )
    after = client.post("/v1/messages", json={"prompt": "hola"}).json()

    assert change.status_code == 200
    assert before["policy"]["model"] == "echo-1"
    assert after["policy"]["model"] == "echo-2"
    assert after["policy"]["temperature"] == 0.9
    # The provider echoes the model it generated with: the policy already applies.
    assert after["text"] == "[echo:echo-2] hola"


def test_an_unknown_provider_changes_nothing() -> None:
    response = client.put(
        "/admin/llm-policy",
        json={"provider": "provider-that-does-not-exist", "model": "x"},
    )

    assert response.status_code == 422
    assert response.json()["available"] == ["echo"]
    assert client.get("/admin/llm-policy").json()["active"]["provider"] == "echo"


def test_reset_returns_to_the_default_policy() -> None:
    client.put("/admin/llm-policy", json={"provider": "echo", "model": "other"})

    response = client.post("/admin/llm-policy/reset")

    assert response.status_code == 200
    assert response.json()["active"]["model"] == "echo-1"


def test_ready_warns_when_the_policy_points_at_a_missing_provider() -> None:
    # The state is forced without going through the endpoint's validation, which is
    # precisely the case /ready has to catch.
    from finguard_assistant.domain.llm_policy import LlmPolicy

    app.state.policies._current = LlmPolicy(provider="ghost", model="x")  # noqa: SLF001

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "provider": "ghost"}
