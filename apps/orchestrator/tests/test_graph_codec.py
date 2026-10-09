import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.domain.engine.operations import Ask
from finguard_orchestrator.domain.engine.state import (
    ActiveQueryResult,
    DialogueState,
    DialogueTurn,
    PaymentConfirmationPending,
    PaymentDraftPending,
    PlanFields,
)
from finguard_orchestrator.domain.enums import Direction, Rail
from finguard_orchestrator.domain.models.payment_result import PaymentPreview
from finguard_orchestrator.domain.models.query_answer import Group
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, FinancialPlan
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft


def test_dialogue_checkpoint_round_trips_typed_state_and_tuple_values() -> None:
    state = DialogueState(uuid4()).remembered(DialogueTurn("gastos de julio", "Consulta realizada"))
    value = codec.loads_state(codec.dumps_state(state))

    assert value == state
    assert isinstance(value.turns, tuple)


def test_dialogue_checkpoint_round_trips_bounded_aggregate_artifact() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
    )
    state = DialogueState(
        uuid4(),
        active_result=ActiveQueryResult(plan, "EUR", 12_500, 3, (Group("SUPERMARKET", 12_500, 3),)),
    )

    restored = codec.loads_state(codec.dumps_state(state))

    assert restored == state
    assert restored.active_result is not None
    assert restored.active_result.groups == (Group("SUPERMARKET", 12_500, 3),)


def test_dialogue_checkpoint_round_trips_paused_payment_draft_and_confirmation() -> None:
    intent_id = uuid4()
    expires = datetime(2026, 10, 9, 12, 30, tzinfo=UTC)
    preview = PaymentPreview(
        intent_id=intent_id,
        recipient="Marta Solis Vega",
        amount_minor=2_600,
        currency="EUR",
        balance_before_minor=70_000,
        balance_after_minor=67_400,
        expires_at=expires,
        rail=Rail.BIZUM,
        recipient_phone="+34600000000",
    )
    draft_state = DialogueState(
        uuid4(), payment=PaymentDraftPending(PaymentDraft(recipient_name="Marta"))
    )
    confirmation_state = DialogueState(uuid4(), payment=PaymentConfirmationPending(preview))

    draft_restored = codec.loads_state(codec.dumps_state(draft_state))
    confirmation_restored = codec.loads_state(codec.dumps_state(confirmation_state))

    assert draft_restored == draft_state
    assert confirmation_restored == confirmation_state
    assert confirmation_restored.payment is not None
    assert confirmation_restored.payment.preview.intent_id == intent_id
    assert confirmation_restored.payment.preview.expires_at == expires


def test_structured_operations_round_trip_dates_and_closed_enum_values() -> None:
    operations = (
        Ask(
            PlanFields(
                direction=Direction.EXPENSE,
                window=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
                categories=("RESTAURANTS",),
            )
        ),
    )

    decoded = codec.loads(codec.dumps(operations))

    assert decoded == operations
    assert isinstance(decoded[0].fields.window.start, date)


def test_unknown_dialogue_schema_fails_closed() -> None:
    with pytest.raises(codec.UnknownCheckpointTypeError, match="dialogue schema"):
        codec.loads_state('{"schema_version":99,"state":{}}')


def test_previous_dialogue_schema_migrates_result_reference_version() -> None:
    state = DialogueState(
        uuid4(),
        version=7,
        active_result=ActiveQueryResult(
            FinancialPlan(
                direction=Direction.EXPENSE,
                date_range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
            ),
            "EUR",
            12_500,
            3,
            (Group("SUPERMARKET", 12_500, 3),),
        ),
    )
    encoded = json.loads(codec.dumps_state(state))
    encoded["schema_version"] = 2
    encoded["state"]["f"].pop("active_visual")
    encoded["state"]["f"]["active_result"]["f"].pop("result_version")

    restored = codec.loads_state(json.dumps(encoded))

    assert restored.active_result is not None
    assert restored.active_result.result_version == 7
    assert restored.active_visual is None


def test_schema_three_conversation_loads_with_no_pending_payment() -> None:
    encoded = json.loads(codec.dumps_state(DialogueState(uuid4(), version=5)))
    encoded["schema_version"] = 3
    encoded["state"]["f"].pop("payment")

    restored = codec.loads_state(json.dumps(encoded))

    assert restored.version == 5
    assert restored.payment is None
