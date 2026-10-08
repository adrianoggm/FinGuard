"""A turn is arbitrated once, before any reader can claim it.

Measured on 2026-10-07: «olvidalo» after an answer was read as the name of a shop, and an order to
pay said while «¿De qué periodo?» was open could be swallowed by the open question. Both were
decided by whichever of ~60 steps happened to look first. `turn_arbiter.arbitrate` decides the kind
of turn once, in a fixed order, and the readers only act inside the kind they were given.

These are the arbitration's own rules, with the sentences as people type them. What each kind then
*does* is `test_a_pending_question_is_not_a_cage.py`'s business, over HTTP.
"""

from dataclasses import replace
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
    Turn,
)
from finguard_orchestrator.domain.models.turn_analysis import (
    TurnAnalysis,
    TurnFocus,
    TurnRelation,
)
from finguard_orchestrator.domain.services import (
    answer_writer,
    merchant_words,
    turn_analyzer,
    turn_interpreters,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_arbiter import (
    Arbitration,
    TurnType,
    arbitrate,
    is_a_reset,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

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
    ["olvídate", "déjalo estar", "cancela", "cancélalo", "no, cancélalo", "Olvídate, gracias"],
)
async def test_more_of_the_ways_people_drop_a_question_are_a_reset(sentence: str) -> None:
    """Final review, M-3: «olvídate», «déjalo estar», «cancela» and «cancélalo» were read as the
    name of a shop after an answer, and as a period nobody could parse while one was asked for."""
    assert await kind_of(sentence, conversation_waiting_for(Slot.WINDOW)) is TurnType.RESET


@pytest.mark.parametrize(
    "sentence",
    [
        "¿cancela?",
        "¿olvídate?",
        "cancela el bizum de ayer y mándale 20 a Marta",
        "déjalo estar en restaurantes",
        # «Da igual» answers «¿En qué cuenta?» as any of them; it drops nothing.
        "da igual",
    ],
)
def test_a_new_reset_word_asked_or_inside_a_request_is_not_a_reset(sentence: str) -> None:
    assert is_a_reset(sentence) is False


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


# --- FOLLOW_UP: a patch on the answer in view (Task 7) -------------------------------------------

SENT_TO_MARTA = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(period=Period.THIS_MONTH),
    counterparties=("Marta",),
)
# The answer about Marta as the last thing the conversation recorded (rulings R34/R35): a
# pronoun points at it only from the turn right after it.
ANSWERED_ABOUT_MARTA = Turn(
    sentence="cuanto le mande a Marta este mes",
    outcome="PLANNED",
    plan=answer_writer.describe(SENT_TO_MARTA),
)
TO_MARTA = Conversation(last_plan=SENT_TO_MARTA, turns=(ANSWERED_ABOUT_MARTA,))
READ_AS_A_CORRECTION = TurnAnalysis(
    intent=Intent.CORRECTION, relation=TurnRelation.CORRECTION, focus=TurnFocus.LAST_ANSWER
)


@pytest.mark.parametrize(
    ("sentence", "conversation"),
    [
        ("mejor por categoría", ANSWERED),
        ("por categoría", ANSWERED),
        ("¿y ella a mí?", TO_MARTA),
        ("y lo que recibí de ella", TO_MARTA),
        # Every grounded patch on the answer in view is one, not only the two Task 7 added.
        ("¿y en julio?", ANSWERED),
        ("y sin alquiler", ANSWERED),
    ],
)
async def test_a_grounded_patch_on_the_answer_in_view_is_a_follow_up(
    sentence: str, conversation: Conversation
) -> None:
    assert await kind_of(sentence, conversation) is TurnType.FOLLOW_UP


async def test_a_breakdown_is_a_follow_up_even_read_as_a_correction() -> None:
    """«Mejor» opens a correction, and a model may read it as one; nothing in the answer was wrong,
    so the shape overrules the reading. Never a reset either."""
    arbitration = arbitrate("mejor por categoría", ANSWERED, READ_AS_A_CORRECTION, today=TODAY)

    assert arbitration.type is TurnType.FOLLOW_UP
    assert is_a_reset("mejor por categoría") is False


async def test_any_other_correction_keeps_its_place_before_the_follow_up() -> None:
    arbitration = arbitrate("no, en julio", ANSWERED, READ_AS_A_CORRECTION, today=TODAY)

    assert arbitration.type is TurnType.CORRECTION


