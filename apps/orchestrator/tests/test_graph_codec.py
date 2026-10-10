import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.domain.engine.operations import Ask, PrepareTransfer
from finguard_orchestrator.domain.engine.state import (
    ArtifactSource,
    ArtifactStatus,
    DialogueState,
    DialogueTurn,
    FinancialArtifact,
    FocusHistoryEntry,
    OwnTransferDraftPending,
    PaymentConfirmationPending,
    PaymentDraftPending,
    PlanFields,
    PreviousFocus,
    QueryFocus,
    Scope,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Rail, ResultKind
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.payment_result import PaymentPreview
from finguard_orchestrator.domain.models.query_answer import Group
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, FinancialPlan
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft


def test_dialogue_checkpoint_round_trips_typed_state_and_tuple_values() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)),
    )
    state = DialogueState(
        uuid4(),
        focus=QueryFocus(plan),
        previous_focus=PreviousFocus(QueryFocus(plan), Scope()),
        focus_history=(FocusHistoryEntry(QueryFocus(plan), Scope()),),
    ).remembered(DialogueTurn("gastos de julio", "Consulta realizada"))
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
        active_artifact=FinancialArtifact(
            plan, "EUR", 12_500, 3, (Group("SUPERMARKET", 12_500, 3),)
        ),
    )

    encoded = codec.dumps_state(state)
    assert '"active_artifact"' in encoded
    assert '"active_result"' not in encoded
    restored = codec.loads_state(encoded)

    assert restored == state
    assert restored.active_artifact is not None
    assert restored.active_artifact.groups == (Group("SUPERMARKET", 12_500, 3),)
    assert restored.active_artifact.status is ArtifactStatus.VERIFIED
    assert restored.active_artifact.source is ArtifactSource.BANKING_API


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
    candidates = (uuid4(), uuid4())
    draft_state = DialogueState(
        uuid4(),
        payment=PaymentDraftPending(
            PaymentDraft(
                recipient_name="Aitor Beltran Diaz",
                amount_minor=2_600,
                rail=Rail.TRANSFER,
                recipient_iban="ES9121000418450200051332",
            ),
            candidates,
            suspended=True,
        ),
    )
    confirmation_state = DialogueState(
        uuid4(),
        payment=PaymentConfirmationPending(
            preview,
            "workflow-opaque-123",
            PaymentDraft(
                recipient_name="Marta Solis Vega",
                amount_minor=2_600,
                concept="tapas",
                rail=Rail.BIZUM,
            ),
        ),
    )

    draft_restored = codec.loads_state(codec.dumps_state(draft_state))
    confirmation_restored = codec.loads_state(codec.dumps_state(confirmation_state))

    assert draft_restored == draft_state
    assert isinstance(draft_restored.payment, PaymentDraftPending)
    assert draft_restored.payment.candidate_contact_ids == candidates
    assert draft_restored.payment.suspended
    assert draft_restored.payment.draft.recipient_iban == "ES9121000418450200051332"
    assert confirmation_restored == confirmation_state
    assert confirmation_restored.payment is not None
    assert confirmation_restored.payment.preview.intent_id == intent_id
    assert confirmation_restored.payment.preview.expires_at == expires
    assert confirmation_restored.payment.workflow_id == "workflow-opaque-123"
    assert confirmation_restored.payment.draft == confirmation_state.payment.draft


def test_dialogue_checkpoint_migrates_payment_draft_without_suspended_field() -> None:
    encoded = json.loads(
        codec.dumps_state(
            DialogueState(
                uuid4(),
                payment=PaymentDraftPending(PaymentDraft(recipient_name="Marta Solis Vega")),
            )
        )
    )
    encoded["schema_version"] = 12
    encoded["state"]["f"]["payment"]["f"].pop("suspended")

    restored = codec.loads_state(json.dumps(encoded))

    assert isinstance(restored.payment, PaymentDraftPending)
    assert not restored.payment.suspended


def test_dialogue_checkpoint_round_trips_own_transfer_draft() -> None:
    draft = OwnTransferDraftPending(
        to_account_id=uuid4(), amount_minor=7_500, workflow_id=uuid4().hex
    )
    state = DialogueState(uuid4(), own_transfer=draft)

    restored = codec.loads_state(codec.dumps_state(state))

    assert restored == state


def test_own_transfer_workflow_id_must_be_a_uuid_hex() -> None:
    with pytest.raises(InvalidValueError, match="opaque UUID"):
        OwnTransferDraftPending(workflow_id="x" * 32)


def test_schema_seven_dialogue_loads_with_no_pending_own_transfer() -> None:
    encoded = json.loads(codec.dumps_state(DialogueState(uuid4(), version=4)))
    encoded["schema_version"] = 7
    encoded["state"]["f"].pop("own_transfer")

    restored = codec.loads_state(json.dumps(encoded))

    assert restored.version == 4
    assert restored.own_transfer is None


