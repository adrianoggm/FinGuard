"""The hand-written contract for the account list, both directions.

This client exists because we chose not to generate one, and the cost of that choice is that
banking's shape is stated twice — once there and once here. These tests are what turns "stated
twice" into "checked twice", which is the same argument `test_banking_client` makes.

The direction that bites is the naming: banking answers in camelCase and this service answers its
own client in snake_case, so the translation happens exactly here and nowhere else.
"""

import uuid

import httpx2 as httpx
import pytest

from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.infrastructure.banking.http_accounts import HttpAccounts

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
PIGGY = uuid.UUID("22222222-2222-2222-2222-222222222222")

BODY = {
    "accounts": [
        {
            "accountId": str(ACCOUNT),
            "kind": "CURRENT",
            "displayName": "Cuenta corriente",
            "currency": "EUR",
            "bookedMinor": 227_042,
        },
        {
            "accountId": str(PIGGY),
            "kind": "SAVINGS",
            "displayName": "Hucha",
            "currency": "EUR",
            "bookedMinor": 548_550,
        },
    ]
}


def _reading(monkeypatch, handler) -> HttpAccounts:  # type: ignore[no-untyped-def]
    """The client with its transport replaced. `httpx2.AsyncClient` is built inside the call rather
    than injected — right for production, one client per request and no pool to leak — so a test
    reaches it here. The real class is captured first, or the replacement calls itself."""
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    return HttpAccounts(base_url="http://banking.invalid", timeout_seconds=1.0)


async def test_it_asks_the_holder_accounts_route_of_the_declared_account(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """The account is in the path because it is what banking declares, and declaring is what opens
    the holder's scope. A request to the wrong path would answer 404 rather than quietly widening,
    but a request to another account's path would answer with somebody else's accounts."""
    asked: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json=BODY)

    await _reading(monkeypatch, transport).all_of(ACCOUNT)

    assert asked == [f"http://banking.invalid/v1/accounts/{ACCOUNT}/holder-accounts"]


async def test_camel_case_becomes_the_domain_model(monkeypatch: "pytest.MonkeyPatch") -> None:
    found = await _reading(monkeypatch, lambda _: httpx.Response(200, json=BODY)).all_of(ACCOUNT)

    assert [(h.kind, h.display_name, h.booked_minor) for h in found] == [
        ("CURRENT", "Cuenta corriente", 227_042),
        ("SAVINGS", "Hucha", 548_550),
    ]
    assert found[1].account_id == PIGGY


async def test_an_account_with_no_name_keeps_none(monkeypatch: "pytest.MonkeyPatch") -> None:
    body = {"accounts": [{**BODY["accounts"][0], "displayName": None}]}

    found = await _reading(monkeypatch, lambda _: httpx.Response(200, json=body)).all_of(ACCOUNT)

    assert found[0].display_name is None


async def test_an_unreadable_body_is_not_an_empty_list(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """The one that matters. A holder shown one account because the body was malformed looks
    exactly like a holder who has one, and nothing anywhere would report a problem."""
    body = {"accounts": [{"accountId": "not-a-uuid", "kind": "CURRENT"}]}

    with pytest.raises(BankingUnavailableError):
        await _reading(monkeypatch, lambda _: httpx.Response(200, json=body)).all_of(ACCOUNT)


async def test_a_refusal_from_banking_is_not_an_empty_list(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    with pytest.raises(BankingUnavailableError):
        await _reading(monkeypatch, lambda _: httpx.Response(403, json={})).all_of(ACCOUNT)


async def test_a_bank_that_does_not_answer_is_not_an_empty_list(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    def refuses(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(BankingUnavailableError):
        await _reading(monkeypatch, refuses).all_of(ACCOUNT)


async def test_a_holder_with_one_account_is_a_list_of_one(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """Not a special case anywhere. Everybody has at least the account their credential names, so
    the single-account holder this service was built for is just the shortest list."""
    body = {"accounts": [BODY["accounts"][0]]}

    found = await _reading(monkeypatch, lambda _: httpx.Response(200, json=body)).all_of(ACCOUNT)

    assert len(found) == 1
    assert found[0].display_name == "Cuenta corriente"
