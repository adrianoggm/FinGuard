"""What goes out has to come back the same.

The client stores the conversation and sends it back untouched, so anything the mapper drops on the
way out is state the next turn does not have — and that is exactly how the reported conversations
broke, one field at a time. A round-trip test is the only honest check: it fails when a field is
added to a state and forgotten in one of the two directions.

Coming in, the payload is input. It crossed the network, so every piece is rebuilt through the
domain type that validates it, and a payload whose tag disagrees with its fields is refused rather
than reinterpreted into half a state.
"""

import uuid

import pytest
from pydantic import ValidationError

from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    Metric,
    Period,
    Rail,
    TransactionType,
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
    Workflow,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioKind, RatioPlan
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


def test_a_direct_debit_scope_survives_the_client_round_trip() -> None:
    receipts = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        metric=Metric.RECURRENCE,
        transaction_types=(TransactionType.DIRECT_DEBIT,),
    )

    assert there_and_back(IDLE.answered(receipts)).last_plan == receipts


def test_a_group_ranking_limit_survives_the_client_round_trip() -> None:
    ranking = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        group_by=GroupBy.MERCHANT,
        metric=Metric.AVERAGE,
        group_limit=3,
    )

    assert there_and_back(IDLE.answered(ranking)).last_plan == ranking


def test_a_highlighted_category_survives_the_client_round_trip() -> None:
    out = IDLE.answered(LAST_MONTH, referenced_category="GROCERIES")

    back = there_and_back(out)

    assert back.referenced_category == "GROCERIES"


def test_a_typed_group_and_its_evidence_survive_the_client_round_trip() -> None:
    evidence = (uuid.UUID(int=21), uuid.UUID(int=22))
    out = IDLE.answered(
        LAST_MONTH,
        referenced_group_by=GroupBy.MERCHANT,
        referenced_group_key="Mercadona",
        referenced_transaction_ids=evidence,
    )

    back = there_and_back(out)

    assert back.referenced_group_by is GroupBy.MERCHANT
    assert back.referenced_group_key == "Mercadona"
    assert back.referenced_transaction_ids == evidence


def test_a_partial_group_reference_is_not_valid_conversation_state() -> None:
    with pytest.raises(ValidationError):
        ConversationPayload.model_validate({"referenced_group_by": "MERCHANT"})


def test_duplicate_referenced_movements_are_not_valid_conversation_state() -> None:
    movement_id = str(uuid.UUID(int=21))
    with pytest.raises(ValidationError):
        ConversationPayload.model_validate(
            {"referenced_transaction_ids": [movement_id, movement_id]}
        )


def test_two_plans_and_an_exact_movement_selection_survive_together() -> None:
    earlier = LAST_MONTH
    latest = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_MONTH),
        categories=("GROCERIES",),
        transaction_ids=(uuid.UUID(int=7), uuid.UUID(int=8)),
    )

    back = there_and_back(Conversation(last_plan=latest, prior_plan=earlier))

    assert back.last_plan == latest
    assert back.prior_plan == earlier


def test_an_answered_comparison_survives_as_one_validated_pair() -> None:
    earlier = LAST_MONTH
    latest = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_MONTH),
        categories=("GROCERIES",),
        group_by=GroupBy.CATEGORY,
        metric=Metric.COUNT,
    )
    comparison = ComparisonPlan.between(earlier, latest)
    assert comparison is not None

    back = there_and_back(IDLE.answered_comparison(comparison))

    assert back.last_comparison == comparison
    assert back.prior_plan == comparison.earlier
    assert back.last_plan == comparison.latest


@pytest.mark.parametrize("missing", ["earlier", "latest"])
def test_a_partial_comparison_is_not_valid_conversation_state(missing: str) -> None:
    fields: dict[str, object] = {
        "last_comparison_earlier": mapper.plan_payload(LAST_MONTH),
        "last_comparison_latest": mapper.plan_payload(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.THIS_MONTH),
                categories=("GROCERIES",),
                group_by=GroupBy.CATEGORY,
                metric=Metric.COUNT,
            )
        ),
    }
    fields[f"last_comparison_{missing}"] = None

    with pytest.raises(ValidationError, match="both ordered operands"):
        ConversationPayload(**fields)


def test_an_answered_percentage_survives_with_both_operands() -> None:
    whole = FinancialPlan(
        direction=Direction.INCOME,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("SALARY",),
    )
    part = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        categories=("HOUSING_RENT",),
    )
    ratio = RatioPlan(part=part, whole=whole, kind=RatioKind.SHARE)

    back = there_and_back(IDLE.answered_ratio(ratio))

    assert back.last_ratio == ratio
    assert back.last_plan == part
    assert back.prior_plan == whole


def test_a_percentage_waiting_only_for_its_window_survives() -> None:
    ratio = RatioDraft(
        part=FinancialPlanDraft(direction=Direction.EXPENSE, categories=("GROCERIES",)),
        whole=FinancialPlanDraft(direction=Direction.EXPENSE),
        kind=RatioKind.SHARE,
    )

    back = there_and_back(IDLE.waiting_for_ratio(ratio))

    assert back.pending_ratio == ratio
    assert back.last_asked is Slot.WINDOW


@pytest.mark.parametrize("missing", ["kind", "part", "whole"])
def test_a_partial_percentage_pair_is_not_valid_conversation_state(missing: str) -> None:
    plan = mapper.plan_payload(LAST_MONTH)
    fields: dict[str, object] = {
        "last_ratio_kind": RatioKind.SHARE,
        "last_ratio_part": plan,
        "last_ratio_whole": plan,
    }
    fields[f"last_ratio_{missing}"] = None

    with pytest.raises(ValidationError):
        ConversationPayload(**fields)


