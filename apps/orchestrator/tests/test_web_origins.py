"""Which browsers may talk to this service.

A browser refuses a cross-origin request unless the server says otherwise, so without this the web
client cannot call the orchestrator at all — `fetch` fails before the request is even useful. That
makes CORS the frontend's first dependency rather than a detail.

It is an allow-list of exact origins and never `*`. The credential travels in an `Authorization`
header, and `*` plus credentials is the configuration that lets any page a user visits spend their
session. Every origin here is loopback, which is rule 13: development credentials do not leave
127.0.0.1.
"""

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.bootstrap.main import app

client = TestClient(app)

ALLOWED = "http://127.0.0.1:3000"


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        # Vite's own default, so `npm run dev` works before anybody sets a port.
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
)
def test_the_web_client_may_call_the_service(origin: str) -> None:
    response = client.options(
        "/v1/questions",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_a_page_on_the_internet_may_not() -> None:
    """The reason this is a list and not a wildcard. With credentials in a header, `*` means any
    page a user happens to visit can spend their session."""
    response = client.options(
        "/v1/questions",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )

    assert response.headers.get("access-control-allow-origin") != "https://evil.example"


def test_the_credential_header_is_allowed_through() -> None:
    """Not implied by the origin being allowed: a preflight that does not list `authorization`
    among the permitted headers fails on the header rather than on the origin, which reads as a
    CORS problem and is a different one."""
    response = client.options(
        "/v1/questions",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert "authorization" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize(
    "route", ["/v1/questions", "/v1/questions/stream", "/v1/conversation/actions"]
)
def test_every_header_the_client_sends_survives_a_preflight(route: str) -> None:
    """The failure this catches took the whole chat down while every route answered `curl` fine.

    `Idempotency-Key` was added to the client so a resent payment is one intent instead of two. It
    was not added here — and a header the routes read but CORS does not list is not one the browser
    quietly drops: the preflight is refused and the request is never sent at all. Every message came
    back "no puedo hablar con el orquestador" from a service that was up and healthy.

    Parametrised over the routes the browser posts to, because the chat prepares payments too: a
    header allowed on one and not the others is the same outage on a narrower path.
    """
    response = client.options(
        route,
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
        },
    )

    assert response.status_code == 200, "the browser would never send the request"
    allowed = response.headers["access-control-allow-headers"].lower()
    for sent in ("authorization", "content-type", "idempotency-key"):
        assert sent in allowed


def test_an_answer_carries_the_origin_back() -> None:
    """The preflight is not enough: the actual response needs the header too, or the browser
    discards a 200 that arrived perfectly well."""
    response = client.get("/health", headers={"Origin": ALLOWED})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
