"""The holder's other accounts, from banking's response to the wire.

Three seams and a defect waiting at each: the client that parses banking's JSON, the handler that
flattens it, and the mapper that names the fields. A field lost at any of them looks identical from
the outside — a holder with three accounts shown as having one — and nothing else fails.

The last test is the one to keep. This has to be *additive*: the balance at the top is still the
declared account's, and a client written before there were several accounts must go on reading the
same figures rather than silently starting to read a different one.
"""

import uuid
from datetime import UTC, datetime

from finguard_orchestrator.application.queries.get_account_summary import (
    GetAccountSummaryHandler,
    GetAccountSummaryQuery,
)
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.infrastructure.mappers import llm_mapper

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
PIGGY = uuid.UUID("22222222-2222-2222-2222-222222222222")
FUND = uuid.UUID("33333333-3333-3333-3333-333333333333")
TODAY = datetime(2026, 7, 15, tzinfo=UTC).date()

THREE_ACCOUNTS = AccountSummary(
    currency="EUR",
    booked_minor=227_042,
    available_minor=227_042,
    as_of=datetime(2026, 7, 15, 9, 30, tzinfo=UTC),
    holdings=(
        Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042),
        Holding(PIGGY, "SAVINGS", "Hucha", "EUR", 548_550),
        Holding(FUND, "ASSET", "Fondo indexado", "EUR", 270_000),
    ),
)


class FakeBank:
    def __init__(self, summary: AccountSummary) -> None:
        self._summary = summary

    async def of(self, account_id: uuid.UUID, **_: object) -> AccountSummary:
        return self._summary


async def result_for(summary: AccountSummary):  # type: ignore[no-untyped-def]
    handler = GetAccountSummaryHandler(FakeBank(summary))
    return await handler.handle(GetAccountSummaryQuery(account_id=ACCOUNT, today=TODAY))


async def test_every_account_survives_the_handler() -> None:
    result = await result_for(THREE_ACCOUNTS)

    assert [holding.display_name for holding in result.holdings] == [
        "Cuenta corriente",
        "Hucha",
        "Fondo indexado",
    ]


async def test_the_kind_reaches_the_wire() -> None:
    """The field the screen has to read before adding anything up. On an ASSET the balance is what
    was paid and not what it is worth, so a client that could not tell the kinds apart would total
    all three and call a purchase price somebody's money."""
    response = llm_mapper.summary_to_dto(await result_for(THREE_ACCOUNTS))

    assert [holding.kind for holding in response.holdings] == ["CURRENT", "SAVINGS", "ASSET"]


async def test_the_wire_names_are_the_contract_names() -> None:
    """snake_case, like every other response this service sends. The asymmetry is real and worth
    not tripping over: banking answers *this* service in camelCase — which is what `HttpAccounts`
    parses — and this service answers the client in snake_case, which is what `api.ts` reads."""
    response = llm_mapper.summary_to_dto(await result_for(THREE_ACCOUNTS))

    assert response.model_dump()["holdings"][1] == {
        "account_id": str(PIGGY),
        "kind": "SAVINGS",
        "display_name": "Hucha",
        "currency": "EUR",
        "booked_minor": 548_550,
    }


async def test_an_account_nobody_named_keeps_no_name() -> None:
    """Nullable in banking's schema and nullable all the way here. Falling back to the reference
    would put "ES-0001-2" where a name goes, which is a string nobody recognises."""
    unnamed = AccountSummary(
        currency="EUR",
        booked_minor=0,
        available_minor=0,
        as_of=datetime(2026, 7, 15, 9, 30, tzinfo=UTC),
        holdings=(Holding(ACCOUNT, "CURRENT", None, "EUR", 0),),
    )

    response = llm_mapper.summary_to_dto(await result_for(unnamed))

    assert response.holdings[0].display_name is None


async def test_the_balance_at_the_top_is_still_only_the_declared_account() -> None:
    """The property that makes this additive, and the one worth keeping.

    Three accounts holding 10.455,92 € between them, and `bookedMinor` stays 2.270,42 — the
    declared account's. A home screen written before this change goes on showing exactly what it
    showed. If this ever starts returning the sum, every existing client silently begins reporting
    a different figure under the same label, which is the failure this whole phase is about.
    """
    response = llm_mapper.summary_to_dto(await result_for(THREE_ACCOUNTS))

    assert response.booked_minor == 227_042
    assert sum(holding.booked_minor for holding in response.holdings) == 1_045_592


async def test_a_summary_with_no_holdings_is_still_a_summary() -> None:
    """The default, so nothing that builds an `AccountSummary` without them has to change — and so
    a banking that has not been deployed yet degrades to what the screen showed yesterday."""
    plain = AccountSummary(
        currency="EUR",
        booked_minor=227_042,
        available_minor=227_042,
        as_of=datetime(2026, 7, 15, 9, 30, tzinfo=UTC),
    )

    response = llm_mapper.summary_to_dto(await result_for(plain))

    assert response.holdings == []
    assert response.booked_minor == 227_042
