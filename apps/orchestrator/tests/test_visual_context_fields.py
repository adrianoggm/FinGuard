"""A VisualContext remembers how the chart came to be, not only what it is."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from support.visual_planners import BREAKDOWN_PROFILE

from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ChartEncoding,
    ChartSpec,
    VisualContext,
    VisualField,
    VisualProfile,
)
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None


def test_the_three_positional_fields_still_build_a_context() -> None:
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE)
    assert (context.decision, context.source_plan, context.evidence_ids) == (None, None, ())
    assert (context.derived_from, context.turn_id) == (None, None)


def test_a_context_carries_its_decision_evidence_and_turn() -> None:
    decision = VisualDecision(spec=SPEC, profile=BREAKDOWN_PROFILE, source="deterministic")
    turn = uuid4()
    ids = (uuid4(), uuid4())
    context = VisualContext(
        "chart-1", SPEC, BREAKDOWN_PROFILE, decision=decision, evidence_ids=ids, turn_id=turn
    )
    assert context.decision is decision
    assert context.evidence_ids == ids
    assert context.turn_id == turn


def test_derived_from_must_be_a_chart_id() -> None:
    with pytest.raises(ValueError, match="derived_from"):
        VisualContext("chart-2", SPEC, BREAKDOWN_PROFILE, derived_from="not a chart id!")
    context = VisualContext("chart-2", SPEC, BREAKDOWN_PROFILE, derived_from="chart-1")
    assert context.derived_from == "chart-1"


def test_a_context_cannot_derive_from_itself() -> None:
    with pytest.raises(ValueError, match="derived_from"):
        VisualContext("chart-3", SPEC, BREAKDOWN_PROFILE, derived_from="chart-3")


def test_evidence_ids_are_uuids() -> None:
    with pytest.raises(TypeError):
        VisualContext("chart-4", SPEC, BREAKDOWN_PROFILE, evidence_ids=("not-a-uuid",))  # type: ignore[arg-type]


def test_replace_keeps_the_new_fields() -> None:
    context = VisualContext("chart-5", SPEC, BREAKDOWN_PROFILE, derived_from="chart-1")
    assert replace(context, chart_id="chart-6").derived_from == "chart-1"


# --- Task 1 (Wave 4.5): descriptive provenance, sealed with the rest ---------------------------


def test_a_contexts_source_and_fallback_reason_round_trip_through_the_wire() -> None:
    """`source`/`fallback_reason` survive `to_payload` -> `to_domain` exactly, the same way
    `window`/`plan_digest` already do (the fix wave's own round-trip test)."""
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, source="llm", fallback_reason=None)
    conversation = Conversation(visual_contexts=(context,))
    payload = conversation_mapper.to_payload(conversation)
    (item,) = payload.visual_contexts
    assert item.source == "llm"
    assert item.fallback_reason is None
    (restored,) = conversation_mapper.to_domain(payload).visual_contexts
    assert restored.source == "llm"
    assert restored.fallback_reason is None


def test_a_deterministic_fallback_payload_round_trips_both_fields() -> None:
    context = VisualContext(
        "chart-1", SPEC, BREAKDOWN_PROFILE, source="deterministic", fallback_reason="timeout"
    )
    conversation = Conversation(visual_contexts=(context,))
    payload = conversation_mapper.to_payload(conversation)
    (restored,) = conversation_mapper.to_domain(payload).visual_contexts
    assert restored.source == "deterministic"
    assert restored.fallback_reason == "timeout"


