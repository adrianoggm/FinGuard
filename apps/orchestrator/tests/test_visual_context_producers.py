"""The two places a chart is remembered fill in how it came to be."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import UUID, uuid4

import pytest
from support.visual_planners import BREAKDOWN_PROFILE, default_policy

from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
)
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    VisualContext,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.models.conversation import IDLE
from finguard_orchestrator.domain.models.visual_follow_up import VisualFollowUp
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_explanation import explain_chart
from finguard_orchestrator.domain.services.visual_profile import drawn_labels, visual_profile_of
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import VisualContextPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper, llm_mapper
from finguard_orchestrator.infrastructure.mappers.llm_mapper import _visualization_to_dto
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None
# The mapper carries `visual_plan_digest` through as an opaque fingerprint, so any sixteen hex
# characters stand in for the plan that drew the chart.
DIGEST = "0123456789abcdef"


async def _decided(result: AskFinancialQuestionResult) -> AskFinancialQuestionResult:
    """The Result as the graph hands it to the mapper: the chart decided from the dataset's
    profile by the visual policy, and carried on the Result."""
    dataset, spoken = result.visual_dataset, result.plan_context
    assert dataset is not None and spoken is not None
    profile = visual_profile_of(
        dataset, total_minor=result.total_minor, group_by=spoken.group_by, plan_metric=spoken.metric
    )
    decision = await default_policy().decide("", profile, DEFAULT_PRESENTATION)
    return result if decision is None else replace(result, visual_decision=decision)


def _evidence_of(plan: object) -> tuple[UUID, ...]:
    return llm_mapper._evidence_of(plan)


async def _charted_result() -> AskFinancialQuestionResult:
    """A real turn, built the way `test_visualization_stage1._result` builds its own: an
    `AskFinancialQuestionResult` run through `_decided` so it carries a real
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
        # The digest of the plan that drew THIS chart, set once on the Result, before anything a
        # test does afterward could change `metric_definition`.
        visual_plan_digest=DIGEST,
    )
    return await _decided(result)


@pytest.fixture
async def charted_result() -> AskFinancialQuestionResult:
    return await _charted_result()


async def _charted_result_grouped_by_category() -> AskFinancialQuestionResult:
    """The same turn as `_charted_result`, except the plan that draws the chart already groups by
    CATEGORY -- so the digest sealed on the newest context is the digest of this plan from the
    start, the R7 case a matching digest is meant to describe (as opposed to `charted_result`'s
    own `GroupBy.NONE` plan, later mutated by a test AFTER the chart is drawn -- exactly the stale,
    non-matching case R7 exists to catch)."""
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        transaction_ids=(uuid4(), uuid4()),
        group_by="CATEGORY",
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
        visual_plan_digest=DIGEST,
    )
    return await _decided(result)


@pytest.fixture
def chart_response(charted_result: AskFinancialQuestionResult) -> object:
    """The `ChartVisualizationResponse` the mapper built for `charted_result`, the way
    `test_visualization_stage1` pairs its own real-turn result with `_visualization_to_dto`."""
    return _visualization_to_dto(charted_result)


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


