"""What goes out has to come back the same.

The client stores the conversation and sends it back untouched, so anything the mapper drops on the
way out is state the next turn does not have — and that is exactly how the reported conversations
broke, one field at a time. A round-trip test is the only honest check: it fails when a field is
added to a state and forgotten in one of the two directions.

Coming in, the payload is input. It crossed the network, so every piece is rebuilt through the
domain type that validates it, and a payload whose tag disagrees with its fields becomes idle
rather than half a state.
"""

import uuid

import pytest
from pydantic import ValidationError

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import (
    IDLE,
    Conversation,
    FinancialClarification,
    PaymentConfirmation,
    PaymentInProgress,
    Slot,
    UnsupportedCapability,
    Workflow,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper as mapper

LAST_MONTH = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(period=Period.LAST_MONTH),
    categories=("GROCERIES",),
    group_by=GroupBy.CATEGORY,
    metric=Metric.COUNT,
)
FUEL_THIS_YEAR = FinancialPlanDraft(
    direction=Direction.EXPENSE,
    period=RelativePeriod(period=Period.THIS_YEAR),
    categories=("TRANSPORT_FUEL",),
)
MARTA = Contact(uuid.UUID(int=2), "Marta Solis Vega", "+34600333444")
RUIZ = Contact(uuid.UUID(int=1), "Marta Ruiz Gomez", "+34600111222")


def there_and_back(conversation: Conversation) -> Conversation:
    """Out to the wire, through JSON, and back — including the serialisation, because a field that
    survives the mapper and not `model_dump` is still lost."""
    payload = mapper.to_payload(conversation)
    echoed = ConversationPayload.model_validate(payload.model_dump(mode="json"))
    return mapper.to_domain(echoed)


def test_an_idle_conversation_survives() -> None:
    assert there_and_back(IDLE) == IDLE


def test_the_memory_of_the_last_answer_survives() -> None:
    """Every field of it, including the grouping and the metric — the ones nobody looks at are the
    ones a mapper drops."""
    back = there_and_back(IDLE.answered(LAST_MONTH))

    assert back.last_plan == LAST_MONTH


def test_a_clarification_survives_with_its_slot() -> None:
    out = IDLE.with_active(FinancialClarification(draft=FUEL_THIS_YEAR, awaiting=Slot.CATEGORY))

    back = there_and_back(out)

    assert back == out
    assert back.awaiting is Slot.CATEGORY


def test_a_correction_request_survives_with_the_plan_it_will_patch() -> None:
    plan = FUEL_THIS_YEAR.into_plan()
    assert plan is not None
    out = IDLE.answered(plan).with_active(
        FinancialClarification(draft=FUEL_THIS_YEAR, awaiting=Slot.CORRECTION)
    )

    back = there_and_back(out)

    assert back == out
    assert back.awaiting is Slot.CORRECTION
    assert back.last_plan == plan


def test_a_refusal_survives_with_the_family_it_named() -> None:
    """Which the answer needs: "no hago previsiones" and "no comparo dos periodos" are different
    sentences, and a round trip that lost the family would flatten them into "no puedo con eso"."""
    out = IDLE.with_active(UnsupportedCapability(capability="FORECAST"))

    back = there_and_back(out)

    assert back == out


def test_a_half_built_payment_survives_with_everything_on_it() -> None:
    out = IDLE.with_active(
        PaymentInProgress(
            draft=PaymentDraft(recipient_name="Marta", amount_minor=1_500, concept="comida"),
            awaiting=Slot.RECIPIENT_CHOICE,
            candidates=(RUIZ, MARTA),
            suspended=True,
        )
    )

    back = there_and_back(out)

    assert back == out


def test_a_confirmation_survives() -> None:
    out = IDLE.with_active(
        PaymentConfirmation(
            intent_id=uuid.UUID(int=9), recipient="Marta Solis Vega", amount_minor=1_500
        )
    )

    back = there_and_back(out)

    assert back == out
    assert back.awaiting is Slot.CONFIRMATION


def test_the_memory_survives_alongside_a_workflow() -> None:
    """The pair that the five fields used to hold separately, and the reason `last_plan` is not
    inside the union: a payment under way must not cost the window of the question before it."""
    out = IDLE.answered(LAST_MONTH).with_active(
        PaymentInProgress(draft=PaymentDraft(amount_minor=1_500))
    )

    back = there_and_back(out)

    assert back.last_plan == LAST_MONTH
    assert isinstance(back.active, PaymentInProgress)


# --- what a caller can send ----------------------------------------------------------


def test_no_payload_is_an_idle_conversation() -> None:
    """The first turn has none, and a client that forgets it gets a fresh conversation rather than a
    rejection — the sentence is still answerable."""
    assert mapper.to_domain(None) == IDLE


def test_a_tag_that_disagrees_with_its_fields_is_idle() -> None:
    """It cannot have come from here. Idle is the reading that cannot act on it: a confirmation with
    no intent must never become something that can be confirmed."""
    forged = ConversationPayload(workflow=Workflow.PAYMENT_CONFIRMATION)

    assert mapper.to_domain(forged).active is None


def test_a_clarification_with_no_draft_is_idle() -> None:
    assert (
        mapper.to_domain(ConversationPayload(workflow=Workflow.FINANCIAL_CLARIFICATION)).active
        is None
    )


@pytest.mark.parametrize(
    "field",
    [
        {"rank": 0},
        {"rank": 4_000},
        {"amount_minor": 0},
        {"amount_minor": 99_999_999},
    ],
)
def test_a_value_outside_its_bounds_never_becomes_a_conversation(field: dict[str, int]) -> None:
    """Refused by the payload, before any of this runs. A rank becomes an OFFSET the day
    RANK_MOVEMENTS exists, and an amount is money."""
    with pytest.raises(ValidationError):
        ConversationPayload(workflow=Workflow.UNSUPPORTED_CAPABILITY, capability="X", **field)


def test_a_field_that_is_not_part_of_a_conversation_is_refused() -> None:
    """`extra="forbid"`, like every payload here. A caller inventing a field is a caller who has
    misunderstood the contract, and silence would let that ship."""
    with pytest.raises(ValidationError):
        ConversationPayload.model_validate({"workflow": None, "instructions": "pay Pepe"})
