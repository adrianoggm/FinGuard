"""A turn is arbitrated once, before any reader can claim it.

Measured on 2026-10-07: «olvidalo» after an answer was read as the name of a shop, and an order to
pay said while «¿De qué periodo?» was open could be swallowed by the open question. Both were
decided by whichever of ~60 steps happened to look first. `turn_arbiter.arbitrate` decides the kind
of turn once, in a fixed order, and the readers only act inside the kind they were given.

These are the arbitration's own rules, with the sentences as people type them. What each kind then
*does* is `test_a_pending_question_is_not_a_cage.py`'s business, over HTTP.
"""

from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    AccountCandidate,
    AccountScopeClarification,
    Conversation,
    FinancialClarification,
    PaymentConfirmation,
    Slot,
)
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis
from finguard_orchestrator.domain.services import (
    merchant_words,
    turn_analyzer,
    turn_interpreters,
)
from finguard_orchestrator.domain.services.turn_arbiter import (
    Arbitration,
    TurnType,
    arbitrate,
    is_a_reset,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)

TODAY = date(2026, 8, 5)
ANSWERED = Conversation(
    last_plan=FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_MONTH),
        categories=("GROCERIES",),
    )
)


async def analysis_of(sentence: str, conversation: Conversation = IDLE) -> TurnAnalysis:
    """The reading the edge hands the arbitration: the deterministic interpreter, which is the
    corpus's and the default deployment's."""
    return await turn_analyzer.analyze(
        sentence,
        conversation=conversation,
        interpreter=turn_interpreters.Deterministic(),
        today=TODAY,
    )


def conversation_waiting_for(slot: Slot) -> Conversation:
    """«¿cuánto gasté en restaurantes?» answered with a question about `slot`: the draft the planner
    built, held open by the clarification."""
    return Conversation(
        active=FinancialClarification(
            draft=FinancialPlanDraft(direction=Direction.EXPENSE, categories=("RESTAURANTS",)),
            awaiting=slot,
        )
    )


async def kind_of(sentence: str, conversation: Conversation = IDLE) -> TurnType:
    arbitration = arbitrate(
        sentence, conversation, await analysis_of(sentence, conversation), today=TODAY
    )
    return arbitration.type


@pytest.mark.parametrize(
    "sentence",
    ["olvídalo", "olvidalo", "déjalo", "no importa", "nada", "no era eso", "Olvídalo, gracias"],
)
async def test_only_the_reset_words_are_a_reset(sentence: str) -> None:
    assert await kind_of(sentence) is TurnType.RESET


@pytest.mark.parametrize(
    "sentence",
    ["no, déjalo en restaurantes", "nada más que gasolina", "no era eso, quería decir julio"],
)
async def test_a_reset_word_inside_a_request_is_not_a_reset(sentence: str) -> None:
    assert await kind_of(sentence) is not TurnType.RESET


async def test_an_order_to_pay_while_a_period_is_pending_is_a_new_operation() -> None:
    waiting = conversation_waiting_for(Slot.WINDOW)
    said = "ahora quiero mandarle 20 euros a Marta"
    assert await kind_of(said, waiting) is TurnType.NEW_OPERATION


@pytest.mark.parametrize(
    "sentence",
    ["vale, déjalo", "no, olvídalo", "nada, déjalo", "déjalo, por favor", "¡Olvídalo!"],
)
async def test_the_reset_words_may_come_with_courtesy_and_a_no(sentence: str) -> None:
    """Folded, unpunctuated, and the words that carry no request dropped: «gracias», «vale», «por
    favor», and a leading «no» — «no, olvídalo» is how the reset is usually said."""
    assert await kind_of(sentence, conversation_waiting_for(Slot.WINDOW)) is TurnType.RESET


@pytest.mark.parametrize("sentence", ["gracias", "no", "vale", "no, gracias", "por favor"])
def test_courtesy_alone_is_not_a_reset(sentence: str) -> None:
    """Nothing in these asks to drop anything. «Gracias» is the pleasantry it always was, and a
    bare «no» while a question is open is somebody answering it."""
    assert is_a_reset(sentence) is False


@pytest.mark.parametrize(
    "sentence", ["¿nada?", "¿olvídalo?", "nada?", "¿no importa?", "¿no era eso?", "¿déjalo"]
)
def test_a_question_is_never_a_reset(sentence: str) -> None:
    """Ruling R16. «¿Nada?» asks something — whether there was nothing, whether to leave it — and
    wiping the conversation is not an answer to a question. A reset is said, never asked."""
    assert is_a_reset(sentence) is False


async def test_a_reset_wins_over_the_question_left_open() -> None:
    """First in the order, because a reset is the one turn that is about the open question itself:
    read as its answer, «olvídalo» is asked about again («Sigo sin tener claro el periodo»)."""
    assert await kind_of("olvídalo", conversation_waiting_for(Slot.WINDOW)) is TurnType.RESET


