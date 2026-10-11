"""A VisualContext remembers how the chart came to be, not only what it is."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from support.visual_planners import BREAKDOWN_PROFILE

from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    VisualContext,
    VisualDecision,
)
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper

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


# --- Task 2 (Wave 3 hardening, U-4/C-5/A-6): the profile crosses the wire whole -----------------


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
