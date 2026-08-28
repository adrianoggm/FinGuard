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
from collections.abc import Callable
from dataclasses import fields, replace

import pytest

from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Period,
    Rail,
)
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    AccountAlias,
    Conversation,
    CounterpartyAlias,
    FinancialClarification,
    PaymentConfirmation,
    PaymentInProgress,
    Slot,
    UnsupportedCapability,
    slot_for,
)
from finguard_orchestrator.domain.models.result_reference import (
    ResultReference,
    focused_on_result,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioKind, RatioPlan

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
    open_question = IDLE.with_active(FinancialClarification(draft=RENT, awaiting=Slot.YEAR))

    assert open_question.awaiting is Slot.YEAR


def test_a_payment_says_which_slot_it_asked_for() -> None:
    building = IDLE.with_active(
        PaymentInProgress(draft=PaymentDraft(amount_minor=1_500), awaiting=Slot.RECIPIENT)
    )

    assert building.awaiting is Slot.RECIPIENT


def test_a_confirmation_is_waiting_for_a_yes() -> None:
    holding = IDLE.with_active(
        PaymentConfirmation(
            intent_id=uuid.UUID(int=9), recipient="Marta Solis Vega", amount_minor=1_500
        )
    )

    assert holding.awaiting is Slot.CONFIRMATION


# --- one at a time -------------------------------------------------------------------


def test_starting_a_workflow_ends_the_one_before_it() -> None:
    """The invariant the type exists for. A payment beginning while a clarification is open ends the
    clarification, which is what happens in the conversation too — there is no state where both are
    live, so no code can produce one."""
    open_question = IDLE.with_active(FinancialClarification(draft=RENT))

    paying = open_question.with_active(PaymentInProgress(draft=PaymentDraft()))

    assert isinstance(paying.active, PaymentInProgress)


def test_an_answer_closes_whatever_was_open_and_becomes_the_memory() -> None:
    open_question = IDLE.with_active(FinancialClarification(draft=RENT))

    after = open_question.answered(LAST_MONTH)

    assert after.active is None
    assert after.last_plan == LAST_MONTH


def test_the_memory_survives_a_workflow() -> None:
    """A payment under way must not cost the window of the question before it: "¿y en gasolina?"
    still means last month while a bizum is being built."""
    answered = IDLE.answered(LAST_MONTH)

    building = answered.with_active(PaymentInProgress(draft=PaymentDraft()))

    assert building.last_plan == LAST_MONTH


# --- suspending ----------------------------------------------------------------------


def test_a_payment_suspends_rather_than_vanishing() -> None:
    """The review's point 7. A half-built payment used to survive any number of unrelated turns in
    silence, so "hagamos un bizum / ¿cuánto gasté? / 15 euros" resumed a payment the user had
    stopped thinking about. Suspended keeps it and makes it visible."""
    building = IDLE.with_active(
        PaymentInProgress(draft=PaymentDraft(recipient_name="Marta"), awaiting=Slot.AMOUNT)
    )

    aside = building.suspend()

    assert isinstance(aside.active, PaymentInProgress)
    assert aside.active.suspended is True
    assert aside.active.draft.recipient_name == "Marta", "the work so far is not lost"
    assert aside.active.awaiting is Slot.AMOUNT, "and it still knows what it was asking"


def test_answering_the_aside_remembers_it_without_erasing_the_suspended_payment() -> None:
    building = IDLE.with_active(
        PaymentInProgress(draft=PaymentDraft(recipient_name="Marta"), awaiting=Slot.AMOUNT)
    )

    answered = building.suspend().answered(LAST_MONTH)

    assert isinstance(answered.active, PaymentInProgress)
    assert answered.active.suspended is True
    assert answered.active.draft.recipient_name == "Marta"
    assert answered.last_plan == LAST_MONTH


def test_a_clarification_does_not_suspend() -> None:
    """Asymmetric on purpose. A question about a query is answered on the next turn or it is not;
    keeping one alive across a change of subject is how "este año" completed a plan the user had
    moved on from."""
    open_question = IDLE.answered(LAST_MONTH).with_active(FinancialClarification(draft=RENT))

    aside = open_question.suspend()

    assert aside.active is None
    assert aside.last_plan == LAST_MONTH, "the memory is not what was suspended"


def test_suspending_an_idle_conversation_changes_nothing_that_matters() -> None:
    assert IDLE.suspend().active is None


# --- what a refusal remembers --------------------------------------------------------


def test_a_refusal_waits_for_nothing() -> None:
    """It is a dead end by construction: naming a capability we do not have is the whole state, and
    a slot would promise the next sentence can complete something."""
    refused = IDLE.with_active(UnsupportedCapability(capability="FORECAST"))

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
PENDING_SHARE = RatioDraft(
    part=RENT, whole=FinancialPlanDraft(direction=Direction.EXPENSE), kind=RatioKind.SHARE
)
BOTH_MONTHS = ComparisonPlan(earlier=LAST_MONTH, latest=THIS_MONTH)

# Every field set to something distinguishable, so a transition that drops one is visible.
FULL = Conversation(
    active=PaymentInProgress(draft=PaymentDraft(recipient_name="Marta")),
    last_plan=LAST_MONTH,
    prior_plan=THIS_MONTH,
    retry_plan=LAST_MONTH,
    result_reference=ResultReference(
        LAST_MONTH,
        category="GROCERIES",
        group_by=GroupBy.CATEGORY,
        group_key="GROCERIES",
        transaction_ids=(uuid.UUID(int=7),),
    ),
    last_asked=Slot.WINDOW,
    answered_the_balance=True,
    last_ratio=SHARE,
    pending_ratio=PENDING_SHARE,
    last_comparison=BOTH_MONTHS,
    account_aliases=(
        AccountAlias(alias="la hucha", account_id=uuid.UUID(int=3), display_name="Ahorro"),
    ),
    counterparty_aliases=(
        CounterpartyAlias(
            alias="el casero",
            counterparty_name="Inmobiliaria Sur",
            counterparty_type=CounterpartyType.BILLER,
        ),
    ),
    recent_payment_rail=Rail.BIZUM,
)

# What answering a financial question resets, whichever of the three kinds it was. Listed rather
# than derived by subtraction: a field added later must fail this test until somebody decides
# whether an answer resets it, which is the whole point.
_ANSWERING = {
    "active",
    "last_plan",
    "prior_plan",
    "retry_plan",
    "result_reference",
    "last_asked",
    "answered_the_balance",
    "last_ratio",
    "pending_ratio",
    "last_comparison",
}

# What each transition is allowed to change. Everything else has to survive it.
#
# This is the property the transitions used to lack. Each one enumerated by hand which of the
# sixteen fields it rebuilt, so forgetting one deleted memory silently — and the omission was
# invisible, because it was an absence in a constructor call rather than a line of code. The two
# instances that cost real turns: `with_active` and `asking` dropped `answered_the_balance`, whose
# own comment in `suspend` documents what that breaks — "¿y en la hucha?" after a balance turns
# back into a question about spending.
#
# Sixteen fields times thirteen transitions is two hundred decisions nobody can review. One test
# reviews them all, and a seventeenth field is covered the moment it exists.
_TRANSITIONS: tuple[tuple[str, Callable[[Conversation], Conversation], set[str]], ...] = (
    (
        "with_active",
        lambda c: c.with_active(FinancialClarification(draft=RENT)),
        {"active", "last_asked"},
    ),
    ("with_active(None)", lambda c: c.with_active(None), {"active", "last_asked"}),
    ("paid", lambda c: c.paid(Rail.TRANSFER), {"active", "last_asked", "recent_payment_rail"}),
    ("without_recent_payment", lambda c: c.without_recent_payment(), {"recent_payment_rail"}),
    ("asking", lambda c: c.asking(Slot.AMOUNT), {"last_asked"}),
    ("refused", lambda c: c.refused("FORECAST"), {"active", "last_asked"}),
    ("answering_the_balance", lambda c: c.answering_the_balance(), {"answered_the_balance"}),
    ("awaiting_retry", lambda c: c.awaiting_retry(THIS_MONTH), {"retry_plan"}),
    ("without_retry", lambda c: c.without_retry(), {"retry_plan"}),
    (
        "focused_on_result",
        lambda c: focused_on_result(
            c, ResultReference(LAST_MONTH, group_by=GroupBy.MERCHANT, group_key="Mercadona")
        ),
        {"result_reference"},
    ),
    (
        "waiting_for_ratio",
        lambda c: c.waiting_for_ratio(PENDING_SHARE),
        {"last_asked", "pending_ratio"},
    ),
    ("suspend", lambda c: c.suspend(), {"active"}),
    # The three that answer something share one reset, so they share one allowance: a new answer
    # owns `last_plan`, the referents that came with it, and which of the three answer-shaped
    # memories describes it. The aliases and the payment referent are not part of an answer.
    ("answered", lambda c: c.answered(THIS_MONTH), _ANSWERING),
    ("answered_comparison", lambda c: c.answered_comparison(BOTH_MONTHS), _ANSWERING),
    ("answered_ratio", lambda c: c.answered_ratio(SHARE), _ANSWERING),
)


@pytest.mark.parametrize(
    ("name", "transition", "may_change"), _TRANSITIONS, ids=[case[0] for case in _TRANSITIONS]
)
def test_a_transition_changes_only_what_it_names(
    name: str, transition: Callable[[Conversation], Conversation], may_change: set[str]
) -> None:
    after = transition(FULL)

    for field in fields(Conversation):
        if field.name in may_change:
            continue
        assert getattr(after, field.name) == getattr(FULL, field.name), (
            f"{name} silently dropped {field.name}"
        )


def test_a_clarification_does_not_cost_the_balance_thread() -> None:
    """The live instance of the above, as its own turn — because it is a conversation and not a
    property. A balance, then a question asked back about something else, then "¿y en la hucha?":
    the third turn is about the balance, and it stopped being so because opening the clarification
    dropped the flag that said so."""
    after_a_balance = IDLE.answering_the_balance()

    asked_something_else = after_a_balance.with_active(FinancialClarification(draft=RENT))

    assert asked_something_else.answered_the_balance is True


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