def test_schema_eight_payment_draft_without_candidate_ids_still_loads() -> None:
    state = DialogueState(uuid4(), payment=PaymentDraftPending(PaymentDraft(amount_minor=2_000)))
    encoded = json.loads(codec.dumps_state(state))
    encoded["schema_version"] = 8
    encoded["state"]["f"]["payment"]["f"].pop("candidate_contact_ids")

    restored = codec.loads_state(json.dumps(encoded))

    assert isinstance(restored.payment, PaymentDraftPending)
    assert restored.payment.draft.amount_minor == 2_000
    assert restored.payment.candidate_contact_ids == ()


def test_structured_operations_round_trip_dates_and_closed_enum_values() -> None:
    operations = (
        Ask(
            PlanFields(
                direction=Direction.EXPENSE,
                window=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
                categories=("RESTAURANTS",),
            )
        ),
        PrepareTransfer("Aitor Beltran Diaz", 1_200),
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
        active_artifact=FinancialArtifact(
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
    encoded["state"]["f"]["active_artifact"]["f"].pop("result_version")

    restored = codec.loads_state(json.dumps(encoded))

    assert restored.active_artifact is not None
    assert restored.active_artifact.result_version == 7
    assert restored.active_visual is None


def test_schema_fifteen_active_artifact_migrates_without_stable_ids() -> None:
    state = DialogueState(
        uuid4(),
        active_artifact=FinancialArtifact(
            FinancialPlan(
                direction=Direction.EXPENSE,
                date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)),
            ),
            "EUR",
            1_200,
            2,
            (Group("GROCERIES", 1_200, 2),),
        ),
    )
    encoded = json.loads(codec.dumps_state(state))
    encoded["schema_version"] = 15
    encoded["state"]["f"]["active_artifact"]["f"].pop("query_id")
    encoded["state"]["f"]["active_artifact"]["f"].pop("artifact_id")

    restored = codec.loads_state(json.dumps(encoded))

    assert restored.active_artifact is not None
    assert restored.active_artifact.query_id == ""
    assert restored.active_artifact.artifact_id == ""


def test_schema_sixteen_active_result_migrates_to_verified_financial_artifact() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31)),
        group_by=GroupBy.CATEGORY,
    )
    state = DialogueState(
        uuid4(),
        version=3,
        active_artifact=FinancialArtifact(
            plan,
            "EUR",
            12_500,
            3,
            (Group("SUPERMARKET", 12_500, 3),),
            query_id="query-3",
            artifact_id="artifact-3",
        ),
    )
    encoded = json.loads(codec.dumps_state(state))
    encoded["schema_version"] = 16
    state_fields = encoded["state"]["f"]
    artifact = state_fields.pop("active_artifact")
    artifact["$type"] = "ActiveQueryResult"
    artifact_fields = artifact["f"]
    for field in ("result_kind", "status", "source", "provenance"):
        artifact_fields.pop(field)
    state_fields["active_result"] = artifact

    restored = codec.loads_state(json.dumps(encoded))

    assert restored.active_artifact is not None
    assert restored.active_artifact.result_kind is ResultKind.BREAKDOWN
    assert restored.active_artifact.status is ArtifactStatus.VERIFIED
    assert restored.active_artifact.source is ArtifactSource.BANKING_API
    assert restored.active_artifact.provenance == ("query-3",)


def test_schema_three_conversation_loads_with_no_pending_payment() -> None:
    encoded = json.loads(codec.dumps_state(DialogueState(uuid4(), version=5)))
    encoded["schema_version"] = 3
    encoded["state"]["f"].pop("payment")

    restored = codec.loads_state(json.dumps(encoded))

    assert restored.version == 5
    assert restored.payment is None


def test_schema_four_pending_payment_migrates_without_workflow_handle() -> None:
    preview = PaymentPreview(
        intent_id=uuid4(),
        recipient="Marta Solis Vega",
        amount_minor=2_600,
        currency="EUR",
        balance_before_minor=70_000,
        balance_after_minor=67_400,
        expires_at=datetime(2026, 10, 9, 12, 30, tzinfo=UTC),
        rail=Rail.BIZUM,
        recipient_phone="+34600000000",
    )
    state = DialogueState(uuid4(), payment=PaymentConfirmationPending(preview))
    encoded = json.loads(codec.dumps_state(state))
    encoded["schema_version"] = 4
    encoded["state"]["f"]["payment"]["f"].pop("workflow_id")
    encoded["state"]["f"]["payment"]["f"].pop("draft")

    restored = codec.loads_state(json.dumps(encoded))

    assert restored.payment == PaymentConfirmationPending(preview)
