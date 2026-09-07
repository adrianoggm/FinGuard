"""The two places a chart is remembered fill in how it came to be."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from support.visual_planners import BREAKDOWN_PROFILE, default_policy
from test_decisions_call_sites import turn_for
from test_visual_follow_up import CHARTED, FOLLOW_UP, PREVIOUS

from finguard_orchestrator.application.queries import ask_financial_question
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
from finguard_orchestrator.domain.models.visual_follow_up import VisualFollowUp
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

    Two departures from that file's own pattern, both required by what this test checks.

    First, the sentence: `test_visualization_stage2_core` uses "ponlo en barras" and similar --
    the brief's "ponlo en horizontal" names no keyword `interpret_visual` recognises (its style
    vocabulary is barra/linea/area/apil.../ordena.../circular/donut/anillo), so it would never
    produce a patch. "ordénalo de menor a mayor" is a proven ascending-sort sentence, kept here so
    the fixture actually exercises a restyle instead of asserting on `None`. Fix round 1 is what
    lets a restyle over this spec verify at all: `SPEC` (the floor's own plan for
    `BREAKDOWN_PROFILE`) ranks its dimension with a per-encoding sort, and before the wire carried
    a field's `order`, a descending restyle over this exact spec was wrongly refused (`rule=
    "order"`) the moment the context had crossed the wire once -- `test_a_round_tripped_ranked_
    bar_still_verifies_the_descending_sort` (`test_visualization_stage2_core.py`) pins that a
    restyle now verifies. The direction asked for here is ascending rather than descending
    specifically because fix wave 2's F2 answers a *descending* restyle over this same
    already-descending `SPEC` with `VISUAL_UNCHANGED` and no derived context -- exactly what this
    fixture exists to produce one of.

    Second, the round trip: the conversation is serialised through `conversation_mapper.to_payload`
    and back through `to_domain` before it reaches `_visual_only_response`, because that is what
    the real endpoint receives -- a conversation from the wire, where `source_plan`/`evidence_ids`
    were never serialised (only `derived_from`/`turn_id` are, per Task 3). The response's own
    conversation is converted back to a domain `Conversation` the same way (rather than left as the
    wire `ConversationPayload`, whose `VisualContextPayload` never carried `decision`/`source_plan`/
    `evidence_ids` to begin with) so the test can read those fields at all.
    """
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        transaction_ids=(uuid4(),),
    )
    initial_context = VisualContext(
        "chart-1", SPEC, BREAKDOWN_PROFILE, source_plan=plan, evidence_ids=(uuid4(),)
    )
    conversation = IDLE.with_visual_context(initial_context)
    from_wire = conversation_mapper.to_domain(conversation_mapper.to_payload(conversation))
    intent = interpret_visual("ordénalo de menor a mayor", from_wire.visual_contexts)
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


def test_a_reshape_names_the_chart_it_came_from(charted_result, chart_response) -> None:
    """Task 4: a follow-up classified `ANALYTICAL_RESHAPE` while a chart was in context produces a
    context that names the chart it came from -- the newest one on the conversation BEFORE this
    turn's is appended."""
    previous = VisualContext("chart-0", SPEC, BREAKDOWN_PROFILE)
    reshaped = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan"),
    )
    remembered = llm_mapper._remember_visual_context(reshaped, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from == "chart-0"


def test_an_unrelated_new_chart_names_no_origin(charted_result, chart_response) -> None:
    """The same chart-in-context setup as the reshape above, but classified `UNRELATED`: the new
    chart names no origin even though a previous chart exists."""
    previous = VisualContext("chart-0", SPEC, BREAKDOWN_PROFILE)
    unrelated = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("UNRELATED", "none"),
    )
    remembered = llm_mapper._remember_visual_context(unrelated, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from is None


async def test_the_handler_and_the_mapper_together_prove_derived_from(monkeypatch) -> None:
    """Fix round 1, Important 2. The two mapper tests above hand-set `visual_follow_up` with
    `replace(...)` and never run the handler; `test_visual_follow_up.py`'s handler tests read
    `result.visual_follow_up` and never run the mapper. Nothing before this test drove `stream()`'s
    convergence (`ask_financial_question.py`, the box read alongside `_decided_visual`) and
    `_remember_visual_context`'s `derived_from` read together -- the one seam this commit exists
    to join, and the one thing left unguarded: moving the convergence's `replace(...)` under a
    `visual_dataset is not None` guard, or attaching the box's value anywhere else, would leave
    every other test in this module green while the feature stayed dead.

    One real turn -- `"Solo restaurantes"` over `CHARTED` (which already holds `chart-1`) with
    `analysis=FOLLOW_UP` injected, exactly as `test_visual_follow_up.py`'s own seam tests inject it
    -- run three times with only the classification varied, monkeypatched the way
    `test_every_kind_is_a_record_row` (`test_visual_follow_up.py`) varies it: every case draws the
    identical real chart, and only `visual_follow_up.kind`'s effect on `derived_from` differs.
    `question_to_dto` is driven directly -- the composition `_visualization_to_dto` then
    `_remember_visual_context` that actually builds a `QuestionResponse` -- rather than chaining the
    two mapper functions by hand.
    """
    for kind, claimant, expected in (
        ("ANALYTICAL_RESHAPE", "follow_up_plan", "chart-1"),
        ("UNRELATED", "none", None),
        ("PRESENTATION_ONLY", "interpret_visual", None),
    ):
        monkeypatch.setattr(
            ask_financial_question,
            "classify_visual_follow_up",
            lambda query, analysis, conversation, kind=kind, claimant=claimant: VisualFollowUp(
                kind, claimant
            ),
        )
        result, _ = await turn_for(
            "Solo restaurantes", PREVIOUS, conversation=CHARTED, analysis=FOLLOW_UP
        )
        response = llm_mapper.question_to_dto(result)
        assert response.conversation.visual_contexts[-1].derived_from == expected, kind


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