def test_a_fresh_chart_carries_the_labels_its_dataset_draws(charted_result, chart_response) -> None:
    """Task 1 (Wave 4.3): `_remember_visual_context` computes `drawn_labels` off the same
    `result.visual_dataset` its profile already came from, and puts the three fields it returns
    on the `VisualContext` it builds -- before the seal, so a holder's own token covers them."""
    remembered = llm_mapper._remember_visual_context(charted_result, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    expected = drawn_labels(charted_result.visual_dataset)
    assert context.labels == expected.labels
    assert context.series_labels == expected.series_labels
    assert context.ranked == expected.ranked


async def test_a_fresh_chart_carries_the_window_and_plan_digest() -> None:
    """The fix wave's own two provenance facts: the window this turn's dataset actually covers
    (carried up from `VisualDataset.window`) and a fingerprint of the plan that produced it, both
    set before the seal, the same as `test_a_fresh_chart_carries_the_labels_its_dataset_draws`
    proves for labels above.

    I-1 (fix wave, D-4.5-6/M-2): the digest sealed onto the context now comes from `result.
    visual_plan_digest` -- `stream()`'s own line, computed from the plan that produced the
    dataset BEFORE any back-fill -- not from `result.metric_definition` recomputed here. This
    fixture builds the Result directly (bypassing `stream()`), so it sets `visual_plan_digest`
    by hand the same way `stream()` would have."""
    plan = FinancialPlan(
        Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        transaction_ids=(uuid4(), uuid4()),
    )
    window = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 20))
    result = AskFinancialQuestionResult(
        outcome="PLANNED",
        text="Resultado",
        plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
        groups=[SpokenGroup("visible", 100, 2), SpokenGroup("also visible", 50, 1)],
        visual_dataset=VisualDataset(
            rows=(VisualRow("Comida", 100), VisualRow("Casa", 50)), metric="SUM", window=window
        ),
        metric_definition=plan,
        visual_plan_digest=DIGEST,
    )
    charted = await _decided(result)
    response = _visualization_to_dto(charted)
    remembered = llm_mapper._remember_visual_context(charted, response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.window == window
    assert context.plan_digest == DIGEST


def test_a_fresh_chart_with_no_financial_plan_carries_no_digest(
    charted_result, chart_response
) -> None:
    """`plan_digest` is `None` whenever the turn produced no `visual_plan_digest` at all --
    `charted_result`'s own fixture sets one for its own `metric_definition`, so this drives the
    guard with `None` directly (as `stream()` would leave it when `metric_definition` is not a
    `FinancialPlan` at the point the digest is taken) rather than asserting only the positive
    case above."""
    no_plan = replace(charted_result, metric_definition=None, visual_plan_digest=None)
    remembered = llm_mapper._remember_visual_context(no_plan, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.plan_digest is None


def test_a_kpi_or_no_chart_result_leaves_no_context(charted_result) -> None:
    """Unchanged behaviour: `_remember_visual_context`'s own guard (`not isinstance(visualization,
    ChartVisualizationResponse)`) is what a KPI response and a turn with no visualization at all
    both fail, and this task changes nothing about it -- the conversation must come back exactly
    as it arrived."""
    remembered = llm_mapper._remember_visual_context(charted_result, None)
    assert remembered.conversation.visual_contexts == charted_result.conversation.visual_contexts


def test_a_reshape_names_the_chart_it_came_from(charted_result, chart_response) -> None:
    """B-3 (Wave 3 hardening): a chart whose plan descends from the trusted chart's own plan --
    same window, same direction, a narrowing or a split of its categories -- names that chart as
    its origin. `visual_follow_up` still carries `ANALYTICAL_RESHAPE` here (a real reshape reads
    this way too), but it is `previous.source_plan` matching `charted_result.metric_definition`
    that decides it now, not the kind -- `test_derived_from_is_decided_by_the_plan_not_the_verb`
    below is the pinned proof that the kind alone no longer does.

    F-H3 (Wave 3 hardening, fix round): `plan_continues_conversation=True` stands in for the turn
    that continued the conversation's plan -- the causal fact `derived_from` requires alongside
    the structural match, so this fixture, which builds its result by hand, states it explicitly.
    """
    previous = VisualContext(
        "chart-0", SPEC, BREAKDOWN_PROFILE, source_plan=charted_result.metric_definition
    )
    reshaped = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan"),
        plan_continues_conversation=True,
    )
    remembered = llm_mapper._remember_visual_context(reshaped, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from == "chart-0"


def test_a_reshape_over_an_untrusted_previous_names_no_origin(
    charted_result, chart_response
) -> None:
    """m9 (fix wave): a new, sealed chart used to be able to name an *untrusted* chart as its
    `derived_from` -- only the id crossed over (this turn's own dataset still built the new
    chart's spec and profile), but `derived_from` is what «el gráfico anterior» resolves through,
    so a legitimate chart's provenance could point at one the server never vouched for. The trust
    gate wins even when the plan matches (`source_plan` is set here, identical to the reshape test
    above) -- an untrusted chart names no origin whatever its own plan says.
    """
    previous = replace(
        VisualContext(
            "chart-0", SPEC, BREAKDOWN_PROFILE, source_plan=charted_result.metric_definition
        ),
        trusted=False,
        trust_reason="content",
    )
    reshaped = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan"),
    )
    remembered = llm_mapper._remember_visual_context(reshaped, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from is None


def test_a_drill_down_names_the_chart_it_drilled(charted_result, chart_response) -> None:
    """The same fact-based rule as the reshape above, for a follow-up classified `DRILL_DOWN`."""
    previous = VisualContext(
        "chart-0", SPEC, BREAKDOWN_PROFILE, source_plan=charted_result.metric_definition
    )
    drilled = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("DRILL_DOWN", "drill_down_reader"),
        plan_continues_conversation=True,
    )
    remembered = llm_mapper._remember_visual_context(drilled, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from == "chart-0"


def test_a_drill_down_over_an_untrusted_previous_names_no_origin(
    charted_result, chart_response
) -> None:
    """The m9 rule holds for a drill too: an untrusted previous chart is never named as the
    origin, whether the follow-up that produced the new chart was a reshape or a drill, and
    whether or not its own plan matches."""
    previous = replace(
        VisualContext(
            "chart-0", SPEC, BREAKDOWN_PROFILE, source_plan=charted_result.metric_definition
        ),
        trusted=False,
        trust_reason="content",
    )
    drilled = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("DRILL_DOWN", "drill_down_reader"),
    )
    remembered = llm_mapper._remember_visual_context(drilled, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from is None


def test_an_unrelated_new_chart_names_no_origin(charted_result, chart_response) -> None:
    """B-3: the same chart-in-context setup as the reshape above, but `chart-0`'s own plan (a
    different window: THIS_YEAR rather than `charted_result`'s LAST_MONTH) shares no lineage with
    the plan that drew the new one -- so the new chart names no origin, whatever
    `visual_follow_up` says. Kept at `UNRELATED` here because that is what an unrelated question
    is classified, but the kind is no longer what this reads (B-3): a mismatched plan behind an
    `ANALYTICAL_RESHAPE` label would refuse exactly the same way."""
    unrelated_plan = FinancialPlan(
        direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_YEAR)
    )
    previous = VisualContext("chart-0", SPEC, BREAKDOWN_PROFILE, source_plan=unrelated_plan)
    unrelated = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("UNRELATED", "none"),
    )
    remembered = llm_mapper._remember_visual_context(unrelated, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from is None


def test_two_unrelated_turns_sharing_a_shape_do_not_claim_each_other(
    charted_result, chart_response
) -> None:
    """F-H3 (Wave 3 hardening, fix round): the review's own named risk. `chart-0` and
    `charted_result` share window and direction and neither names a category -- exactly the
    common bare-window shape `_same_scope` alone would call compatible -- but nothing continued
    this turn from that chart: `plan_continues_conversation` is `False` (this turn's plan was
    read fresh, not patched from the conversation), the honest state of a second, independently
    asked "¿cuánto gasté?" several turns after an unrelated one. `derived_from` must stay `None`
    even though the structural check alone (`_same_lineage`) would pass -- the causal flag is the
    gate now, not a second vote alongside the shape.
    """
    previous = VisualContext(
        "chart-0", SPEC, BREAKDOWN_PROFILE, source_plan=charted_result.metric_definition
    )
    coincidental = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("UNRELATED", "none"),
        plan_continues_conversation=False,
    )
    remembered = llm_mapper._remember_visual_context(coincidental, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from is None


def test_derived_from_is_decided_by_the_plan_not_the_verb(charted_result, chart_response) -> None:
    """B-3's own pin: a chart whose `visual_follow_up.kind` is `UNRELATED` -- the classification a
    turn like "enséñamelo por meses" can get, because the verb alone does not read as a
    follow-up -- still derives from the trusted chart in view when its plan is a refinement of the
    same window and categories. The kind used to be the only thing `_remember_visual_context`
    read; this is the case where it disagreed with the fact and the fact must win.

    F-H3 (Wave 3 hardening, fix round): the fact itself moved on from B-3's own version of it.
    B-3 read the *plan's shape* as the fact; the review named the gap that leaves (two unrelated
    turns can share a shape by coincidence) and this reads the *reader's own causal claim*
    (`plan_continues_conversation`) instead, with the shape kept only as the sanity check below
    it. This unit fixture states that fact by hand, same as the kind it already stated by hand.
    """
    previous = VisualContext(
        "chart-0", SPEC, BREAKDOWN_PROFILE, source_plan=charted_result.metric_definition
    )
    unrelated_but_same_plan = replace(
        charted_result,
        conversation=charted_result.conversation.with_visual_context(previous),
        visual_follow_up=VisualFollowUp("UNRELATED", "none"),
        plan_continues_conversation=True,
    )
    remembered = llm_mapper._remember_visual_context(unrelated_but_same_plan, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.derived_from == "chart-0"


def test_two_charts_in_one_process_get_two_turn_ids(charted_result, chart_response) -> None:
    first = llm_mapper._remember_visual_context(charted_result, chart_response)
    second = llm_mapper._remember_visual_context(charted_result, chart_response)
    first_turn_id = first.conversation.visual_contexts[-1].turn_id
    second_turn_id = second.conversation.visual_contexts[-1].turn_id
    assert first_turn_id != second_turn_id


def test_a_plan_without_evidence_yields_no_ids() -> None:
    assert _evidence_of(object()) == ()
    assert _evidence_of(None) == ()


# --- Task 3 (Wave 4.4): `spoken` on a VOICE charted answer ------------------------------------


async def test_a_voice_charted_answer_carries_a_spoken_summary() -> None:
    """`question_to_dto(..., spoken_for_voice=True)` over a charted result fills `spoken` with the
    presenter's own sentence -- named here by its top label (the dataset draws "Comida" (100)
    ahead of "Casa" (50), ranked) once the plan that DREW THE CHART groups by CATEGORY.

    R7 (Wave 4.5): built via `_charted_result_grouped_by_category` rather than `charted_result`
    mutated after the fact, so `plan_digest(metric_definition) == newest.plan_digest` actually
    holds -- the case this test exists to describe is the plan matching the chart, not a
    same-shaped plan swapped in afterwards (that is `test_r7_...stale...` below)."""
    voice_result = await _charted_result_grouped_by_category()
    response = llm_mapper.question_to_dto(voice_result, spoken_for_voice=True)
    assert response.spoken is not None
    assert response.spoken == "Tus gastos por categoría, con Comida en cabeza."
    assert response.text == "Resultado"  # the screen keeps the full written answer regardless


def test_the_same_result_over_text_channel_has_no_spoken(charted_result) -> None:
    """`spoken_for_voice=False` (the TEXT route's own value) computes nothing at all."""
    response = llm_mapper.question_to_dto(charted_result, spoken_for_voice=False)
    assert response.spoken is None


def test_a_voice_kpi_answer_has_no_spoken() -> None:
    """A KPI turn draws no `ChartVisualizationResponse` at all, so `spoken_for_voice=True` still
    computes nothing -- there is no chart for a voice summary to be derived from."""
    result = AskFinancialQuestionResult(outcome="ANSWERED", text="Gastaste 42 €", total_minor=4200)
    response = llm_mapper.question_to_dto(result, spoken_for_voice=True)
    assert response.visualization is None
    assert response.spoken is None


# --- Task 3 (Wave 4.5): `explanation` on every charted answer, on every channel -----------------


def _remembered_newest(result: AskFinancialQuestionResult) -> VisualContext:
    """The exact domain `VisualContext` `question_to_dto` computes `explanation`/`spoken` from --
    built the same way the mapper itself builds it (`_visualization_to_dto` then
    `_remember_visual_context`), so a test can call `explain_chart` over the very object the
    mapper used rather than a wire round trip, which does not preserve every `VisualProfile`
    field (`conversation_mapper.to_domain` zeroes `row_count`, for one) and so is not equivalent
    for this purpose."""
    visualization = _visualization_to_dto(result)
    remembered = llm_mapper._remember_visual_context(result, visualization)
    return remembered.conversation.visual_contexts[-1]


def test_a_text_charted_answer_carries_its_explanation(charted_result) -> None:
    """`question_to_dto` fills `explanation` from `explain_chart` over the newest sealed context,
    on TEXT (`spoken_for_voice=False`) exactly as it does on VOICE."""
    expected = explain_chart(_remembered_newest(charted_result))
    response = llm_mapper.question_to_dto(charted_result, spoken_for_voice=False)
    assert response.explanation is not None
    assert response.explanation.text == expected.as_text()
    assert response.explanation.chart_reason == expected.chart_reason
    assert response.explanation.ordering_reason == expected.ordering_reason
    assert response.explanation.scope_summary == expected.scope_summary
    assert response.explanation.provenance == "Elegido por las reglas de FinGuard."
    assert "€" not in response.explanation.text


def test_a_voice_charted_answer_also_carries_its_explanation(charted_result) -> None:
    """The same thing, on VOICE -- `explanation` does not depend on `spoken_for_voice`."""
    expected = explain_chart(_remembered_newest(charted_result))
    response = llm_mapper.question_to_dto(charted_result, spoken_for_voice=True)
    assert response.explanation is not None
    assert response.explanation.text == expected.as_text()
    assert response.spoken is not None
    assert "€" not in response.spoken


def test_a_kpi_answer_has_no_explanation() -> None:
    """A KPI turn draws no `ChartVisualizationResponse`, so there is no chart to explain."""
    result = AskFinancialQuestionResult(outcome="ANSWERED", text="Gastaste 42 €", total_minor=4200)
    response = llm_mapper.question_to_dto(result, spoken_for_voice=True)
    assert response.visualization is None
    assert response.explanation is None


async def test_metric_definition_borrowed_is_false_until_the_backfill_sets_it() -> None:
    """The flag's own default (`AskFinancialQuestionResult.metric_definition_borrowed = False`)
    is what a producer that stamped its own plan leaves untouched -- exactly the shape
    `_charted_result_grouped_by_category` builds (and, since round 3, exactly what `_answered`
    itself now does for real), which is why `question_to_dto` still speaks the specific noun for it
    (`test_a_voice_charted_answer_carries_a_spoken_summary`, above). Pinned here directly, on the
    field, so the two ends of R14/R15's contract -- False on a stamped plan, True once `stream()`'s
    back-fill runs for a producer that has none -- are each asserted once, in one place."""
    voice_result = await _charted_result_grouped_by_category()
    assert voice_result.metric_definition_borrowed is False


def test_a_fresh_charts_newest_context_carries_the_decisions_source(
    charted_result, chart_response
) -> None:
    """Task 1 (Wave 4.5): `charted_result` drives the default deterministic policy
    (`default_policy()`, `_charted_result`'s own fixture), so `visual_decision.source ==
    "deterministic"` with no `fallback_reason` -- `_remember_visual_context` copies both onto the
    context it builds, before the seal, the same way it already does for `window`/`plan_digest`."""
    assert charted_result.visual_decision.source == "deterministic"
    remembered = llm_mapper._remember_visual_context(charted_result, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.source == "deterministic"
    assert context.fallback_reason is None


# --- Task 3: sealed where it is born -----------------------------------------------------------


def test_a_fresh_chart_is_sealed_for_its_holder_and_unsealed_without_one(
    charted_result, chart_response
) -> None:
    """`question_to_dto` builds `seal` itself from the two keyword arguments it already takes and
    hands it to `_remember_visual_context`, so the context it mints is sealed before `to_payload`
    ever sees it -- signed at birth, not by a second pass over the wire afterward.

    With no holder to seal for -- every existing caller in this codebase's tests, and every turn
    on a channel/feature-flag combination the routes gate to `None` -- the context stays exactly
    as it always has: unsealed, and `trusted=True` in-process, which is `VisualContext`'s own
    default rather than anything this task has to set.
    """
    tokens = MetricTokenCodec({"active": b"x" * 32}, "active")

    sealed = llm_mapper.question_to_dto(charted_result, holder_reference="h", metric_tokens=tokens)
    assert sealed.conversation is not None
    (payload_context,) = sealed.conversation.visual_contexts[-1:]
    assert payload_context.token is not None
    assert (
        tokens.verify_context(
            payload_context.token,
            "h",
            sealed.conversation.conversation_id,
            len(sealed.conversation.visual_contexts) - 1,
            conversation_mapper.context_semantics(payload_context),
        )
        == "ok"
    )

    unsealed = llm_mapper.question_to_dto(charted_result)
    (plain_context,) = unsealed.conversation.visual_contexts[-1:]
    assert plain_context.token is None

    remembered = llm_mapper._remember_visual_context(charted_result, chart_response)
    assert remembered.conversation.visual_contexts[-1].trusted is True


def test_chart_suggestions_offers_nothing_for_an_untrusted_context() -> None:
    """A chip offered over a context nobody can vouch for is a button that will answer
    `VISUAL_UNTRUSTED` the moment it is tapped -- `_chart_suggestions` says so up front rather than
    relying on a restyle's own refusal to happen to produce the same empty list."""
    untrusted = replace(
        VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE), trusted=False, trust_reason="content"
    )
    assert llm_mapper._chart_suggestions(untrusted) == []


def test_to_domain_is_called_only_where_a_caller_can_verify_it() -> None:
    """`to_domain` builds `VisualContext.trusted` from its `verify` callable, so a call with no
    `verify` trusts whatever the caller echoed with no check at all -- `to_domain`'s own default.

    The graph owns the conversation now and reads none back off the wire, so `src/` has no call
    site left. A new one -- verified or not -- fails this test until someone decides which of the
    two shapes it is, the way `test_otel.py`'s own import sweep pins where `opentelemetry` may be
    imported from.
    """
    import ast
    from pathlib import Path

    source_root = Path(__file__).parents[1] / "src" / "finguard_orchestrator"

    class _Visitor(ast.NodeVisitor):
        def __init__(self, relative: str) -> None:
            self.relative = relative
            self.stack: list[str] = []
            self.calls: list[tuple[str, int, str, bool]] = []

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._function(node)

        def visit_Call(self, node: ast.Call) -> None:
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else None
            )
            if name == "to_domain":
                enclosing = self.stack[-1] if self.stack else "<module>"
                verified = any(keyword.arg == "verify" for keyword in node.keywords)
                self.calls.append((self.relative, node.lineno, enclosing, verified))
            self.generic_visit(node)

    calls: list[tuple[str, int, str, bool]] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        visitor = _Visitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=relative))
        calls.extend(visitor.calls)

    assert calls == [], f"a to_domain call site appeared where none is accounted for: {calls}"


