"""«Gracias» stopped invoking a language model.

Ninety-eight of the corpus's 879 turns are a greeting, a thank-you or a goodbye, and every one of
them reached the financial planner. Six intents were about money and the seventh was a catch-all,
so a pleasantry fell into the catch-all and the catch-all plans queries — a model asked what period
«gracias» refers to.

**It was found by a model disagreeing with us.** `qwen3.5:9b` answered IDENTITY_OR_HELP for
fifty-seven of them, the closest of nine to "not about their account", and it was more right than
the rules were. That is what the shadow migration is for: a missing category is invisible until
something is forced to choose within it. See `evaluation/conversations/RESULTS.md`.

The behaviour is one line — say something back — and the tests are almost all about what must
*not* change. A pleasantry is not a turn: it does not end a subject, it does not pause a payment,
and it does not throw away a saved retry.
"""

import uuid
from datetime import date

import pytest

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Direction, Period, Spoken
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    Conversation,
    PaymentInProgress,
    Slot,
)
from finguard_orchestrator.domain.services import courtesy
from finguard_orchestrator.domain.services.intent_router import Intent, route
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft


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
LAST_MONTH = FinancialPlan(
    direction=Direction.EXPENSE, period=RelativePeriod(period=Period.LAST_MONTH)
)


class UnusedPlanner:
    """A pleasantry that reaches this is the defect, so reaching it is the failure."""

    async def plan(self, sentence: str, **kwargs: object) -> object:
        raise AssertionError(f"a language model was asked to plan a query for {sentence!r}")


class UnusedLedger:
    async def run(self, plan: object, **kwargs: object) -> object:
        raise AssertionError("the bank was read")


class NoAccounts:
    async def of(self, account_id: uuid.UUID, **kwargs: object) -> object:
        raise AssertionError("the balance was read")


class NoContacts:
    async def all_of(self, account_id: uuid.UUID) -> tuple[()]:
        return ()


def a_handler() -> AskFinancialQuestionHandler:
    return AskFinancialQuestionHandler(
        UnusedPlanner(),  # type: ignore[arg-type]
        UnusedLedger(),  # type: ignore[arg-type]
        accounts=NoAccounts(),  # type: ignore[arg-type]
        contacts=NoContacts(),
        cards=NoCards(),  # type: ignore[arg-type]
    )


async def answer(sentence: str, was: Conversation = IDLE):
    return await a_handler().handle(
        AskFinancialQuestionQuery(
            sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=was
        )
    )


# --- the vocabulary ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "gracias",
        "Gracias",
        "GRACIAS",
        "gracias!!",
        "¡gracias!",
        "muchas gracias",
        "mil gracias",
        "vale gracias",
        "te lo agradezco",
        "hola",
        "Hola!",
        "buenas",
        "buenos días",
        "buenas tardes",
        "adiós",
        "hasta luego",
        "nos vemos",
        # The model's words, not ours. It read these as courtesy while the rules sent them to a
        # planner, and there is no reading of either under which they are about money.
        "muy amable",
        "gracias, adiós",
        "gracias, hasta luego",
        "Hola, gracias por atenderme",
        "Me alegra saludarte, gracias",
        "Un saludo y muchsas gracias, adios",
        "Gracias por la ayuda, hasta luego, gracias",
        "Estoy agradecido, gracias, adiós ya",
        "Muchas gracias, fue un placer, adiós, grazias",
    ],
)
def test_these_are_pleasantries_whatever_is_open(said: str) -> None:
    """The first tier. None of these can be an answer to anything, so no state has to be consulted:
    nobody replies «gracias» to «¿de cuánto quieres el bizum?» and means fifteen euros."""
    assert route(said, has_previous=True, at_rest=False) is Intent.COURTESY