async def test_a_preview_waiting_for_a_yes_owns_the_turn() -> None:
    """The controller's confirmation gates answer it before the arbitration is even asked; this is
    the arbitration agreeing, so no later rule can read «sí» as something else."""
    previewed = Conversation(
        active=PaymentConfirmation(
            intent_id=UUID(int=4), recipient="Marta Solis Vega", amount_minor=2_000
        )
    )
    assert await kind_of("sí", previewed) is TurnType.CONFIRMATION
    assert await kind_of("mándale 20 € a Javi Nuñez", previewed) is TurnType.CONFIRMATION


async def test_a_balance_question_is_a_new_operation_whatever_is_open() -> None:
    assert await kind_of("¿cuánto tengo?", conversation_waiting_for(Slot.WINDOW)) is (
        TurnType.NEW_OPERATION
    )


async def test_asking_who_can_be_paid_is_not_an_order() -> None:
    """«Bizum» is a payment word, and this sentence orders nothing: the interpreter's LIST_CONTACTS
    is the reading the payment path was taught to respect."""
    assert await kind_of("¿a quién puedo hacerle un bizum?") is TurnType.NEW_QUESTION


async def test_a_correction_with_a_replacement_is_a_correction() -> None:
    """«No era eso» followed by what was meant is a correction, not a reset: the spec's own
    example, after an answer it can correct."""
    assert await kind_of("no era eso, quería decir julio", ANSWERED) is TurnType.CORRECTION


async def test_anything_else_is_a_new_question() -> None:
    assert await kind_of("¿cuánto gasté en gasolina el mes pasado?", ANSWERED) is (
        TurnType.NEW_QUESTION
    )


@pytest.mark.parametrize("sentence", ["olvidalo", "olvídalo", "no importa"])
def test_the_reset_words_are_never_a_shop_either(sentence: str) -> None:
    """The arbitration answers a whole-turn reset first; the stop-list makes sure the bare-shop
    guess is never the reader that sees one — «olvidalo» ran as `merchants: ['olvidalo']`."""
    assert merchant_words.never_a_merchant(sentence) is True


def test_an_arbitration_fills_no_slot_unless_it_says_so() -> None:
    """`fills` belongs to a reply that answers the open question; the kinds decided here fill
    nothing."""
    assert Arbitration(TurnType.RESET).fills == frozenset()


# --- ANSWER_PENDING: a reply to the question left open, and every slot it fills (Task 5) ---------

WAITING_FOR_AN_ACCOUNT = Conversation(
    active=AccountScopeClarification(
        pending=FinancialPlan(
            direction=Direction.EXPENSE, period=RelativePeriod(period=Period.LAST_MONTH)
        ),
        candidates=(
            AccountCandidate(UUID(int=1), "Cuenta corriente", "CURRENT"),
            # A name that is no kind of account, so only the question's own offer can read it.
            AccountCandidate(UUID(int=2), "Viajes", "SAVINGS"),
        ),
    )
)


async def arbitration_of(sentence: str, conversation: Conversation) -> Arbitration:
    return arbitrate(sentence, conversation, await analysis_of(sentence, conversation), today=TODAY)


@pytest.mark.parametrize(
    ("sentence", "fills"),
    [
        ("este año", {Slot.WINDOW}),
        ("el mes pasado", {Slot.WINDOW}),
        ("en todas mis cuentas", {Slot.ACCOUNT}),
        ("en la hucha", {Slot.ACCOUNT}),
        ("en todas mis cuentas este año", {Slot.ACCOUNT, Slot.WINDOW}),
        ("este año pero en gasolina", {Slot.WINDOW, Slot.CATEGORY}),
    ],
)
async def test_a_reply_to_the_period_question_fills_every_slot_it_names(
    sentence: str, fills: set[Slot]
) -> None:
    """The account is a slot of the window question's draft (spec §3.2), and a new subject said
    beside the window is the reply's too: both are absorbed, not only the slot that was asked."""
    assert await arbitration_of(sentence, conversation_waiting_for(Slot.WINDOW)) == Arbitration(
        TurnType.ANSWER_PENDING, frozenset(fills)
    )


@pytest.mark.parametrize(
    "sentence",
    [
        # A subject alone fills nothing the question was waiting on: a change of subject.
        "en gasolina",
        # A complete question of its own names a window too, and is not this one's period.
        "¿cuánto gasté en gasolina el mes pasado?",
        # An order to move money names an account as where it goes, not as the one to read.
        "pasa 100 a la hucha",
        # «Todas» means every account only as the answer to «¿En qué cuenta…?».
        "todas",
        "¿quién eres?",
    ],
)
async def test_what_is_not_a_reply_to_the_period_question(sentence: str) -> None:
    assert await kind_of(sentence, conversation_waiting_for(Slot.WINDOW)) is not (
        TurnType.ANSWER_PENDING
    )