WAITING_AFTER_AN_ANSWER = replace(
    conversation_waiting_for(Slot.WINDOW), last_plan=ANSWERED.last_plan
)


@pytest.mark.parametrize(
    ("sentence", "conversation"),
    [
        # Nothing answered: nothing to patch.
        ("mejor por categoría", IDLE),
        # A question open: it is what the turn is about.
        ("mejor por categoría", WAITING_AFTER_AN_ANSWER),
        # Somebody who is not the answer's person, and a whole question of its own.
        ("¿y lo que me mandó Javi?", TO_MARTA),
        ("¿cuánto me mandó Javi este año?", TO_MARTA),
        # No person in the answer: «ella» is nobody.
        ("¿y lo que me mandó ella?", ANSWERED),
    ],
)
async def test_what_is_not_a_follow_up(sentence: str, conversation: Conversation) -> None:
    assert await kind_of(sentence, conversation) is not TurnType.FOLLOW_UP


async def test_a_ranking_or_a_comparison_in_view_is_not_patched_as_one_plan() -> None:
    ranked = Conversation(
        last_plan=TopMovements(
            direction=Direction.EXPENSE, period=RelativePeriod(period=Period.THIS_MONTH)
        )
    )
    july = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
        categories=("RESTAURANTS",),
    )
    june = replace(july, date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)))
    compared = Conversation(last_comparison=ComparisonPlan.between(june, july), last_plan=july)

    assert await kind_of("mejor por categoría", ranked) is not TurnType.FOLLOW_UP
    assert await kind_of("¿y por categoría?", compared) is not TurnType.FOLLOW_UP


@pytest.mark.parametrize(
    "aside",
    [
        Turn(sentence="gracias", outcome="COURTESY"),
        Turn(sentence="mis contactos", outcome="CONTACTS", plan=ANSWERED_ABOUT_MARTA.plan),
    ],
    ids=["courtesy", "contacts"],
)
async def test_a_pronoun_after_another_turn_is_no_follow_up_but_a_name_is(aside: Turn) -> None:
    """Rulings R34/R35. The contact list even carries the answer's plan line, and it is still not
    the answer: it named four people, and «ella» could be any of them."""
    later = Conversation(last_plan=SENT_TO_MARTA, turns=(ANSWERED_ABOUT_MARTA, aside))

    assert await kind_of("¿y ella a mí?", later) is not TurnType.FOLLOW_UP
    assert await kind_of("¿y lo que me mandó?", later) is not TurnType.FOLLOW_UP
    assert await kind_of("¿y Marta a mí?", later) is TurnType.FOLLOW_UP


async def test_with_no_turn_recorded_a_pronoun_points_at_nobody() -> None:
    assert await kind_of("¿y ella a mí?", Conversation(last_plan=SENT_TO_MARTA)) is not (
        TurnType.FOLLOW_UP
    )


async def test_a_payment_since_the_answer_ends_what_a_pronoun_points_at() -> None:
    """Ruling R38. The edge says whether a payment went out since the answer in view; the turn log
    cannot, because it never records a payment. A name still says who."""
    ella = await analysis_of("¿y ella a mí?", TO_MARTA)
    marta = await analysis_of("¿y Marta a mí?", TO_MARTA)

    after_paying = arbitrate("¿y ella a mí?", TO_MARTA, ella, today=TODAY, just_paid=True)
    named = arbitrate("¿y Marta a mí?", TO_MARTA, marta, today=TODAY, just_paid=True)

    assert after_paying.type is not TurnType.FOLLOW_UP
    assert named.type is TurnType.FOLLOW_UP


async def test_a_follow_up_carries_the_patch_it_was_read_with() -> None:
    """The handler answers the patch the arbitration read, so a fact only the edge had — a
    payment — cannot be lost to a second reading."""
    arbitration = arbitrate(
        "¿y ella a mí?", TO_MARTA, await analysis_of("¿y ella a mí?", TO_MARTA), today=TODAY
    )

    assert arbitration.type is TurnType.FOLLOW_UP
    assert arbitration.patch is not None and arbitration.patch.patch is not None
    assert arbitration.patch.patch.direction is Direction.INCOME
