"""The two places a chart is remembered fill in how it came to be."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from support.visual_planners import BREAKDOWN_PROFILE, default_policy

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
    VisualDataset,
    VisualRow,
    _decided_visual,
)
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.models.conversation import IDLE
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_interpreter import interpret_visual
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.infrastructure.controllers.llm_controller import (
    _derived_context,
    _visual_only_response,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper, llm_mapper
from finguard_orchestrator.infrastructure.mappers.llm_mapper import _visualization_to_dto

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None


def _evidence_of(plan: object) -> tuple[UUID, ...]:
    return llm_mapper._evidence_of(plan)


async def _charted_result() -> AskFinancialQuestionResult:
    """A real turn, built the way `test_visualization_stage1._result` builds its own: an
    `AskFinancialQuestionResult` run through `_decided_visual` so it carries a real
    `visual_decision`. The one addition is `metric_definition` -- a `FinancialPlan` naming the two
    movements behind the figures -- which that helper never sets, because nothing before Task 4
    read it back off a chart's context."""
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        transaction_ids=(uuid4(), uuid4()),
    )
    result = AskFinancialQuestionResult(
        outcome="PLANNED",
        text="Resultado",
        plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
        groups=[SpokenGroup("visible", 100, 2), SpokenGroup("also visible", 50, 1)],
        visual_dataset=VisualDataset(
            rows=(VisualRow("Comida", 100), VisualRow("Casa", 50)), metric="SUM"
        ),
        metric_definition=plan,
    )
    return await _decided_visual(result, default_policy(), DEFAULT_PRESENTATION, "")


@pytest.fixture
async def charted_result() -> AskFinancialQuestionResult:
    return await _charted_result()


@pytest.fixture
def chart_response(charted_result: AskFinancialQuestionResult) -> object:
    """The `ChartVisualizationResponse` the mapper built for `charted_result`, the way
    `test_visualization_stage1` pairs its own real-turn result with `_visualization_to_dto`."""
    return _visualization_to_dto(charted_result)


@pytest.fixture
def restyle_turn() -> object:
    """Drives the restyle endpoint the way `test_visualization_stage2_core` does: a domain
    `Conversation` holding one `VisualContext`, a sentence `interpret_visual` turns into a patch,
    and `_visual_only_response` applying it.

    Three departures from that file's own pattern, all required by what this test checks.

    First, the sentence: `test_visualization_stage2_core` uses "ponlo en barras" and similar --
    the brief's "ponlo en horizontal" names no keyword `interpret_visual` recognises (its style
    vocabulary is barra/linea/area/apil.../ordena.../circular/donut/anillo), so it would never
    produce a patch. "ponlo en barras" restates the mark `SPEC` already has -- a no-op patch that
    still exercises a real restyle, chosen over a sort-based sentence for the reason the third
    departure below explains.

    Second, the round trip: the conversation is serialised through `conversation_mapper.to_payload`
    and back through `to_domain` before it reaches `_visual_only_response`, because that is what
    the real endpoint receives -- a conversation from the wire, where `source_plan`/`evidence_ids`
    were never serialised (only `derived_from`/`turn_id` are, per Task 3). The response's own
    conversation is converted back to a domain `Conversation` the same way (rather than left as the
    wire `ConversationPayload`, whose `VisualContextPayload` never carried `decision`/`source_plan`/
    `evidence_ids` to begin with) so the test can read those fields at all.

    Third, the spec: `SPEC` (the module-level floor's own plan for `BREAKDOWN_PROFILE`) ranks its
    dimension with a per-encoding sort -- `EncodingSort("descending", "value_minor")` on the `y`
    encoding -- because that is what a ranked breakdown bar always carries. `conversation_mapper.
    to_domain`'s own comment on its profile reconstruction says a round-tripped `VisualProfile`'s
    `role` is a narrowed derivation good only for `valid_for`/`merge_encoding_patch`; the same
    narrowing drops every field's `order` to the dataclass default `"NONE"`, because
    `VisualFieldPayload` never carried it either. Task 3's verifier reads exactly that `order` for
    its `order` rule -- unconditionally, before this fixture's own sentence is even considered --
    so a chart built with `SPEC`'s own baked-in sort is refused (`rule="order"`) the moment its
    context has crossed the wire once, whatever restyle is then asked of it. This fixture is about
    provenance, not about that wire gap, so it drives a spec with no per-encoding sort at all --
    structurally the same ranked breakdown bar, minus the one property this wire limitation cannot
    carry. (A follow-up ought to add `order` to the wire profile; until then, a sort-driven restyle
    over a context that has crossed the wire is refused even when the sort it asks for is one the
    floor's own chart already agrees with.)
    """
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        transaction_ids=(uuid4(),),
    )
    unsorted_spec = replace(
        SPEC, encodings=tuple(replace(encoding, sort=None) for encoding in SPEC.encodings)
    )
    initial_context = VisualContext(
        "chart-1", unsorted_spec, BREAKDOWN_PROFILE, source_plan=plan, evidence_ids=(uuid4(),)
    )
    conversation = IDLE.with_visual_context(initial_context)
    from_wire = conversation_mapper.to_domain(conversation_mapper.to_payload(conversation))
    intent = interpret_visual("ponlo en barras", from_wire.visual_contexts)
    assert intent is not None
    response = _visual_only_response(intent, from_wire)
    assert response.conversation is not None
    return SimpleNamespace(conversation=conversation_mapper.to_domain(response.conversation))


