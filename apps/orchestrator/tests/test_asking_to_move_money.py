"""«Mueve 200 € a la hucha», said out loud.

The action exists and a screen uses it. This is the sentence reaching it, and the whole path is
deterministic: a closed vocabulary of accounts and a closed grammar of amounts, so no model decides
anything about where money goes.

The routing is where the care is. These verbs appear in questions too — "¿cuánto paso a la hucha
cada mes?" carries both a moving verb and an account — and reading a question as an instruction
would answer somebody with an offer to move money they never asked to move.
"""

import uuid
from datetime import UTC, date, datetime

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Spoken
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.conversation import Slot, Workflow, workflow_of
from finguard_orchestrator.domain.services import intent_router
from finguard_orchestrator.domain.services.intent_router import Intent

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 5)

CURRENT = Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042)
PIGGY = Holding(uuid.UUID(int=2), "SAVINGS", "Hucha", "EUR", 548_550)
FUND = Holding(uuid.UUID(int=3), "ASSET", "Fondo indexado", "EUR", 270_000)


def summary(*holdings: Holding) -> AccountSummary:
    return AccountSummary(
        currency="EUR",
        booked_minor=227_042,
        available_minor=227_042,
        as_of=datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
        holdings=holdings or (CURRENT, PIGGY, FUND),
    )


class FakeBank:
    def __init__(self, of: AccountSummary) -> None:
        self._summary = of

    async def of(self, account_id: uuid.UUID, **_: object) -> AccountSummary:
        return self._summary


class Never:
    """Nothing on this path may reach a model, a plan or the ledger."""

    def __getattr__(self, name: str) -> object:  # pragma: no cover - the assertion is the point
        raise AssertionError(f"a transfer sentence reached {name}")


async def said(sentence: str, of: AccountSummary | None = None):  # type: ignore[no-untyped-def]
    handler = AskFinancialQuestionHandler(
        Never(),  # type: ignore[arg-type]
        Never(),  # type: ignore[arg-type]
        accounts=FakeBank(of or summary()),  # type: ignore[arg-type]
        contacts=Never(),  # type: ignore[arg-type]
    )
    last = None
    async for step in handler.stream(
        AskFinancialQuestionQuery(sentence=sentence, account_id=ACCOUNT, today=TODAY)
    ):
        if not hasattr(step, "name"):
            last = step
    assert last is not None
    return last


# --- which sentences are instructions ---------------------------------------------------


def test_a_moving_verb_and_an_account_is_an_instruction() -> None:
    for sentence in ("mueve 200 € a la hucha", "traspasa 100 al fondo", "mete 30 € en la hucha"):
        assert intent_router.route(sentence, has_previous=False) is Intent.OWN_TRANSFER


def test_a_question_carrying_the_same_words_is_not() -> None:
    """The one that matters in the router. "¿Cuánto paso a la hucha cada mes?" is a question about
    a habit, and answering it with an offer to move money is the worst shape a misreading takes
    here — the reply to a question would be a confirmation prompt."""
    for sentence in ("¿cuánto paso a la hucha cada mes?", "¿qué traspaso a la hucha?"):
        assert intent_router.route(sentence, has_previous=False) is not Intent.OWN_TRANSFER


def test_a_moving_verb_with_no_account_is_not() -> None:
    """ "Mueve el dinero" is not an instruction anybody could act on."""
    assert intent_router.route("mueve el dinero", has_previous=False) is not Intent.OWN_TRANSFER


def test_an_account_with_no_verb_is_a_question_about_it() -> None:
    assert intent_router.route("¿cuánto tengo en la hucha?", has_previous=False) is (
        Intent.ACCOUNT_BALANCE
    )


def test_a_payment_to_a_person_is_still_a_payment() -> None:
    """Checked before this one, and it must stay that way: a real payment dropped into the transfer
    path is a payment that cannot be made."""
    assert intent_router.route("hazle un bizum de 15 € a Marta", has_previous=False) is (
        Intent.PAYMENT
    )


# --- what the answer is -----------------------------------------------------------------


async def test_it_says_what_it_understood_and_where_to_confirm() -> None:
    answer = await said("mueve 200 € a la hucha")

    assert answer.outcome == Spoken.TRANSFER_READY.value
    assert "200,00 €" in answer.text
    assert "Hucha" in answer.text
    assert answer.total_minor == 200_00


async def test_it_does_not_stage_the_transfer() -> None:
    """The chat reads sentences. Preparing is a `payments:prepare` scope on another route, and a
    read path that quietly gained the ability to stage a payment would erase the seam between
    asking for something and paying for it."""
    answer = await said("mueve 200 € a la hucha")

    assert answer.conversation.active is None
    assert "confirm" in answer.text.lower()


async def test_an_amount_nobody_said_is_asked_for() -> None:
    answer = await said("pasa dinero a la hucha")

    assert "¿Cuánto" in answer.text
    assert "Hucha" in answer.text


async def test_an_account_the_holder_does_not_have_is_said_plainly() -> None:
    answer = await said("mueve 200 € a la hucha", summary(CURRENT))

    assert "No tienes ninguna cuenta de ese tipo" in answer.text


# --- the one question this service asks about an account --------------------------------


async def test_two_accounts_of_one_kind_is_the_one_thing_worth_asking() -> None:
    """`Slot.ACCOUNT` finally has an emitter, and this is why it took one.

    Every other ambiguity here is better answered with a breakdown than with a question — showing
    every account lets the reader take what they meant. An instruction cannot be answered that way:
    either the money moves or it does not, and moving it into an account nobody named is an act
    rather than a sentence.
    """
    other = Holding(uuid.UUID(int=4), "SAVINGS", "Hucha viajes", "EUR", 100_00)

    answer = await said("mueve 200 € a la hucha", summary(CURRENT, PIGGY, other))

    assert answer.conversation.awaiting is Slot.ACCOUNT
    assert workflow_of(answer.conversation.active) is Workflow.FINANCIAL_CLARIFICATION
    assert "Hucha" in answer.text and "Hucha viajes" in answer.text
    assert "¿A cuál" in answer.text
