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

import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Spoken
from finguard_orchestrator.domain.models.account_summary import AccountSummary, Holding
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    Conversation,
    Slot,
    TransferInProgress,
)
from finguard_orchestrator.domain.services import intent_router
from finguard_orchestrator.domain.services.intent_router import Intent


class NoCards:
    """The card directory. Required like the balance and the address book, so no build can be
    half-wired — a chat that cannot reach the wallet answered "eso no puedo calcularlo todavía"
    about a screen the user can open."""

    async def all_of(self, account_id, **kwargs):
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=__import__("datetime").date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 5)

CURRENT = Holding(ACCOUNT, "CURRENT", "Cuenta corriente", "EUR", 227_042)
PIGGY = Holding(uuid.UUID(int=2), "SAVINGS", "Hucha", "EUR", 548_550)
FUND = Holding(uuid.UUID(int=3), "ASSET", "Fondo indexado", "EUR", 270_000)
VACATIONS = Holding(uuid.UUID(int=5), "SAVINGS", "Vacaciones", "EUR", 75_000)


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


async def said(sentence: str, of: AccountSummary | None = None, was: Conversation = IDLE):  # type: ignore[no-untyped-def]
    handler = AskFinancialQuestionHandler(
        Never(),  # type: ignore[arg-type]
        Never(),  # type: ignore[arg-type]
        accounts=FakeBank(of or summary()),  # type: ignore[arg-type]
        contacts=Never(),
        cards=NoCards(),  # type: ignore[arg-type]
    )
    last = None
    async for step in handler.stream(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=was
        )
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


def test_wanting_information_about_transfers_does_not_prepare_one() -> None:
    sentence = "Quiero saber cómo transferir dinero entre mis cuentas"

    assert intent_router.route(sentence, has_previous=False) is Intent.FINANCIAL_QUERY


@pytest.mark.parametrize(
    "sentence",
    [
        "Quiero transferir dinero de una de mis cuentas a otra",
        "Tengo que pasar dinero de una cuenta mía a otra cuenta que también es mía",
        "Estoy intentando hacer una transferencia entre mis cuentas, ¿cómo lo hago?",
        "Necesito trasvasar dinero de una cuenta a otra, ambas mías",
    ],
)
def test_explicitly_owned_account_relationships_are_internal_transfers(sentence: str) -> None:
    assert intent_router.route(sentence, has_previous=False) is Intent.OWN_TRANSFER


def test_a_damaged_own_account_phrase_stays_on_the_safe_internal_rail() -> None:
    sentence = "Quiero mover dinreo de una cuenta a otr, ¿es posible?"

    assert intent_router.route(sentence, has_previous=False) is Intent.OWN_TRANSFER


def test_two_generic_accounts_offer_only_the_holders_internal_accounts() -> None:
    sentence = "Quiero transferir dinero de una cuenta a otra cuenta"

    # OWN_TRANSFER can only resolve an account from the authenticated holder's directory; this
    # route cannot invent an external beneficiary or prepare a third-party transfer.
    assert intent_router.route(sentence, has_previous=False) is Intent.OWN_TRANSFER


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


async def test_an_unknown_custom_account_keeps_the_transfer_open_without_guessing() -> None:
    answer = await said("mueve 200 € a la cuenta Empresa", summary(CURRENT, VACATIONS))

    assert answer.conversation.awaiting is Slot.ACCOUNT
    assert "No encuentro una cuenta con ese nombre" in answer.text
    assert "Cuenta corriente" in answer.text and "Vacaciones" in answer.text
    active = answer.conversation.active
    assert isinstance(active, TransferInProgress)
    assert active.amount_minor == 20_000


# --- the one question this service asks about an account --------------------------------


async def test_two_accounts_of_one_kind_is_asked_about_and_the_answer_is_heard() -> None:
    """The ambiguity is real, so the question has to be asked — and for one release it was a
    question this path could not hear the answer to.

    Nothing here held state: reading a sentence was all it did, and preparing is a
    `payments:prepare` scope on another route. So the amount was already gone when the reply
    arrived, and "¿a cuál lo muevo?" answered "a Hucha" fell through to the financial planner, which
    asked which period. Reported twice from real conversations, once for this question and once for
    the amount.

    The turn keeps what it read, so the reply only has to name the account.
    """
    other = Holding(uuid.UUID(int=4), "SAVINGS", "Hucha viajes", "EUR", 100_00)
    accounts = summary(CURRENT, PIGGY, other)

    asked = await said("mueve 200 € a la hucha", accounts)

    assert "Hucha" in asked.text and "Hucha viajes" in asked.text
    assert asked.conversation.awaiting is Slot.ACCOUNT
    active = asked.conversation.active
    assert isinstance(active, TransferInProgress)
    assert active.amount_minor == 200_00, "the amount was dropped and cannot be asked for twice"

    finished = await said("a Hucha viajes", accounts, was=asked.conversation)

    assert finished.outcome == Spoken.TRANSFER_READY.value
    assert finished.total_minor == 200_00
    assert "Hucha viajes" in finished.text
    assert finished.conversation.active is None


async def test_the_amount_asked_for_is_the_amount_it_hears() -> None:
    """The other half of the same defect, and the turn that reported it: "¿Cuánto quieres mover a
    Hucha?" answered "1000 pavos" came back "¿de qué periodo?"."""
    asked = await said("quiero traspasar del fondo a hucha")
    assert asked.conversation.awaiting is Slot.AMOUNT

    finished = await said("1000 pavos", was=asked.conversation)

    assert finished.outcome == Spoken.TRANSFER_READY.value
    assert finished.total_minor == 100_000
    assert "Hucha" in finished.text


async def test_a_transfer_hears_an_amount_written_in_words() -> None:
    answer = await said("mueve doscientos euros a la hucha")

    assert answer.outcome == Spoken.TRANSFER_READY.value
    assert answer.total_minor == 20_000


async def test_a_transfer_resolves_the_holders_custom_account_name() -> None:
    answer = await said(
        "mueve doscientos euros a la cuenta Vacaciones",
        summary(CURRENT, PIGGY, VACATIONS),
    )

    assert answer.outcome == Spoken.TRANSFER_READY.value
    assert answer.total_minor == 20_000
    assert "Vacaciones" in answer.text


async def test_a_custom_name_answers_the_account_choice_the_service_asked() -> None:
    accounts = summary(CURRENT, VACATIONS)
    asked = await said("quiero transferir dinero entre mis cuentas", accounts)

    finished = await said("Vacaciones", accounts, was=asked.conversation)

    assert finished.conversation.awaiting is Slot.AMOUNT
    assert "Vacaciones" in finished.text


async def test_a_transfer_that_names_no_account_asks_which_rather_than_refusing() -> None:
    """ "Me gustaría transferir dinero entre cuentas" is an instruction with the destination left
    out. It used to reach the capability guard and be refused with a sentence about *querying* the
    transfers between accounts — a refusal about reading, to somebody asking to write."""
    answer = await said("me gustaria transferir dinero entre cuentas")

    assert answer.conversation.awaiting is Slot.ACCOUNT
    assert "Hucha" in answer.text and "Cuenta corriente" in answer.text
