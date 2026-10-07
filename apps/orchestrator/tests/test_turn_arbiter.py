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