def test_a_tampered_source_untrusts_a_sealed_context() -> None:
    """A digest is as much a part of what the seal covers as `plan_digest` is -- changing `source`
    in place, after the seal, must untrust the context the same way (mirrors
    `test_a_tampered_plan_digest_untrusts_a_sealed_context`, `test_visualization_stage1.py`)."""
    codec = MetricTokenCodec({"k1": b"x" * 32}, "k1")
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, source="llm")
    conversation_id = uuid4()
    sealed = conversation_mapper.seal_context(context, conversation_id, 0, "holder-a", codec)
    assert sealed.token is not None

    def verify(token: str | None, conv_id, position: int, semantics: dict[str, object]):
        return codec.verify_context(token, "holder-a", conv_id, position, semantics)

    payload = conversation_mapper.to_payload(
        Conversation(conversation_id=conversation_id, visual_contexts=(sealed,))
    )
    payload.visual_contexts[0].source = "patch"
    tampered = conversation_mapper.to_domain(payload, verify=verify).visual_contexts[0]
    assert tampered.trusted is False and tampered.token is None
    assert tampered.trust_reason == "content"


# --- Task 2 (Wave 3 hardening, U-4/C-5/A-6): the profile crosses the wire whole -----------------


def test_per_field_cardinality_round_trips_distinctly() -> None:
    """Two nominal fields with different cardinalities must come back with their own numbers, not
    the one dataset-level count `_context_payload` used to pick from the first nominal dimension
    and copy onto every field (A-6). `field_b` here stands for the field the donut rule or the
    drill would judge differently if it silently inherited `field_a`'s count instead of its own."""
    profile = VisualProfile(
        fields=(
            VisualField("field_a", "nominal", "dimension", 6),
            VisualField("field_b", "nominal", "dimension", 3),
            VisualField("value_minor", "quantitative", "measure", 0),
        ),
        metric="SUM",
        unit="money",
    )
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "field_a", "nominal"),
            ChartEncoding("color", "field_b", "nominal"),
            ChartEncoding("y", "value_minor", "quantitative"),
        ),
    )
    context = VisualContext("chart-1", spec, profile)
    conversation = Conversation(visual_contexts=(context,))
    payload = conversation_mapper.to_payload(conversation)
    (restored,) = conversation_mapper.to_domain(payload).visual_contexts
    assert restored.profile.field("field_a").cardinality == 6
    assert restored.profile.field("field_b").cardinality == 3


def test_row_count_and_plan_metric_round_trip_so_measure_metric_reads_true() -> None:
    """`row_count`/`plan_metric` used to be zeroed by `to_domain` regardless of what the wire
    carried (U-4/C-5). A COMPARISON-of-COUNT chart explained on a later turn must still read
    `measure_metric() == "COUNT"`, not fall back to the shape marker and say "importe" (A-7's
    defect, reachable here through the round trip A-6/U-4 own)."""
    profile = VisualProfile(
        fields=(
            VisualField("month", "temporal", "dimension", 0),
            VisualField("value_minor", "quantitative", "measure", 0),
        ),
        metric="COMPARISON",
        unit="count",
        row_count=42,
        plan_metric="COUNT",
    )
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "month", "temporal"),
            ChartEncoding("y", "value_minor", "quantitative"),
        ),
    )
    context = VisualContext("chart-1", spec, profile)
    conversation = Conversation(visual_contexts=(context,))
    payload = conversation_mapper.to_payload(conversation)
    (restored,) = conversation_mapper.to_domain(payload).visual_contexts
    assert restored.profile.row_count == 42
    assert restored.profile.measure_metric() == "COUNT"


def test_an_unknown_source_value_is_a_422() -> None:
    """A wrong `source` is a 422 like any other field -- the same convention
    `test_a_derived_from_that_is_not_a_chart_id_is_refused_at_the_edge` (`test_visualization_
    stage1.py`) already proves against `ConversationPayload.model_validate` directly, standing in
    for the request body FastAPI validates the same way at `POST /v1/questions`."""
    conversation = conversation_mapper.to_payload(
        Conversation(visual_contexts=(VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE),))
    )
    raw = conversation.model_dump(mode="json")
    raw["visual_contexts"][0]["source"] = "not-a-real-source"
    with pytest.raises(ValidationError):
        ConversationPayload.model_validate(raw)
