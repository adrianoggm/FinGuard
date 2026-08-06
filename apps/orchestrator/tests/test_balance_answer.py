"""The balance, asked in the chat.

    "Buenos días, ¿podrías decirme cuánto dinero tengo en mis cuentas?"
    → "No he encontrado movimientos del 2026-08-04 al 2026-08-04."

Three things were wrong and only one of them was the model's doing. The router had no intent for
it, so it went to the financial planner; `FinancialPlan` cannot express a balance, so the model
built the nearest thing it could reach — today's movements; and the figure it was asked for had
been available all along, with no model in the path, on the same port the home screen reads.

So this is not a new capability. It is a route to one that already existed.
"""

import uuid
from datetime import date, datetime

import pytest

from finguard_orchestrator.application import AskFinancialQuestionHandler, AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, Period, Spoken
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.conversation import (
    Conversation,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)

SUMMARY = AccountSummary(
    currency="EUR",
    booked_minor=1_042_042,
    available_minor=1_037_042,
    as_of=datetime(2026, 8, 4, 8, 12),
)


class FakeAccounts:
    def __init__(self, summary: AccountSummary | None = None, failure: Exception | None = None):
        self._summary = summary
        self._failure = failure
        self.asked_for: uuid.UUID | None = None

    async def of(
        self, account_id: uuid.UUID, *, today: date, reporting_timezone: str
    ) -> AccountSummary:
        self.asked_for = account_id
        if self._failure is not None:
            raise self._failure
        assert self._summary is not None
        return self._summary


class FakePlanner:
    def __init__(self) -> None:
        self.asked: str | None = None

    async def plan(
        self, sentence: str, *, today: date, previous: FinancialPlan | None = None
    ) -> PlanningResult:
        self.asked = sentence
        return PlanningResult.planned(
            FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(period=Period.TODAY))
        )


class FakeLedger:
    def __init__(self) -> None:
        self.asked_for: uuid.UUID | None = None

    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid"):
        self.asked_for = account_id
        return QueryAnswer(currency="EUR", total_minor=0, movement_count=0)


class FakeContacts:
    """The address book port. Required, like the balance one, so no build can be half-wired."""

    async def all_of(self, account_id: uuid.UUID) -> tuple:
        return ()


def ask(sentence: str, accounts: FakeAccounts) -> tuple[FakePlanner, FakeLedger, object]:
    planner, ledger = FakePlanner(), FakeLedger()
    handler = AskFinancialQuestionHandler(
        planner, ledger, accounts=accounts, contacts=FakeContacts()
    )
    return planner, ledger, handler


# --- the sentence -------------------------------------------------------------------


def test_the_two_balances_are_both_named_when_they_differ() -> None:
    """Booked is what the ledger has settled, available is what can be spent. Showing one and
    calling it the other tells somebody they have money they do not, so when they differ the
    answer says both and labels which is which."""
    said = answer_writer.balance(SUMMARY)

    assert said == (
        "Tienes 10.370,42 € disponibles en tu cuenta. El saldo contabilizado es de 10.420,42 €."
    )


def test_one_figure_when_there_is_nothing_pending() -> None:
    """The common case. Repeating the same number twice under two labels reads like a discrepancy
    the user has to work out, so it is said once."""
    settled = AccountSummary(
        currency="EUR",
        booked_minor=1_037_042,
        available_minor=1_037_042,
        as_of=datetime(2026, 8, 4, 8, 12),
    )

    assert answer_writer.balance(settled) == "Tienes 10.370,42 € en tu cuenta."


# --- the route ----------------------------------------------------------------------


async def test_the_balance_reaches_the_ledger_and_not_the_model() -> None:
    accounts = FakeAccounts(SUMMARY)
    planner, ledger, handler = ask("¿cuánto dinero tengo en mis cuentas?", accounts)

    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence="Buenos días, ¿podrías decirme cuánto dinero tengo en mis cuentas?",
            account_id=ACCOUNT,
            today=TODAY,
        )
    )

    assert planner.asked is None, "a balance question was sent to a model that cannot express one"
    assert ledger.asked_for is None, "the balance was answered with a movement query"
    assert accounts.asked_for == ACCOUNT
    assert result.outcome == Spoken.BALANCE.value
    assert "10.370,42 €" in result.text


async def test_the_figures_travel_with_the_answer() -> None:
    """A client showing a balance should not have to parse the sentence for it — the same
    argument as the movements travelling beside a total."""
    _, _, handler = ask("¿cuál es mi saldo?", FakeAccounts(SUMMARY))

    result = await handler.handle(
        AskFinancialQuestionQuery(sentence="¿cuál es mi saldo?", account_id=ACCOUNT, today=TODAY)
    )

    assert result.currency == "EUR"
    assert result.total_minor == 1_037_042


async def test_a_balance_question_does_not_erase_the_previous_plan() -> None:
    """It is a turn in the conversation, not a reset. Asking the balance in the middle of an
    analysis of last month must not cost the window of the question before it."""
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_MONTH),
        categories=("GROCERIES",),
    )
    _, _, handler = ask("¿cuánto tengo?", FakeAccounts(SUMMARY))

    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence="¿cuánto tengo?",
            account_id=ACCOUNT,
            today=TODAY,
            conversation=Conversation(last_plan=previous),
        )
    )

    assert result.conversation.last_plan is not None
    assert result.conversation.last_plan.period.period is Period.LAST_MONTH
    assert result.conversation.last_plan.categories == ("GROCERIES",)


async def test_a_bank_that_will_not_answer_does_not_become_a_zero() -> None:
    """The one number that must never be invented. "Tienes 0,00 €" for "we could not ask" is the
    worst wrong answer a bank can give."""
    _, _, handler = ask("¿cuánto tengo?", FakeAccounts(failure=BankingUnavailableError("down")))

    result = await handler.handle(
        AskFinancialQuestionQuery(sentence="¿cuánto tengo?", account_id=ACCOUNT, today=TODAY)
    )

    assert "0,00" not in result.text
    assert result.total_minor is None
    assert result.outcome != Spoken.BALANCE.value


@pytest.mark.parametrize(
    "sentence",
    ["¿cuánto gasté el mes pasado?", "¿cuántos movimientos tengo este mes?"],
)
async def test_a_question_about_movements_still_goes_to_the_planner(sentence: str) -> None:
    planner, _, handler = ask(sentence, FakeAccounts(SUMMARY))

    await handler.handle(
        AskFinancialQuestionQuery(sentence=sentence, account_id=ACCOUNT, today=TODAY)
    )

    assert planner.asked == sentence