def test_negative_filters_survive_the_client_round_trip() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.THIS_YEAR),
        excluded_categories=("HOUSING_RENT",),
        excluded_merchants=("Mercadona",),
    )

    back = there_and_back(IDLE.answered(plan))

    assert back.last_plan == plan


def test_an_internal_only_query_survives_the_client_round_trip() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_YEAR),
        include_internal=True,
        internal_only=True,
        across_accounts=True,
    )

    back = there_and_back(IDLE.answered(plan))

    assert back.last_plan == plan


def test_an_explicit_account_scope_survives_the_client_round_trip() -> None:
    scoped = FinancialPlan(
        direction=Direction.ALL,
        period=RelativePeriod(period=Period.LAST_N_MONTHS, n=4),
        only_account_id=uuid.UUID(int=7),
    )

    back = there_and_back(IDLE.answered(scoped))

    assert back.last_plan == scoped


def test_a_query_waiting_for_a_bank_retry_survives_without_becoming_an_answer() -> None:
    out = Conversation(last_plan=None, retry_plan=LAST_MONTH)

    back = there_and_back(out)

    assert back.retry_plan == LAST_MONTH
    assert back.last_plan is None


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
            intent_id=uuid.UUID(int=9),
            recipient="Marta Solis Vega",
            amount_minor=1_500,
            concept="tapas",
        )
    )

    back = there_and_back(out)

    assert back == out
    assert back.awaiting is Slot.CONFIRMATION
    assert isinstance(back.active, PaymentConfirmation)
    assert back.active.concept == "tapas"


def test_a_confirmation_keeps_the_currency_it_was_prepared_in() -> None:
    """Its own test, in a currency that is not the default, because that is the only way this
    fails: every other round-trip case uses EUR, so a payload with nowhere to put a currency
    rebuilt the right answer by accident and the loss was invisible."""
    out = IDLE.with_active(
        PaymentConfirmation(
            intent_id=uuid.UUID(int=9),
            recipient="Marta Solis Vega",
            amount_minor=1_500,
            currency="USD",
        )
    )

    back = there_and_back(out)

    assert isinstance(back.active, PaymentConfirmation)
    assert back.active.currency == "USD"


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


@pytest.mark.parametrize(
    "forged",
    [
        # A tag with none of the fields its state carries.
        {"workflow": "PAYMENT_CONFIRMATION"},
        {"workflow": "FINANCIAL_CLARIFICATION"},
        {"workflow": "PAYMENT_IN_PROGRESS"},
        {"workflow": "UNSUPPORTED_CAPABILITY"},
        # A tag carrying another state's fields.
        {"workflow": "UNSUPPORTED_CAPABILITY", "capability": "AVERAGE", "amount_minor": 1_500},
        {"workflow": "PAYMENT_IN_PROGRESS", "payment": {}, "intent_id": str(uuid.UUID(int=9))},
        # A slot no state asks for. A reply meant for a period must never fill a payment.
        {"workflow": "PAYMENT_IN_PROGRESS", "payment": {}, "awaiting": "WINDOW"},
        {"awaiting": "CONFIRMATION"},
        # An identifier that is not one. It used to reach `uuid.UUID(...)` inside the mapper.
        {
            "workflow": "PAYMENT_CONFIRMATION",
            "intent_id": "not-a-uuid",
            "recipient": "Marta",
            "amount_minor": 1_500,
        },
    ],
)
def test_a_state_this_service_never_issues_is_refused_rather_than_reinterpreted(
    forged: dict[str, object],
) -> None:
    """It cannot have come from here, so it is a 422 and not a silent idle.

    Reinterpreting used to feel safe — a confirmation with no intent became nothing, which at least
    could not pay anybody. What it also did was discard whatever conversation was really under way
    and tell the caller nothing, so a client bug looked like a user who had changed the subject.
    """
    with pytest.raises(ValidationError):
        ConversationPayload(**forged)


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


def test_a_transfer_confirmation_keeps_the_account_it_is_going_to() -> None:
    """The client draws the preview from this state, so a chat that could not tell a Bizum from a
    transfer would draw the same prompt for both — and the one difference that matters to the person
    approving it, twenty-four characters of account number, would not be on it."""
    out = IDLE.with_active(
        PaymentConfirmation(
            intent_id=uuid.UUID(int=9),
            recipient="Elena Marin",
            amount_minor=20_000,
            rail=Rail.TRANSFER,
            recipient_iban="ES9121000418450200051332",
        )
    )

    back = there_and_back(out)

    assert back == out
    assert isinstance(back.active, PaymentConfirmation)
    assert back.active.rail is Rail.TRANSFER
    assert back.active.recipient_iban == "ES9121000418450200051332"


def test_a_bizum_confirmation_carries_no_account() -> None:
    """Absent means BIZUM, so a client written before transfers keeps round-tripping a state it
    does not understand — and a Bizum that arrived with an account would be a state this service
    never issues."""
    out = IDLE.with_active(
        PaymentConfirmation(intent_id=uuid.UUID(int=9), recipient="Marta", amount_minor=1_500)
    )

    back = there_and_back(out)

    assert isinstance(back.active, PaymentConfirmation)
    assert back.active.rail is Rail.BIZUM
    assert back.active.recipient_iban is None