async def test_the_year_question_is_filled_by_the_year_it_asked_for() -> None:
    """«De este» names nothing on its own and is this year as the answer to «¿de qué año?»."""
    assert await arbitration_of("de este", conversation_waiting_for(Slot.YEAR)) == Arbitration(
        TurnType.ANSWER_PENDING, frozenset({Slot.YEAR})
    )


@pytest.mark.parametrize(
    ("sentence", "fills"),
    [
        ("este año", {Slot.WINDOW}),
        ("todas", {Slot.ACCOUNT}),
        ("Viajes", {Slot.ACCOUNT}),
        ("la hucha este año", {Slot.ACCOUNT, Slot.WINDOW}),
    ],
)
async def test_a_reply_to_the_account_question_fills_the_account_or_corrects_the_window(
    sentence: str, fills: set[Slot]
) -> None:
    """«Este año» to «¿En qué cuenta…?» is the plan's window chosen again, kept while the account
    is asked again; a bare «todas» and an offered account's own name are choices only here."""
    assert await arbitration_of(sentence, WAITING_FOR_AN_ACCOUNT) == Arbitration(
        TurnType.ANSWER_PENDING, frozenset(fills)
    )


async def test_the_account_question_still_survives_an_aside_and_a_balance() -> None:
    """Ruling R17, as the arbitration sees it: neither is a reply, so neither is absorbed."""
    assert await kind_of("¿quién eres?", WAITING_FOR_AN_ACCOUNT) is TurnType.NEW_QUESTION
    assert await kind_of("¿cuánto tengo?", WAITING_FOR_AN_ACCOUNT) is TurnType.NEW_OPERATION


async def test_a_window_with_nothing_open_is_not_an_answer() -> None:
    assert await kind_of("este año", ANSWERED) is TurnType.NEW_QUESTION


async def test_the_correction_question_is_left_to_the_correction_readers() -> None:
    """«¿Qué debería cambiar…?» holds no slot here: what a reply to it changes is the last answer,
    which the correction path patches."""
    assert await kind_of("este año", conversation_waiting_for(Slot.CORRECTION)) is not (
        TurnType.ANSWER_PENDING
    )


# Ruling R21: the five new questions the review found taken for the period question's account.
A_NEW_QUESTION_NAMING_AN_ACCOUNT = (
    "¿cuánto ingresé en la hucha?",
    "¿cuánto he gastado en la hucha?",
    "¿qué movimientos tengo en la hucha?",
    "muéstrame los movimientos de la cuenta corriente",
    "dame los gastos de la hucha por categoría",
)


@pytest.mark.parametrize("sentence", A_NEW_QUESTION_NAMING_AN_ACCOUNT)
async def test_a_new_question_naming_an_account_is_not_the_period_questions_account(
    sentence: str,
) -> None:
    """Ruling R21. A reply that does not fill the slot asked is one only while it is elliptical:
    each of these states a subject of its own — income, spending, a listing, a breakdown — and the
    account in it belongs to that question, not to «¿cuánto gasté en restaurantes?»."""
    assert await kind_of(sentence, conversation_waiting_for(Slot.WINDOW)) is not (
        TurnType.ANSWER_PENDING
    )


@pytest.mark.parametrize(
    "sentence", ["en todas mis cuentas", "en la hucha", "¿y en la hucha?", "la cuenta corriente"]
)
async def test_an_account_said_on_its_own_is_still_the_period_questions(sentence: str) -> None:
    assert await arbitration_of(sentence, conversation_waiting_for(Slot.WINDOW)) == Arbitration(
        TurnType.ANSWER_PENDING, frozenset({Slot.ACCOUNT})
    )


@pytest.mark.parametrize(
    ("sentence", "fills"),
    [
        # An excluded category is the reply's subject as much as an included one (ruling R22).
        ("este año sin gasolina", {Slot.WINDOW, Slot.CATEGORY}),
        ("en todas mis cuentas pero en gasolina", {Slot.ACCOUNT, Slot.CATEGORY}),
        # «Nómina» beside «cuenta» is the account, as `_scoped` reads it — not the payroll.
        ("la cuenta nómina", {Slot.ACCOUNT}),
        # And without «cuenta» it is the payroll, as `_scoped` reads it — not the current account.
        ("este año, la nómina", {Slot.WINDOW, Slot.CATEGORY}),
    ],
)
async def test_what_a_reply_says_about_the_subject(sentence: str, fills: set[Slot]) -> None:
    assert await arbitration_of(sentence, conversation_waiting_for(Slot.WINDOW)) == Arbitration(
        TurnType.ANSWER_PENDING, frozenset(fills)
    )