def test_the_seal_is_never_silently_skipped() -> None:
    """Fix round 2 (I1): the verify side has an allowlist (the test above); the seal side did not,
    and that is exactly where C1 shipped -- a compound turn called `question_to_dto(result)` with no
    `holder_reference`/`metric_tokens`, so its own charts were born unsealed and refused their own
    restyle one turn later. This is the seal-side mirror: every production call of
    `question_to_dto(` passes both `holder_reference=` and `metric_tokens=`, with no exception.
    """
    import ast
    from pathlib import Path

    source_root = Path(__file__).parents[1] / "src" / "finguard_orchestrator"

    class _Visitor(ast.NodeVisitor):
        def __init__(self, relative: str) -> None:
            self.relative = relative
            self.stack: list[str] = []
            self.question_to_dto_calls: list[tuple[str, str, frozenset[str]]] = []

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._function(node)

        def visit_Call(self, node: ast.Call) -> None:
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else None
            )
            enclosing = self.stack[-1] if self.stack else "<module>"
            # m5 (fix wave): a keyword bound to a literal `None` does not count as passed --
            # `question_to_dto`'s own guard (`if holder_reference is not None and metric_tokens is
            # not None:`) already treats it as absent, so `metric_tokens=None` written at the call
            # site must fail this sweep the same way omitting the keyword entirely would.
            keywords = frozenset(
                keyword.arg
                for keyword in node.keywords
                if keyword.arg
                and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
            )
            if name == "question_to_dto":
                self.question_to_dto_calls.append((self.relative, enclosing, keywords))
            self.generic_visit(node)

    # m5: pinned directly against the visitor, since no live call site writes this shape --
    # `metric_tokens=None` (a literal), not the `metric_tokens if source is not None else None`
    # conditional D-3b-6 just removed. A keyword present only by name and bound to `None` must
    # still read as unsealed.
    synthetic = _Visitor("synthetic.py")
    synthetic.visit(
        ast.parse("def f():\n    question_to_dto(r, holder_reference=h, metric_tokens=None)\n")
    )
    (_, _, synthetic_keywords) = synthetic.question_to_dto_calls[0]
    assert "metric_tokens" not in synthetic_keywords, "a literal metric_tokens=None must not seal"

    question_to_dto_calls: list[tuple[str, str, frozenset[str]]] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        visitor = _Visitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=relative))
        question_to_dto_calls.extend(visitor.question_to_dto_calls)

    assert question_to_dto_calls, "no question_to_dto( call sites found -- test rotted"
    unsealed = {
        (relative, enclosing)
        for relative, enclosing, keywords in question_to_dto_calls
        if not {"holder_reference", "metric_tokens"} <= keywords
    }
    assert not unsealed, (
        f"a question_to_dto( call site skips holder_reference=/metric_tokens=: {unsealed}"
    )


def test_trusted_never_crosses_the_wire() -> None:
    """Fix round 2 (I2): `_compound_read_reply`'s own `to_domain(rendered.conversation)` call has
    no `verify=`, so every context it round-trips comes back `trusted=True`
    (`conversation_mapper.py`'s own default), including one that arrived untrusted. That
    relaundering is harmless only because nothing on the wire ever carries `trusted` in the first
    place -- pinned here directly rather than left as an inference from reading
    `_context_payload`.
    """
    assert "trusted" not in VisualContextPayload.model_fields
    context = VisualContext(
        "chart-1", SPEC, BREAKDOWN_PROFILE, trusted=False, trust_reason="content"
    )
    payload = conversation_mapper.to_payload(IDLE.with_visual_context(context))
    (item,) = payload.visual_contexts
    assert "trusted" not in item.model_dump()
