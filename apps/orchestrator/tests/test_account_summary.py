"""What the bank app's home screen shows, and why no model is involved in it.

The screen behind the conversation needs a balance and the last few movements. Getting them by
asking the assistant "¿cuál es mi saldo?" would put a language model in the path of the first number
a user reads, and — worse — the home screen and the chat could then disagree, which is the failure
that looks worst on camera: two balances on one phone.

So the plan is built here, in code, from a window and a count that are configuration. Banking still
validates it, the figures still come from the ledger, and nothing about this path can be talked into
answering a different question.
"""

import uuid
from datetime import UTC, date, datetime

import pytest

from finguard_orchestrator.application.queries.get_account_summary import (
    GetAccountSummaryHandler,
    GetAccountSummaryQuery,
)
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.query_answer import Movement

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 7, 15)

SUMMARY = AccountSummary(
    currency="EUR",
    booked_minor=1_037_042,
    available_minor=1_037_042,
    as_of=datetime(2026, 7, 15, 9, 30, tzinfo=UTC),
    movements=(
        Movement(
            occurred_at=datetime(2026, 7, 14, 18, 20, tzinfo=UTC),
            description="Compra en Mercadona",
            amount_minor=-4_210,
            direction="EXPENSE",
            merchant_name="Mercadona",
            category="GROCERIES",
        ),
        Movement(
            occurred_at=datetime(2026, 6, 25, 8, 0, tzinfo=UTC),
            description="Nomina mensual",
            amount_minor=245_000,
            direction="INCOME",
            merchant_name="Innovasur SL",
            category="SALARY",
        ),
    ),
)


class FakeBank:
    def __init__(self, summary: AccountSummary | None = None) -> None:
        self._summary = summary
        self.asked: tuple[uuid.UUID, date, str] | None = None

    async def of(
        self, account_id: uuid.UUID, *, today: date, reporting_timezone: str
    ) -> AccountSummary:
        self.asked = (account_id, today, reporting_timezone)
        if self._summary is None:
            raise BankingUnavailableError("no")
        return self._summary


async def test_the_summary_carries_the_balance_and_the_last_movements() -> None:
    handler = GetAccountSummaryHandler(FakeBank(SUMMARY))

    result = await handler.handle(GetAccountSummaryQuery(account_id=ACCOUNT, today=TODAY))

    assert result.currency == "EUR"
    assert result.booked_minor == 1_037_042
    assert [movement.merchant_name for movement in result.movements] == [
        "Mercadona",
        "Innovasur SL",
    ]


async def test_the_holder_s_own_day_and_zone_travel_with_the_request() -> None:
    """Same reason as on the question path: a window is a date *and* a zone, and between midnight
    and 01:00 peninsular time a holder in the Canaries is on the previous day."""
    bank = FakeBank(SUMMARY)

    await handler_for(bank).handle(
        GetAccountSummaryQuery(
            account_id=ACCOUNT, today=TODAY, reporting_timezone="Atlantic/Canary"
        )
    )

    assert bank.asked == (ACCOUNT, TODAY, "Atlantic/Canary")


def handler_for(bank: FakeBank) -> GetAccountSummaryHandler:
    return GetAccountSummaryHandler(bank)


async def test_the_bank_being_down_reaches_the_caller_as_itself() -> None:
    """Not swallowed into an empty summary. A balance of 0,00 € is a figure, and showing one for
    "we could not ask" is the worst kind of wrong on a bank's home screen."""
    with pytest.raises(BankingUnavailableError):
        await handler_for(FakeBank(None)).handle(
            GetAccountSummaryQuery(account_id=ACCOUNT, today=TODAY)
        )


async def test_the_amounts_stay_signed() -> None:
    """A movement's sign is what makes it a charge or a payment in. The screen renders it; nothing
    on the way there decides to show absolute values."""
    result = await handler_for(FakeBank(SUMMARY)).handle(
        GetAccountSummaryQuery(account_id=ACCOUNT, today=TODAY)
    )

    assert [movement.amount_minor for movement in result.movements] == [-4_210, 245_000]
