"""One active workflow, and it says what it waits for.

The five nullable fields the client used to hold — plan, unfinished, pending, payment, refusal —
describe thirty-two states, and most of them are nonsense: a payment awaiting confirmation *and* a
half-built payment, a clarification *and* a refusal. Nothing prevented any of them, and every new
conversational defect added a sixth field.

These tests pin the two properties that replace all that:

    at most one workflow is active     the union has no way to hold two
    a waiting state names its slot     so "de este" can be read against YEAR

They are pure and they are the contract the wire and the client both implement. The transitions are
here rather than in the handler because "what happens to the conversation when a payment starts
while a question is open" is a fact about conversations, not about HTTP.
"""

import uuid
from dataclasses import replace

from finguard_orchestrator.domain.enums import (
    Direction,
    Period,
)
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    Conversation,
    FinancialClarification,
    PaymentConfirmation,
    PaymentInProgress,
    Slot,
    UnsupportedCapability,
    slot_for,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind, RatioPlan

LAST_MONTH = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(period=Period.LAST_MONTH),
    categories=("GROCERIES",),
)
RENT = FinancialPlanDraft(direction=Direction.EXPENSE, categories=("HOUSING_RENT",))
MARTA = Contact(uuid.UUID(int=2), "Marta Solis Vega", "+34600333444")


def test_an_empty_conversation_waits_for_nothing() -> None:
    assert IDLE.active is None
    assert IDLE.awaiting is None
    assert IDLE.last_plan is None


def test_a_clarification_says_which_slot_it_asked_for() -> None:
    """The half of the review's point the window vocabulary was standing in for. "Del año" and "el
    año" were added to that vocabulary because nothing recorded that the question had been about a
    year; with the slot on the state, the reply is read against it."""
    open_question = replace(IDLE, active=FinancialClarification(draft=RENT, awaiting=Slot.YEAR))

    assert open_question.awaiting is Slot.YEAR


def test_a_payment_says_which_slot_it_asked_for() -> None:
    building = replace(
        IDLE,
        active=PaymentInProgress(draft=PaymentDraft(amount_minor=1_500), awaiting=Slot.RECIPIENT),
    )

    assert building.awaiting is Slot.RECIPIENT


def test_a_confirmation_is_waiting_for_a_yes() -> None:
    holding = replace(
        IDLE,
        active=PaymentConfirmation(
            intent_id=uuid.UUID(int=9), recipient="Marta Solis Vega", amount_minor=1_500
        ),
    )

    assert holding.awaiting is Slot.CONFIRMATION


# --- one at a time -------------------------------------------------------------------


def test_starting_a_workflow_ends_the_one_before_it() -> None:
    """The invariant the type exists for. A payment beginning while a clarification is open ends the
    clarification, which is what happens in the conversation too — there is no state where both are
    live, so no code can produce one."""
    open_question = replace(IDLE, active=FinancialClarification(draft=RENT))

    paying = replace(open_question, active=PaymentInProgress(draft=PaymentDraft()))

    assert isinstance(paying.active, PaymentInProgress)


# --- suspending ----------------------------------------------------------------------


# --- what a refusal remembers --------------------------------------------------------


def test_a_refusal_waits_for_nothing() -> None:
    """It is a dead end by construction: naming a capability we do not have is the whole state, and
    a slot would promise the next sentence can complete something."""
    refused = replace(IDLE, active=UnsupportedCapability(capability="FORECAST"))

    assert refused.awaiting is None
    assert refused.last_plan is None, "and it is not remembered as an answered question"


# --- which question a payment asks ---------------------------------------------------


def test_the_recipient_is_asked_for_before_the_amount() -> None:
    both = frozenset({PaymentField.RECIPIENT, PaymentField.AMOUNT})

    assert slot_for(both) is Slot.RECIPIENT
    assert slot_for(frozenset({PaymentField.AMOUNT})) is Slot.AMOUNT
    assert slot_for(frozenset({PaymentField.RECIPIENT})) is Slot.RECIPIENT


# --- a transition changes only what it names -----------------------------------------

THIS_MONTH = replace(LAST_MONTH, period=RelativePeriod(period=Period.THIS_MONTH))
SHARE = RatioPlan(part=LAST_MONTH, whole=replace(LAST_MONTH, categories=()), kind=RatioKind.SHARE)


def test_candidates_travel_with_the_payment_that_needs_them() -> None:
    """So the client can offer a choice, and so the choice is part of the state rather than a
    parallel field that could disagree with it."""
    ambiguous = Conversation(
        active=PaymentInProgress(
            draft=PaymentDraft(amount_minor=1_500),
            awaiting=Slot.RECIPIENT_CHOICE,
            candidates=(MARTA,),
        )
    )

    assert isinstance(ambiguous.active, PaymentInProgress)
    assert ambiguous.active.candidates == (MARTA,)
    assert ambiguous.awaiting is Slot.RECIPIENT_CHOICE