def test_a_fresh_chart_remembers_its_decision_plan_evidence_and_turn(
    charted_result, chart_response
) -> None:
    """`charted_result` is a fixture: an AskFinancialQuestionResult with a visual_decision and a
    FinancialPlan metric_definition whose transaction_ids are two UUIDs; `chart_response` is the
    ChartVisualizationResponse the mapper built for it. Build both the way test_visualization_stage1
    builds its real-turn fixture."""
    remembered = llm_mapper._remember_visual_context(charted_result, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.decision is charted_result.visual_decision
    assert context.source_plan is charted_result.metric_definition
    assert context.evidence_ids == charted_result.metric_definition.transaction_ids
    assert isinstance(context.turn_id, UUID)
    assert context.derived_from is None


def test_two_charts_in_one_process_get_two_turn_ids(charted_result, chart_response) -> None:
    first = llm_mapper._remember_visual_context(charted_result, chart_response)
    second = llm_mapper._remember_visual_context(charted_result, chart_response)
    first_turn_id = first.conversation.visual_contexts[-1].turn_id
    second_turn_id = second.conversation.visual_contexts[-1].turn_id
    assert first_turn_id != second_turn_id


def test_a_plan_without_evidence_yields_no_ids() -> None:
    assert _evidence_of(object()) == ()
    assert _evidence_of(None) == ()


def test_a_restyled_chart_names_its_origin_and_keeps_its_plan(restyle_turn) -> None:
    """`restyle_turn` drives the restyle endpoint (llm_controller) with a conversation holding
    `VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, source_plan=<a FinancialPlan>,
    evidence_ids=(uuid4(),))` and a restyle sentence; it returns the QuestionResponse's
    conversation, converted back to a domain `Conversation` (see the fixture's docstring for why).

    `derived.source_plan is None` and `derived.evidence_ids == ()` look like a broken copy, but they
    are the correct outcome: the restyle endpoint never sees the single-turn context above -- it
    sees `from_wire`, already round-tripped through the wire once, where those two fields do not
    travel. Within a single turn they would be copied faithfully; across a turn boundary, on this
    wire, they cannot be. `derived.decision is None` for the same reason: `VisualContextPayload`
    carries no `decision` field at all, so the response read back through `conversation_mapper.
    to_domain` (this fixture's own last line) never carries one either -- Task 3's restyle chart
    IS a `VisualDecision(source="patch")` in the process that built it (`_derived_context`'s
    direct, in-process test in `test_visualization_stage2_core.py` proves that construction), it
    is only unobservable here because nothing about a decision travels this wire."""
    response = restyle_turn
    contexts = response.conversation.visual_contexts
    derived = contexts[-1]
    assert derived.derived_from == "chart-1"
    assert derived.turn_id is not None and derived.turn_id != contexts[0].turn_id
    assert derived.decision is None  # decision never crosses the wire -- see the docstring above
    assert derived.source_plan is None
    assert derived.evidence_ids == ()


def test_the_restyle_helper_copies_a_trusted_contexts_provenance_forward() -> None:
    """`restyle_turn` above proves the wire's own emptying of `source_plan`/`evidence_ids`, which
    means it cannot also prove the copy that runs before the wire ever sees them: in that fixture,
    `trusted_context.source_plan`/`.evidence_ids` are already `None`/`()` by the time
    `_derived_context` reads them, so deleting the copy and falling through to the dataclass
    defaults would leave every assertion there unchanged.

    `_derived_context` is `_visual_only_response`'s own copy step, pulled into a module-level
    function precisely so it can be driven directly with an in-process `VisualContext` that still
    carries real provenance -- no wire, no round trip, nothing to empty it first. This is the test
    that fails if `source_plan=trusted.source_plan, evidence_ids=trusted.evidence_ids` is deleted
    from its construction."""
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        transaction_ids=(uuid4(), uuid4()),
    )
    trusted = VisualContext(
        "chart-1",
        SPEC,
        BREAKDOWN_PROFILE,
        source_plan=plan,
        evidence_ids=(uuid4(), uuid4()),
        turn_id=uuid4(),
    )
    derived = _derived_context(trusted, SPEC, "chart-2")
    assert derived.source_plan is trusted.source_plan
    assert derived.evidence_ids == trusted.evidence_ids
    assert derived.derived_from == trusted.chart_id
    assert isinstance(derived.turn_id, UUID) and derived.turn_id != trusted.turn_id
    assert derived.decision is None