@pytest.mark.parametrize(
    "said",
    [
        "vale",
        "ok",
        "perfecto",
        "genial",
        "eso es",
        "ya",
        "bueno",
        "de acuerdo",
        "entendido",
        # Two the tier was missing rather than excluding. "Ajá" sat in an episode whose own `why`
        # says an interjection may never be answered as a question about money, and was expected as
        # a QUERY three lines below it; "me fío" is the second half of "vale, me fío", where the
        # comma rule already split the turn and only one half was manners.
        "ajá",
        "me fío",
        "vale, me fío",
    ],
)
def test_these_are_pleasantries_only_when_nothing_is_waiting(said: str) -> None:
    """The second tier, and the reason it needs the conversation. Thirty of the corpus's turns are
    a bare «vale» after an answer — manners, and every one of them reached the planner. The same
    word while a slot is open might be an answer to it, and reading it as manners eats the turn."""
    assert route(said, has_previous=True, at_rest=True) is Intent.COURTESY
    assert route(said, has_previous=True, at_rest=False) is not Intent.COURTESY


@pytest.mark.parametrize(
    "said",
    [
        # Manners with a question attached. The question is what has to be answered, so the whole
        # turn belongs to whatever answers it — the patterns are anchored end to end for this.
        "gracias, ¿y en la hucha?",
        "hola, ¿cuánto gasté el mes pasado?",
        "buenas, quiero hacer un bizum a Marta",
        # «Sí» is in neither tier and never will be: it is the word that confirms a payment, and a
        # rule that could read it as manners is a rule that could swallow a confirmation.
        "sí",
        "si",
        # And "no" for the same reason, which is the argument the module makes about "sí" read in
        # the other direction: it is the word that *declines* a payment.
        "no",
        # A question with manners on the front. The question is what has to be answered.
        "vale, ¿y en la hucha?",
        "gracias, ¿cuánto tengo?",
        "hola, dime mi saldo",
    ],
)
def test_these_are_not(said: str) -> None:
    assert route(said, has_previous=True, at_rest=True) is not Intent.COURTESY


def test_a_pleasantry_gets_an_answer_rather_than_a_plan() -> None:
    """The planner raises if it is reached, so this passing is the whole point of the change."""
    assert courtesy.reply("gracias", at_rest=True) is not None


async def test_thanks_is_answered_without_a_model() -> None:
    said = await answer("gracias")

    assert said.outcome == Spoken.COURTESY.value
    assert "de nada" in said.text.lower()


async def test_an_acknowledgement_is_not_answered_de_nada() -> None:
    """«Vale» is not gratitude. Answering it "de nada" is the kind of reply that makes somebody
    stop typing to the thing."""
    said = await answer("vale")

    assert said.outcome == Spoken.COURTESY.value
    assert "de nada" not in said.text.lower()


async def test_a_greeting_says_what_can_be_asked() -> None:
    """Different text from a thank-you, because "de nada" to «hola» reads like a machine — and it
    is the one turn where saying what this thing does is useful rather than noise."""
    said = await answer("hola")

    assert "gastos" in said.text.lower()


# --- and everything it must not disturb --------------------------------------------------


async def test_the_previous_question_survives_it() -> None:
    """«¿cuánto gasté?» → «gracias» → «¿y en la hucha?» has to still narrow the spending. If the
    pleasantry cleared the plan, the follow-up would be a question about nothing."""
    was = IDLE.answered(LAST_MONTH)

    said = await answer("gracias", was)

    assert said.conversation.last_plan == LAST_MONTH


async def test_a_half_built_payment_is_not_even_paused() -> None:
    """Every other aside pauses a Bizum, so a later bare amount cannot silently resume it after the
    subject changed. Saying thank you is not changing the subject, and pausing it would make the
    user repeat the amount they were about to give."""
    draft = PaymentDraft(recipient_name="Marta Solis Vega")
    was = IDLE.with_active(PaymentInProgress(draft=draft, awaiting=Slot.AMOUNT))

    said = await answer("gracias", was)

    active = said.conversation.active
    assert isinstance(active, PaymentInProgress)
    assert not active.suspended


async def test_a_saved_retry_survives_it() -> None:
    """A pleasantry between a bank outage and the retry must not throw the query away while the
    button offering it is still on screen."""
    was = IDLE.awaiting_retry(LAST_MONTH)

    said = await answer("gracias", was)

    assert said.conversation.retry_plan == LAST_MONTH
