import functools
from dataclasses import replace
from typing import get_args
from uuid import UUID

import pytest
from support.visual_planners import default_policy
from test_visualization_stage1 import FAMILY_DATASETS

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
    VisualDataset,
    VisualRow,
    _decided_visual,
)
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ChartEncoding,
    ChartSpec,
    EncodingPatch,
    EncodingSort,
    VisualContext,
    VisualField,
    Visualization,
    VisualProfile,
    merge_encoding_patch,
)
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.models.visual_plan import Rejection, RejectionRule, draft_of
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)
from finguard_orchestrator.domain.services.visual_interpreter import (
    VisualIntent,
    apply_visual_intent,
    interpret_visual,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.controllers.llm_controller import (
    _REFUSAL_TEXT,
    _derived_context,
    _saved_visual_source,
    _visual_only_response,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    MetricSourcePayload,
    QuestionRequest,
    VisualizationDerivation,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.infrastructure.mappers.conversation_mapper import (
    _encoding_sort_from_payload,
    to_domain,
    to_payload,
)
from finguard_orchestrator.infrastructure.mappers.llm_mapper import (
    _chart_suggestions,
    question_to_dto,
)
from finguard_orchestrator.infrastructure.metrics.codec import decode_chart, encode_chart
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
from finguard_orchestrator.infrastructure.visualization.vega_lite import present, present_spec
from finguard_orchestrator.observability.decisions import Step, Verdict, recording_turn


async def _decided(result: AskFinancialQuestionResult) -> AskFinancialQuestionResult:
    """The Result as the handler hands it to the mapper.

    The chart is no longer decided inside `question_to_dto`; the application decides it and the
    Result carries it. This is the handler's own line, called with the handler's own arguments, so
    a Result built here is the one a live turn produces.
    """
    return await _decided_visual(result, default_policy(), DEFAULT_PRESENTATION, "")


def _base() -> tuple[ChartSpec, VisualProfile]:
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("y", "dimension", "nominal"),
        ),
    )
    profile = VisualProfile(
        fields=(
            # `order="MEASURE_DESC"`: this is a ranked breakdown bar, the shape
            # `visual_profile_of` actually builds for one -- Task 3's verifier reads a
            # dimension's own `order` before it lets a chart-wide sort touch its axis, and
            # a plain default `"NONE"` would refuse the descending-sort restyle several tests
            # below drive over this exact fixture.
            VisualField("dimension", "nominal", "dimension", 3, "MEASURE_DESC"),
            VisualField("value_minor", "quantitative", "measure", 3),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )
    return spec, profile


def test_patch_is_atomic_and_rejects_unknown_fields() -> None:
    spec, profile = _base()
    with pytest.raises(ValueError):
        merge_encoding_patch(spec, EncodingPatch(sort_field="missing", sort="descending"), profile)
    assert spec.mark == "bar"


def test_donut_requires_valid_profile_and_rewrites_channels() -> None:
    spec, profile = _base()
    donut = merge_encoding_patch(spec, EncodingPatch(mark="donut"), profile)
    assert donut.mark == "donut"
    assert {item.channel for item in donut.encodings} == {"theta", "color"}
    invalid = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 1),
            VisualField("value_minor", "quantitative", "measure", 1),
        ),
        metric="SUM",
        unit="money",
    )
    with pytest.raises(ValueError, match="positive, exclusive parts and a verified total"):
        merge_encoding_patch(spec, EncodingPatch(mark="donut"), invalid)
    # `invalid` above never reaches the cardinality bound at all -- it fails the
    # positive/mutually_exclusive/verified_total gate first. This one clears that gate with a
    # single part, so it is the case that actually exercises "between 2 and 5".
    one_part = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 1),
            VisualField("value_minor", "quantitative", "measure", 1),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )
    with pytest.raises(ValueError, match="between 2 and 5"):
        merge_encoding_patch(spec, EncodingPatch(mark="donut"), one_part)


def test_ratio_share_can_become_a_donut_and_return_to_bars() -> None:
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "dimension", "nominal"),
        ),
        stack="normalize",
    )
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 2),
            VisualField("value_minor", "quantitative", "measure", 2),
        ),
        metric="RATIO_SHARE",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )
    donut = merge_encoding_patch(spec, EncodingPatch(mark="donut"), profile)
    assert donut.stack is None
    assert {item.channel for item in donut.encodings} == {"theta", "color"}
    bars = merge_encoding_patch(donut, EncodingPatch(mark="bar"), profile)
    assert {item.channel for item in bars.encodings} == {"x", "y"}


def test_limit_presenter_uses_valid_rank_window_and_measure_sort() -> None:
    spec, profile = _base()
    limited = merge_encoding_patch(spec, EncodingPatch(limit=3), profile)
    rendered = present(Visualization(limited, ({"dimension": "a", "value_minor": 1},)))
    assert rendered["data"] == [{"dimension": "a", "value_minor": 1}]
    assert rendered["spec"]["transform"][0]["sort"] == [
        {"field": "value_minor", "order": "descending"}
    ]
    assert rendered["spec"]["transform"][1] == {"filter": "datum._rank <= 3"}


def test_temporal_bar_sort_is_presented_as_discrete_and_keeps_measure_sort() -> None:
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "dimension", "temporal", "Periodo"),
            ChartEncoding("y", "value_minor", "quantitative", "Importe"),
        ),
        sort="descending",
        sort_field="value_minor",
    )
    rendered = present_spec(spec)
    assert rendered["encoding"]["x"]["type"] == "nominal"
    assert rendered["encoding"]["x"]["sort"] == {
        "field": "value_minor",
        "order": "descending",
    }


def test_visual_derivation_dto_rejects_ids_reuse_and_embedded_rows() -> None:
    structural = {
        "mark": {"type": "bar"},
        "encoding": {"x": {"field": "dimension", "type": "nominal"}},
    }
    with pytest.raises(ValueError):
        VisualizationDerivation(source_chart_id="chart-a", chart_id="chart-a", spec=structural)
    with pytest.raises(ValueError):
        VisualizationDerivation(
            source_chart_id="chart-a",
            chart_id="chart-b",
            spec={**structural, "data": {"values": [{"amount": 10}]}},
        )


def test_temporal_chart_supports_a_bar_follow_up_but_refuses_a_value_sort() -> None:
    """A temporal chart survives a mark-only follow-up (line to bar); a second follow-up asking to
    reorder that bar by value is now refused rather than silently allowed.

    Before Task 3 wired the verifier into this path, `merge_encoding_patch` had no opinion on
    ordering at all, so this chain used to end in `VISUAL_UPDATED` with the months re-ranked by
    amount. `visual_profile_of` (`visual_profile.py::_dimension_order`) never gives a temporal
    dimension anything but `order="TEMPORAL"` -- production never builds the profile this test's
    second half used to rely on -- and the verifier's own `order` rule enforces exactly that: an
    axis whose field is time-ordered keeps its own order whatever mark is currently drawing it.
    """
    spec = ChartSpec(
        "line",
        (
            ChartEncoding("x", "dimension", "temporal", "Periodo"),
            ChartEncoding("y", "value_minor", "quantitative", "Importe"),
        ),
    )
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "temporal", "dimension", 13, "TEMPORAL"),
            VisualField("value_minor", "quantitative", "measure", 13),
        ),
        metric="SUM",
        unit="money",
    )
    conversation = Conversation(visual_contexts=(VisualContext("chart-months", spec, profile),))

    bars = interpret_visual("ponlo en barras", conversation.visual_contexts)
    assert bars is not None
    bars_response = _visual_only_response(bars, conversation)
    assert bars_response.outcome == "VISUAL_UPDATED"
    assert bars_response.conversation is not None
    assert bars_response.visualization_derivation is not None
    chart_b = bars_response.visualization_derivation.chart_id
    assert bars_response.visualization_derivation.source_chart_id == "chart-months"
    assert chart_b != "chart-months"
    # M3: the value-free sweep this test always ran, restored on the step that still succeeds --
    # the mark-only follow-up never touches the axis's own type, so `x` stays temporal here even
    # though the (now refused) sort follow-up used to turn it into a ranked, nominal-looking axis.
    bars_encoding = bars_response.visualization_derivation.spec["encoding"]
    assert bars_encoding["x"]["type"] == "temporal"
    bars_wire = bars_response.model_dump(mode="json")
    assert "data" not in repr(bars_wire["visualization_derivation"])
    assert "values" not in repr(bars_wire["visualization_derivation"])

    with_bars = to_domain(bars_response.conversation)
    assert [item.chart_id for item in with_bars.visual_contexts] == ["chart-months", chart_b]
    assert with_bars.visual_contexts[0].spec.mark == "line"
    assert with_bars.visual_contexts[1].spec.mark == "bar"
    ordered = interpret_visual("ahora ordénalo de mayor a menor", with_bars.visual_contexts)
    assert ordered is not None
    ordered_response = _visual_only_response(ordered, with_bars)
    assert ordered_response.outcome == "VISUAL_INCOMPATIBLE"
    # I1 (fix wave): the Spanish clause for `order`, not the rule token -- the token now lives
    # only in the record's `detail`.
    assert "este eje no admite ese orden" in ordered_response.text
    assert ordered_response.visualization_derivation is None
    # Refused: the previous chart stays, nothing derived.
    with_order = to_domain(ordered_response.conversation)
    assert [item.chart_id for item in with_order.visual_contexts] == ["chart-months", chart_b]
    assert with_order.visual_contexts[1].spec.sort is None


def test_context_ids_and_channels_are_bounded() -> None:
    spec, profile = _base()
    with pytest.raises(ValueError):
        VisualContext("not valid!", spec, profile)
    with pytest.raises(ValueError):
        ChartSpec("bar", (ChartEncoding("x", "a", "nominal"), ChartEncoding("x", "b", "nominal")))


def test_conversation_visual_context_round_trip_is_value_free() -> None:
    spec, profile = _base()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    payload = to_payload(conversation)
    assert "value_minor" in repr(payload)  # field names are allowed
    assert "data" not in repr(payload)
    restored = to_domain(payload)
    assert restored.visual_contexts[0].chart_id == "chart-1"
    assert restored.visual_contexts[0].spec.fields() == spec.fields()
    # The old-client case: `_base()`'s spec carries neither `stack` nor `sort`, so the wire
    # payload never has those keys either -- and `to_domain` must still complete the turn rather
    # than require them.
    assert restored.visual_contexts[0].spec.stack is None
    assert restored.visual_contexts[0].spec.sort is None


async def test_chart_response_emits_id_and_structural_context_without_rows() -> None:
    result = await _decided(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="Resultado",
            question="gastos por categoría",
            plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
            groups=[SpokenGroup("Comida", 100, 1), SpokenGroup("Casa", 50, 1)],
            visual_dataset=VisualDataset((VisualRow("Comida", 100), VisualRow("Casa", 50)), "SUM"),
        )
    )
    response = question_to_dto(result)
    assert response.visualization is not None
    assert response.visualization.kind == "chart"
    assert response.visualization.unit == "money"  # type: ignore[union-attr]
    assert response.visualization.chart_id.startswith("chart-")  # type: ignore[union-attr]
    assert result.conversation.visual_contexts == ()  # result remains immutable
    assert response.conversation is not None
    assert len(response.conversation.visual_contexts) == 1


def test_visual_interpreter_is_value_free_and_skips_financial_changes() -> None:
    spec, profile = _base()
    context = VisualContext("chart-1", spec, profile)
    intent = interpret_visual("ponlo en barras y top 3", (context,))
    assert intent is not None and intent.patch is not None
    assert intent.patch.limit == 3
    ordered = interpret_visual("ordénalo", (context,))
    assert ordered is not None and ordered.patch is not None
    assert ordered.patch.sort == "descending"
    assert interpret_visual("por semanas", (context,)) is None
    assert interpret_visual("top 2 categorías por ticket medio", (context,)) is None
    voice = interpret_visual("ponlo en línea", (context,), voice=True)
    assert voice is not None and voice.explanation is not None
    mixed = interpret_visual("por semanas y ponlo en barras", (context,))
    assert mixed is not None and mixed.patch is None and mixed.explanation is not None


async def test_identical_chart_answers_receive_distinct_ids() -> None:
    result = await _decided(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="x",
            question="gastos",
            plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
            visual_dataset=VisualDataset((VisualRow("a", 100), VisualRow("b", 50)), "SUM"),
        )
    )
    first = question_to_dto(result).visualization
    second = question_to_dto(result).visualization
    assert first is not None and second is not None
    assert first.kind == "chart" and second.kind == "chart"
    assert first.chart_id != second.chart_id


async def test_chart_unit_declares_count_and_ratio_without_changing_minor_rows() -> None:
    count = question_to_dto(
        await _decided(
            AskFinancialQuestionResult(
                outcome="PLANNED",
                text="x",
                visual_dataset=VisualDataset(
                    (VisualRow("lunes", 12), VisualRow("martes", 5)), "FREQUENCY"
                ),
            )
        )
    ).visualization
    ratio = question_to_dto(
        await _decided(
            AskFinancialQuestionResult(
                outcome="PLANNED",
                text="x",
                visual_dataset=VisualDataset(
                    (VisualRow("Parte", 1200), VisualRow("Resto", 8800)), "RATIO_SHARE"
                ),
            )
        )
    ).visualization
    assert count is not None and count.unit == "count" and count.data[0]["value_minor"] == 12  # type: ignore[union-attr]
    assert ratio is not None and ratio.unit == "ratio" and ratio.data[0]["value_minor"] == 1200  # type: ignore[union-attr]


async def test_a_count_comparison_uses_the_profile_unit_end_to_end() -> None:
    result = await _decided(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="x",
            plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="COUNT"),
            visual_dataset=VisualDataset(
                (
                    VisualRow("Comida", 12, series="julio"),
                    VisualRow("Comida", 8, series="junio"),
                    VisualRow("Casa", 5, series="julio"),
                    VisualRow("Casa", 4, series="junio"),
                ),
                "COMPARISON",
            ),
        )
    )

    assert result.visual_decision is not None
    assert result.visual_decision.profile.unit == "count"
    response = question_to_dto(result)
    assert response.visualization is not None
    assert response.visualization.unit == "count"


async def test_breakdown_total_verification_requires_complete_total() -> None:
    async def mapped(total: int | None, rows: tuple[VisualRow, ...]):
        return question_to_dto(
            await _decided(
                AskFinancialQuestionResult(
                    outcome="PLANNED",
                    text="x",
                    total_minor=total,
                    plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
                    visual_dataset=VisualDataset(rows, "SUM"),
                )
            )
        ).conversation

    complete = await mapped(150, (VisualRow("a", 100), VisualRow("b", 50)))
    absent = await mapped(None, (VisualRow("a", 100), VisualRow("b", 50)))
    truncated = await mapped(150, (VisualRow("a", 100), VisualRow("b", 20)))
    ratio = question_to_dto(
        await _decided(
            AskFinancialQuestionResult(
                outcome="PLANNED",
                text="x",
                visual_dataset=VisualDataset(
                    (VisualRow("Parte", 100), VisualRow("Resto", 50)), "RATIO_SHARE"
                ),
            )
        )
    ).conversation
    assert complete is not None and complete.visual_contexts[0].profile.verified_total
    assert absent is not None and not absent.visual_contexts[0].profile.verified_total
    assert truncated is not None and not truncated.visual_contexts[0].profile.verified_total
    assert ratio is not None and ratio.visual_contexts[0].profile.verified_total


def test_controller_visual_response_updates_context_and_returns_presented_spec() -> None:
    spec, profile = _base()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ordénalo descendente", conversation.visual_contexts)
    assert intent is not None
    response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_UPDATED"
    assert response.visualization_derivation is not None
    assert response.visualization_derivation.source_chart_id == "chart-1"
    assert response.visualization_derivation.chart_id != "chart-1"
    assert response.visualization_derivation.spec["$schema"]
    assert response.visualization_derivation.spec["encoding"]
    assert response.conversation is not None


def test_visual_update_rotates_the_save_candidate_with_the_new_spec() -> None:
    """`_base()`'s dimension is nominal, not temporal, so a `line`/`area` mark -- Section D
    requires a temporal axis for either -- would now be refused by the verifier before ever
    reaching the save candidate. `_base()`'s profile is already `positive`,
    `mutually_exclusive` and `verified_total` with three parts, exactly what a donut needs, so
    "ponlo en donut" is the mark change that actually survives verification on this fixture.
    """
    spec, profile = _base()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ponlo en donut", conversation.visual_contexts)
    assert intent is not None
    definition = SavedMetricDefinition(
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(Period.THIS_MONTH),
        ),
        spec,
    )
    tokens = MetricTokenCodec({"active": b"x" * 32}, "active")

    response = _visual_only_response(
        intent,
        conversation,
        source=definition,
        holder_reference="holder",
        metric_tokens=tokens,
    )

    assert response.save_candidate is not None
    assert response.visualization_derivation is not None
    assert response.save_candidate.chart_id == response.visualization_derivation.chart_id
    assert response.save_candidate.chart_id != "chart-1"
    updated = tokens.verify(response.save_candidate.definition_token, "holder")
    assert updated.chart.mark == "donut"
    assert updated.plan == definition.plan


def _unverified_breakdown() -> tuple[ChartSpec, VisualProfile]:
    """A breakdown -- positive, mutually exclusive parts -- whose total nobody has verified. The
    one shape `_donut_claims` exists to refuse: a donut, or the normalised bar that claims the
    same thing drawn as a single 100% bar instead of a circle."""
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "dimension", "nominal"),
        ),
    )
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 3),
            VisualField("value_minor", "quantitative", "measure", 3),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=False,
    )
    return spec, profile


def test_every_rejection_rule_has_a_spanish_clause() -> None:
    """I1: a rule the closed set gains and `_REFUSAL_TEXT` does not would `KeyError` at the one
    moment a holder is waiting for an answer. `set` rather than `==` on a dict/tuple would still
    let the two drift in size; this is the coverage that cannot."""
    assert set(_REFUSAL_TEXT) == set(get_args(RejectionRule))


def test_a_patch_that_the_verifier_refuses_keeps_the_previous_chart_and_names_the_rule() -> None:
    """A donut asked of a breakdown whose total is not verified: merge_encoding_patch may accept
    the shape, the verifier refuses the claim (rule donut_claims). The previous chart stays; the
    response says why; one VISUAL_PLANNER record says DECLINED with the rule.

    Driven with "apílalo en porcentaje" rather than the literal word "donut": `merge_encoding_patch`
    carries its own, identical positive/mutually_exclusive/verified_total gate for `mark == "donut"`
    (`chart.py`, predating this task), which raises before the merged spec ever reaches the new
    verifier step. A normalised bar -- `stack="normalize"`, the same claim drawn as a single 100%
    bar instead of a circle -- has no such gate in `merge_encoding_patch`, so it is the shape that
    actually exercises the verifier's own `_donut_claims` refusal on this path.
    """
    spec, profile = _unverified_breakdown()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("apílalo en porcentaje", conversation.visual_contexts)
    assert intent is not None
    with recording_turn() as turn:
        response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_INCOMPATIBLE"
    # I1 (fix wave): the sentence is Spanish and speaks a clause per rule, not the rule as a
    # token -- the token stays in the record's `detail` below, never in the text the holder
    # reads, and the rule's English `reason` ("...verified whole") never reaches it either.
    assert "un donut necesita partes de un total verificado" in response.text
    assert "donut_claims" not in response.text
    assert "verified whole" not in response.text
    assert response.conversation is not None
    assert response.conversation.visual_contexts[-1].chart_id == "chart-1"  # nothing derived
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.DECLINED
    assert decision.detail == "invalid_proposal:donut_claims"


def test_a_merge_level_refusal_is_recorded_too() -> None:
    """I3: the older refusal path -- `apply_visual_intent` raising before a merged spec ever
    reaches `verify` -- used to record nothing and name no rule, so the per-rule rejection rate
    `record_visual_decision_refused`'s own docstring promises ("patch decisions included") was
    incomplete for the share of refusals that take this path. A literal "donut" ask (unlike
    "apílalo en porcentaje" above) trips `merge_encoding_patch`'s own positive/mutually_exclusive/
    verified_total gate directly, before the verifier's `_donut_claims` rule ever runs.

    Now recorded exactly like a verifier refusal: one DECLINED row naming rule `merge`, the
    Spanish `merge` clause in the text (the exception's own English -- "positive, exclusive
    parts" -- never reaches the holder), the previous chart kept and nothing derived.
    """
    spec, profile = _unverified_breakdown()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ponlo en donut", conversation.visual_contexts)
    assert intent is not None
    with recording_turn() as turn:
        response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_INCOMPATIBLE"
    assert "el cambio pedido no encaja con los campos del gráfico" in response.text
    assert "positive, exclusive parts" not in response.text
    assert response.conversation is not None
    assert response.conversation.visual_contexts[-1].chart_id == "chart-1"  # nothing derived
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.DECLINED
    assert decision.detail == "invalid_proposal:merge"


def _floor_breakdown() -> tuple[ChartSpec, VisualProfile]:
    """The floor's own breakdown bar -- dimension on `y`, ranked descending by the measure on its
    own axis (`EncodingSort`, not the chart-wide `sort` `_breakdown_bar_ranked` above stands in
    for). Task 3's orientation swap only moves a `ChartEncoding`'s `channel`, so this is the
    fixture that lets the merged chart still carry that per-axis ranking after it turns -- the
    handcrafted `_breakdown_bar_ranked` has no such `EncodingSort` to carry across and would fail
    the verifier's `order` rule the moment the turn is the only thing asked for.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    return spec, profile


def test_a_bar_turns_vertical_when_asked() -> None:
    """Task 3: the floor's breakdown bar has its dimension on `y` (horizontal bars). «pon el
    gráfico anterior en vertical» moves the dimension to `x` and the measure to `y` -- the
    presented spec differs from what was on screen, and the merged chart still verifies (its
    axis's own `EncodingSort` rides along with the channel swap)."""
    spec, profile = _floor_breakdown()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("pon el gráfico anterior en vertical", conversation.visual_contexts)
    assert intent is not None
    response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_UPDATED"
    assert response.visualization_derivation is not None
    presented_encoding = response.visualization_derivation.spec["encoding"]
    assert presented_encoding["x"]["field"] == "dimension"
    assert presented_encoding["y"]["field"] == "value_minor"
    derived = to_domain(response.conversation).visual_contexts[-1]
    assert {(item.channel, item.field) for item in derived.spec.encodings} >= {
        ("x", "dimension"),
        ("y", "value_minor"),
    }


def test_turning_a_bar_already_that_way_answers_visual_unchanged() -> None:
    """The same breakdown bar is already horizontal (dimension on `y`); «ponlo en horizontal»
    asks for the orientation it already has -- a no-op the merge answers by returning the spec
    unchanged, which the controller's own no-op defence (`canonical(updated.spec) ==
    canonical(context.spec)`) then answers as `VISUAL_UNCHANGED`, minting nothing."""
    spec, profile = _floor_breakdown()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ponlo en horizontal", conversation.visual_contexts)
    assert intent is not None
    response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_UNCHANGED"
    assert response.visualization_derivation is None


def test_a_grouped_comparison_bar_turns_and_its_offset_swaps_too() -> None:
    """Task 3's own family member: the floor's grouped comparison bar carries its series on
    `yOffset` (alongside `color`) as well as its dimension on `y`. Turning it swaps both --
    `yOffset` becomes `xOffset`, not just the axis pair -- and the merged chart still verifies:
    the same eight-family shape `test_the_floors_own_chart_verifies_as_itself`
    (`test_visual_plan_verifier.py`) checks, turned.
    """
    dataset = VisualDataset(
        metric="COMPARISON",
        rows=(
            VisualRow("ALQUILER", 100, series="2026-07"),
            VisualRow("ALQUILER", 90, series="2026-06"),
            VisualRow("OCIO", 50, series="2026-07"),
            VisualRow("OCIO", 70, series="2026-06"),
        ),
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    assert any(item.channel == "yOffset" for item in spec.encodings)  # the shape this test is about
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ponlo en vertical", conversation.visual_contexts)
    assert intent is not None
    response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_UPDATED"
    derived = to_domain(response.conversation).visual_contexts[-1]
    assert any(item.channel == "xOffset" for item in derived.spec.encodings)
    assert not any(item.channel == "yOffset" for item in derived.spec.encodings)


def test_asking_a_line_to_turn_is_a_merge_refusal() -> None:
    """Refuse, never repair: orientation applies to bars only. A line asked to turn trips
    `merge_encoding_patch`'s own gate directly, before the verifier ever runs -- recorded exactly
    like the donut gate above, one DECLINED row naming rule `merge`.
    """
    spec = ChartSpec(
        "line",
        (
            ChartEncoding("x", "dimension", "temporal", "Periodo"),
            ChartEncoding("y", "value_minor", "quantitative", "Importe"),
        ),
    )
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "temporal", "dimension", 13, "TEMPORAL"),
            VisualField("value_minor", "quantitative", "measure", 13),
        ),
        metric="SUM",
        unit="money",
    )
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ponlo en vertical", conversation.visual_contexts)
    assert intent is not None
    with recording_turn() as turn:
        response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_INCOMPATIBLE"
    assert "el cambio pedido no encaja con los campos del gráfico" in response.text
    assert "orientation applies to bars" not in response.text
    assert response.conversation is not None
    assert response.conversation.visual_contexts[-1].chart_id == "chart-1"  # nothing derived
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.DECLINED
    assert decision.detail == "invalid_proposal:merge"


def _breakdown_bar_ranked() -> tuple[ChartSpec, VisualProfile]:
    """A breakdown bar whose dimension already ranks by magnitude -- `order="MEASURE_DESC"` -- so
    a chart-wide descending sort by the measure is a claim the verifier's `_order` rule accepts."""
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("y", "dimension", "nominal"),
        ),
    )
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 3, "MEASURE_DESC"),
            VisualField("value_minor", "quantitative", "measure", 3),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )
    return spec, profile


def test_a_restyle_that_changes_nothing_answers_visual_unchanged_and_mints_no_version() -> None:
    """F2 (P0, fix wave 2, the review's own second half of F1): once the merge correctly edits
    the axis that already owns the order (F1), «ordena de mayor a menor» over a chart already
    descending on that axis is a genuine no-op -- and a no-op must not mint a version, a fresh
    `chart_id` or a `patch` decision nobody asked for. The conversation comes back byte-identical
    to what was handed in and no `VISUAL_PLANNER` row is recorded: the `VISUAL_FOLLOW_UP` row
    recorded upstream is this turn's whole audit trail.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ordena de mayor a menor", conversation.visual_contexts)
    assert intent is not None and intent.patch is not None
    with recording_turn() as turn:
        response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_UNCHANGED"
    assert response.text == "El gráfico ya está así; lo dejo como está."
    assert response.conversation == to_payload(conversation)
    assert response.visualization_derivation is None
    assert response.save_candidate is None
    assert turn.sealed().where(Step.VISUAL_PLANNER) == ()


def test_visual_unchanged_is_judged_against_the_chart_on_screen_not_the_saved_metric() -> None:
    """N1 (round 1, re-review): F2's equality used to compare `updated.spec` against
    `trusted_context.spec`, which is the *saved metric's* chart whenever a verified
    `SavedMetricDefinition` targets this restyle (`source=`) -- not the chart on screen
    (`context.spec`). A restyle that is a no-op relative to the saved chart but a real change
    relative to what is actually drawn used to answer "ya está así" and silently drop the save
    candidate the same request would otherwise have minted. The comparison is now against
    `context.spec`, which is also what `trusted_context` collapses to whenever there is no saved
    source (`replace(context, spec=source.chart)` only runs when one is given) -- so the plain
    no-op path is unaffected by this fix, checked below alongside the saved-metric one.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    on_screen = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert on_screen is not None
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", on_screen, profile),))
    intent = interpret_visual("ordena de menor a mayor", conversation.visual_contexts)
    assert intent is not None and intent.patch is not None

    # The saved chart already ranks ascending: applying the same patch to it is a no-op. But the
    # chart on screen is still the floor's own descending one, so the merged spec is a genuine
    # change relative to what is actually drawn.
    saved_chart = merge_encoding_patch(
        on_screen, EncodingPatch(sort="ascending", sort_field="value_minor"), profile
    )
    definition = SavedMetricDefinition(
        FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH)),
        saved_chart,
    )
    tokens = MetricTokenCodec({"active": b"x" * 32}, "active")
    response = _visual_only_response(
        intent, conversation, source=definition, holder_reference="holder", metric_tokens=tokens
    )
    assert response.outcome == "VISUAL_UPDATED"
    assert response.save_candidate is not None

    # The plain no-op -- no saved source, so trusted_context is context -- still answers
    # VISUAL_UNCHANGED: this fix narrows what counts as "unchanged", it does not remove it.
    already_ascending = Conversation(
        visual_contexts=(VisualContext("chart-1", saved_chart, profile),)
    )
    same_intent = interpret_visual("ordena de menor a mayor", already_ascending.visual_contexts)
    assert same_intent is not None and same_intent.patch is not None
    unchanged_response = _visual_only_response(same_intent, already_ascending)
    assert unchanged_response.outcome == "VISUAL_UNCHANGED"


# --- Task 3: refused where it is untrusted, sealed where it is derived --------------------------


def test_an_untrusted_context_is_refused_before_any_merge_or_ledger_call() -> None:
    """An echoed context whose seal did not check out (`conversation_mapper.to_domain`'s own
    `verify`, `trusted=False`) is drawn and can still be navigated, but it is not a legitimate
    target for a restyle. `_visual_only_response` refuses it before `_saved_visual_source`'s own
    refusal even matters, before any merge or verify -- the conversation comes back byte-identical
    to what was handed in, and zero ledger calls: the `VISUAL_FOLLOW_UP` row recorded upstream
    (`llm_controller.py`'s own short-circuit) is this turn's whole audit trail, exactly like F2's
    `VISUAL_UNCHANGED` above.
    """
    spec, profile = _base()
    untrusted = replace(VisualContext("chart-1", spec, profile), trusted=False)
    conversation = Conversation(visual_contexts=(untrusted,))
    intent = interpret_visual("ordénalo descendente", conversation.visual_contexts)
    assert intent is not None and intent.patch is not None
    with recording_turn() as turn:
        response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_UNTRUSTED"
    assert response.text == "No reconozco ese gráfico; pide uno nuevo y podré cambiarlo."
    assert response.conversation == to_payload(conversation)
    assert turn.sealed().decisions == ()


def test_a_restyle_seals_its_derived_context_for_the_holder() -> None:
    """Task 3's `seal` flows straight from `_visual_only_response` into `_derived_context`, so a
    restyle's own chart is signed the same turn it is minted -- the holder does not have to ask a
    fresh question just to get a context this process's own verifier will accept back."""
    spec, profile = _base()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ordénalo descendente", conversation.visual_contexts)
    assert intent is not None
    tokens = MetricTokenCodec({"active": b"x" * 32}, "active")
    seal = functools.partial(
        conversation_mapper.seal_context, holder_reference="holder", codec=tokens
    )

    response = _visual_only_response(intent, conversation, seal=seal)

    assert response.outcome == "VISUAL_UPDATED"
    assert response.conversation is not None
    (payload_context,) = response.conversation.visual_contexts[-1:]
    assert payload_context.token is not None
    assert tokens.verify_context(
        payload_context.token, "holder", conversation_mapper.context_semantics(payload_context)
    )


def test_saved_visual_source_refuses_an_untrusted_context_even_with_a_valid_token() -> None:
    """A saved-metric restyle needs both a valid metric token AND a context the caller can still
    vouch for. `_saved_visual_source` used to check only the token; an echoed context whose seal
    did not check out now refuses here too, before the token verification even has a say -- refuse,
    never repair, applied to the save-candidate path and not only to the restyle merge above.
    """
    spec, profile = _base()
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
    definition = SavedMetricDefinition(plan, spec)
    tokens = MetricTokenCodec({"active": b"x" * 32}, "active")
    who = Caller(holder_reference="holder", account_id=UUID(int=0), scopes=frozenset())
    metric_token = tokens.issue(who.holder_reference, definition)
    untrusted = replace(VisualContext("chart-1", spec, profile), trusted=False)
    conversation = Conversation(visual_contexts=(untrusted,))
    intent = VisualIntent(None, "chart-1")
    request = QuestionRequest(
        question="ponlo en donut",
        metric_sources=[MetricSourcePayload(chart_id="chart-1", definition_token=metric_token)],
    )

    result = _saved_visual_source(request, intent, conversation, who, tokens, True)

    assert result is None


def test_saved_visual_source_refuses_a_metric_token_bound_to_a_different_chart() -> None:
    """I3 (fix wave): a metric token now names the chart it was issued for. A token minted for
    plan B's chart, offered as chart A's `metric_sources` entry, is the holder's own token over
    the holder's own chart -- nothing forged -- but pairing it with A would sign a saved metric
    that draws B's plan over a profile nobody verified for it. `_saved_visual_source` refuses it
    the same way an untrusted context does above, before the profile/field check ever runs.
    """
    spec, profile = _base()
    plan_b = FinancialPlan(direction=Direction.INCOME, period=RelativePeriod(Period.THIS_YEAR))
    definition_b = SavedMetricDefinition(plan_b, spec)
    tokens = MetricTokenCodec({"active": b"x" * 32}, "active")
    who = Caller(holder_reference="holder", account_id=UUID(int=0), scopes=frozenset())
    token_for_b = tokens.issue(who.holder_reference, definition_b, chart_id="chart-b")
    conversation = Conversation(visual_contexts=(VisualContext("chart-a", spec, profile),))
    intent = VisualIntent(None, "chart-a")
    request = QuestionRequest(
        question="ponlo en donut",
        metric_sources=[MetricSourcePayload(chart_id="chart-a", definition_token=token_for_b)],
    )

    result = _saved_visual_source(request, intent, conversation, who, tokens, True)

    assert result is None


def test_a_metric_token_bound_to_another_chart_restyles_without_a_save_candidate() -> None:
    """I3, end to end (the reviewer's own probe): before this fix, offering plan B's token as
    chart A's source answered `VISUAL_UPDATED` *and* minted a save candidate pairing plan B with a
    spec merged over chart A's profile nobody verified for B. After it, `source` falls back to
    `None` -- the turn is unaffected, since the intent is a real restyle of chart A's own trusted
    context -- but no candidate is minted: pairing B's plan with A's chart is exactly the
    unverified pairing this fix closes.
    """
    spec, profile = _base()
    plan_b = FinancialPlan(direction=Direction.INCOME, period=RelativePeriod(Period.THIS_YEAR))
    definition_b = SavedMetricDefinition(plan_b, spec)
    tokens = MetricTokenCodec({"active": b"x" * 32}, "active")
    who = Caller(holder_reference="holder", account_id=UUID(int=0), scopes=frozenset())
    token_for_b = tokens.issue(who.holder_reference, definition_b, chart_id="chart-b")
    conversation = Conversation(visual_contexts=(VisualContext("chart-a", spec, profile),))
    intent = interpret_visual("ordénalo descendente", conversation.visual_contexts)
    assert intent is not None
    request = QuestionRequest(
        question="ordénalo descendente",
        metric_sources=[MetricSourcePayload(chart_id="chart-a", definition_token=token_for_b)],
    )

    source = _saved_visual_source(request, intent, conversation, who, tokens, True)
    assert source is None

    response = _visual_only_response(
        intent,
        conversation,
        source=source,
        holder_reference=who.holder_reference,
        metric_tokens=tokens,
    )

    assert response.outcome == "VISUAL_UPDATED"
    assert response.save_candidate is None


def test_the_chips_offered_all_change_the_chart() -> None:
    """F2: `_chart_suggestions` used to offer «Ordena de menor a mayor» by checking only the
    chart-wide `spec.sort` -- which stays `None` once an axis owns its own descending sort
    (F1's own shape, the floor's own shape), so the guard always passed and the chip was offered
    even when applying it changed nothing. The generator now proposes both directions and lets
    the same gate `_visual_only_response` runs -- `interpret_visual` / `apply_visual_intent` /
    `verify`, plus F2's own canonical-equality check -- drop whichever one is a no-op: a
    descending bar offers the ascending chip and not the descending one, and a chart just flipped
    ascending offers the descending chip and not the ascending one.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    descending = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert descending is not None
    descending_context = VisualContext("chart-1", descending, profile)
    descending_suggestions = _chart_suggestions(descending_context)
    assert "Ordena de menor a mayor" in descending_suggestions
    assert "Ordena de mayor a menor" not in descending_suggestions

    ascending_intent = interpret_visual("ordena de menor a mayor", (descending_context,))
    assert ascending_intent is not None and ascending_intent.patch is not None
    ascending = apply_visual_intent(ascending_intent, descending_context)
    ascending_context = VisualContext("chart-2", ascending.spec, profile)
    ascending_suggestions = _chart_suggestions(ascending_context)
    assert "Ordena de mayor a menor" in ascending_suggestions
    assert "Ordena de menor a mayor" not in ascending_suggestions


async def test_a_verified_patch_is_recorded_as_a_patch_decision() -> None:
    """«ordénalo de mayor a menor» over a breakdown bar: verifies; VISUAL_UPDATED as before -- and
    the turn record and the derived context both learn a `patch`, not a fresh proposal, decided it.
    """
    spec, profile = _breakdown_bar_ranked()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ordénalo de mayor a menor", conversation.visual_contexts)
    assert intent is not None
    with recording_turn() as turn:
        response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_UPDATED"
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert (decision.verdict, decision.now, decision.detail) == (
        Verdict.AGREED,
        "patch",
        "accepted",
    )
    derived = to_domain(response.conversation).visual_contexts[-1]
    assert derived.derived_from == "chart-1"
    # decision is turn-local: after the wire it is None -- assert the in-process helper instead:
    trusted = VisualContext("chart-1", spec, profile)
    updated_spec = merge_encoding_patch(
        spec, EncodingPatch(sort="descending", sort_field="value_minor"), profile
    )
    ctx = _derived_context(
        trusted,
        updated_spec,
        "chart-2",
        decision=VisualDecision(
            spec=updated_spec, profile=profile, source="patch", accepted=True, valid=True
        ),
    )
    assert ctx.decision is not None and ctx.decision.source == "patch"


def test_a_round_tripped_ranked_bar_still_verifies_the_descending_sort() -> None:
    """Fix round 1's own pin: before the wire carried a field's `role`/`order`, this exact
    restyle -- a ranked breakdown bar, echoed back once, then asked to sort descending by the
    measure it is already ranked by -- was wrongly refused as `VISUAL_INCOMPATIBLE` (rule
    `order`), because `conversation_mapper.to_domain` reset the dimension's `order` to `"NONE"`
    and the verifier's `order` rule reads `NONE` as "this field has no ranking convention, nothing
    may sort it". The wire now carries `order`, so the same restyle verifies and is recorded as a
    `patch`, exactly as it would have been in a single turn that never crossed the wire at all.
    """
    spec, profile = _breakdown_bar_ranked()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    from_wire = to_domain(to_payload(conversation))
    intent = interpret_visual("ordénalo de mayor a menor", from_wire.visual_contexts)
    assert intent is not None
    with recording_turn() as turn:
        response = _visual_only_response(intent, from_wire)
    assert response.outcome == "VISUAL_UPDATED"
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert (decision.verdict, decision.now, decision.detail) == (
        Verdict.AGREED,
        "patch",
        "accepted",
    )
    # I2 (fix wave): `decision` is turn-local, so `from_wire`'s context carries none -- `was` is
    # unrecorded (`None`), never the fabricated "deterministic" default, and the row still knows
    # it changed something: a patch that replaced an unknown prior source always did.
    assert decision.was is None
    assert decision.changed is True


def test_an_accepted_restyle_shows_the_merged_chart_with_its_titles() -> None:
    """C1 + M1 (fix round 2): `verify` is a gate, not a rewrite. `draft_of`/`_structure` carry no
    titles, so the `ChartSpec` `verify` returns on acceptance is the merged chart minus its chart
    title and every encoding title. What is shown, stored in the derived context, and offered for
    saving must stay `updated.spec` -- the merged chart, titles included -- never that title-less
    gate value.

    Built the way the floor actually builds a breakdown chart: `visual_profile_of` +
    `DeterministicVisualPlanner`, which sets the per-encoding titles ("Grupo"/"Importe") itself,
    plus an explicit chart-level `title` -- the one thing the floor leaves to the mapper
    (`llm_mapper.py` sets it after planning) and which this test therefore sets by hand.

    The floor's own dimension axis already ranks descending (F1's own shape), so this asks for
    the *other* direction -- «de menor a mayor» -- rather than «de mayor a menor»: after F2, the
    latter is a genuine no-op over this exact fixture and answers `VISUAL_UNCHANGED`, which is
    not what this test is about.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    floor_spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert floor_spec is not None
    spec = replace(floor_spec, title="Gastos por categoria")
    source_titles = {item.channel: item.title for item in spec.encodings}
    assert spec.title == "Gastos por categoria"
    assert any(title is not None for title in source_titles.values())
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ordénalo de menor a mayor", conversation.visual_contexts)
    assert intent is not None
    response = _visual_only_response(intent, conversation)
    assert response.outcome == "VISUAL_UPDATED"
    derived = to_domain(response.conversation).visual_contexts[-1]
    assert derived.spec.title == spec.title
    assert {item.channel: item.title for item in derived.spec.encodings} == source_titles
    assert response.visualization_derivation is not None
    presented_encoding = response.visualization_derivation.spec["encoding"]
    for channel, title in source_titles.items():
        if title is not None:
            assert presented_encoding[channel]["title"] == title  # type: ignore[index]


def test_the_saved_metric_source_keeps_the_original_provenance() -> None:
    """D-4.1-1: the rebuild is replace(context, spec=source.chart), not a three-positional rebuild.

    A three-positional `VisualContext(context.chart_id, source.chart, context.profile)` drops
    `context`'s own `decision` -- the fact this chart's context already carries a `VisualDecision`
    (`source="llm"`, here) -- and `record_visual_decision`'s `was` would then read the rebuilt
    context's `decision` as `None` and report "deterministic", even though the chart it replaced
    was the model's. `replace(context, spec=source.chart)` carries the decision across, so the
    `patch` row this restyle records says what it actually replaced.
    """
    spec, profile = _breakdown_bar_ranked()
    original_decision = VisualDecision(
        spec=spec, profile=profile, source="llm", accepted=True, valid=True
    )
    context = VisualContext("chart-1", spec, profile, decision=original_decision)
    conversation = Conversation(visual_contexts=(context,))
    intent = interpret_visual("ordénalo de mayor a menor", conversation.visual_contexts)
    assert intent is not None
    definition = SavedMetricDefinition(
        FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH)),
        spec,
    )
    with recording_turn() as turn:
        response = _visual_only_response(intent, conversation, source=definition)
    assert response.outcome == "VISUAL_UPDATED"
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.was == "llm"


def _limited() -> tuple[ChartSpec, VisualProfile]:
    spec, profile = _base()
    return replace(spec, sort="descending", limit=8), profile


def test_a_limit_the_conversation_imposes_can_also_be_lifted() -> None:
    """`EncodingPatch.clear` existed for this and nothing produced it.

    The chart cuts a wide breakdown to the rows that fit, so until now that was a limit the
    conversation could impose and never undo: every patch the interpreter built set a property, and
    none cleared one.
    """
    spec, profile = _limited()
    contexts = (VisualContext("chart-1", spec, profile),)

    intent = interpret_visual("Ver todos", contexts)
    assert intent is not None and intent.patch is not None
    assert intent.patch.changed == frozenset({"limit"})
    assert intent.target == "chart-1"
    assert merge_encoding_patch(spec, intent.patch, profile).limit is None

    for phrasing in ("ver todos", "todos", "sin límite", "muéstrame todas"):
        lifted = interpret_visual(phrasing, contexts)
        assert lifted is not None and lifted.patch is not None, phrasing


def test_asking_for_everything_is_only_a_restyle_when_that_is_all_it_says() -> None:
    """ "Todos" is a word financial questions are full of, so the pattern is anchored.

    A sentence that asks for every movement is a query. Reading it as a restyle would answer a
    question about the ledger by redrawing the last chart, which is the worst kind of wrong: it
    looks like it worked.
    """
    spec, profile = _limited()
    contexts = (VisualContext("chart-1", spec, profile),)
    for question in (
        "enséñame todos mis movimientos",
        "todos mis gastos de agosto",
        "quiero ver todos los movimientos de la cuenta",
    ):
        assert interpret_visual(question, contexts) is None, question


def test_the_chart_offers_the_follow_ups_it_can_actually_honour() -> None:
    """Every chip round-trips through the interpreter that has to read it back, and now verifies
    too (C1): `_chart_suggestions` takes the context and filters each candidate through the same
    `interpret_visual` / `apply_visual_intent` / `verify(draft_of(...), ...)` gate
    `_visual_only_response` runs, so a chip offered here is a chip the controller will accept.

    A ranked bar carries its rank on the axis encoding's own sort (`EncodingSort`, not the
    chart-wide one `_base()`/`_limited()` use) -- the shape the floor actually builds for a
    MEASURE_DESC dimension -- which is why this fixture is built by hand rather than reused from
    `_limited()`: only an axis-own sort survives a chart-wide "ascending" patch, per `_order`'s
    own per-encoding-before-chart-wide reading.
    """
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding(
                "y", "dimension", "nominal", sort=EncodingSort("descending", "value_minor")
            ),
            ChartEncoding("x", "value_minor", "quantitative"),
        ),
        limit=8,
    )
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 3, "MEASURE_DESC"),
            VisualField("value_minor", "quantitative", "measure", 3),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )
    context = VisualContext("chart-1", spec, profile)
    suggestions = _chart_suggestions(context)
    assert "Ver todos" in suggestions
    # A line over unordered categories is exactly what the `mark` rule exists to refuse (C1).
    assert "Ponlo en línea" not in suggestions
    assert "Ordena de menor a mayor" in suggestions
    for chip in suggestions:
        intent = interpret_visual(chip, (context,))
        assert intent is not None and intent.patch is not None, chip
        merge_encoding_patch(spec, intent.patch, profile)

    # Nothing to lift when nothing was cut.
    assert "Ver todos" not in _chart_suggestions(
        VisualContext("chart-1", replace(spec, limit=None), profile)
    )
    temporal = ChartSpec(
        "line",
        (
            ChartEncoding("x", "dimension", "temporal"),
            ChartEncoding("y", "value_minor", "quantitative"),
        ),
    )
    temporal_profile = VisualProfile(
        fields=(
            VisualField("dimension", "temporal", "dimension", 3, "TEMPORAL"),
            VisualField("value_minor", "quantitative", "measure", 3),
        ),
        metric="SUM",
        unit="money",
    )
    # A series is not reordered by its measure: no sort chip on a temporal axis.
    assert _chart_suggestions(VisualContext("chart-2", temporal, temporal_profile)) == [
        "Ponlo en barras"
    ]


@pytest.mark.parametrize("family_name", sorted(FAMILY_DATASETS))
def test_every_chip_the_floor_offers_over_this_family_verifies_when_applied(
    family_name: str,
) -> None:
    """C1: `_chart_suggestions` used to offer chips no gate had reviewed, and on the commonest
    chart -- a plain breakdown -- one of them (a line over unordered categories) was refused the
    moment a holder tapped it. Driven through the real gate, not a mock: every chip this
    generator offers over the floor's own chart, for every family the exporter renders, must be
    a chip `apply_visual_intent` + `verify(draft_of(...), ...)` actually accepts.

    The reviewer's own probe, pinned rather than described: the plain breakdown family no longer
    offers «Ponlo en línea» (the `mark` rule refuses a line over unordered categories) while it
    still offers «Ordena de menor a mayor» (the axis already carries its own descending sort,
    which a chart-wide ascending patch cannot unrank -- see the test above).
    """
    dataset, kwargs = FAMILY_DATASETS[family_name]
    profile = visual_profile_of(dataset, **{"total_minor": None, **kwargs})
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None, f"the floor produced no chart for family {family_name!r}"
    context = VisualContext(f"chart-{family_name}", spec, profile)
    suggestions = _chart_suggestions(context)
    for chip in suggestions:
        intent = interpret_visual(chip, (context,))
        assert intent is not None and intent.patch is not None, (family_name, chip)
        merged = apply_visual_intent(intent, context)
        verified = verify(draft_of(merged.spec), context.profile)
        assert isinstance(verified, ChartSpec), (family_name, chip, verified)
    if family_name == "breakdown":
        assert "Ponlo en línea" not in suggestions
        assert "Ordena de menor a mayor" in suggestions


def test_ordena_de_menor_a_mayor_reorders_the_axis_the_floor_already_ranked() -> None:
    """F1 (fix wave 2, P0): the interpreter always built a *chart-wide* `sort="ascending"`
    (`visual_interpreter.py:117-125`), and `present()` (`vega_lite.py:89-90`) lets a per-encoding
    sort win over it -- so over the floor's own ranked bar, whose dimension axis already carries
    `EncodingSort("descending", "value_minor")`, «ordena de menor a mayor» merged, verified,
    minted a new version, and Vega-Lite drew the exact same descending order. It was a no-op that
    looked like it worked.

    The fix: `merge_encoding_patch` now edits the axis that already owns the order -- the
    patch's actual meaning -- instead of a chart-wide setting nothing on the wire reads.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE")
    before_spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert before_spec is not None
    dimension_axis = next(
        item
        for item in before_spec.encodings
        if item.channel in {"x", "y"} and item.type == "nominal"
    )
    assert dimension_axis.sort == EncodingSort("descending", "value_minor")
    context = VisualContext("chart-1", before_spec, profile)
    intent = interpret_visual("ordena de menor a mayor", (context,))
    assert intent is not None and intent.patch is not None
    after = apply_visual_intent(intent, context)

    # The presented spec actually changes -- the whole point of the fix.
    assert present_spec(after.spec) != present_spec(before_spec)
    after_axis = next(
        item for item in after.spec.encodings if item.channel == dimension_axis.channel
    )
    assert after_axis.sort == EncodingSort("ascending", "value_minor")
    assert after.spec.sort is None
    assert after.spec.sort_field is None
    presented = present_spec(after.spec)
    assert presented["encoding"][dimension_axis.channel]["sort"] == {
        "field": "value_minor",
        "order": "ascending",
    }

    # The mirror: «de mayor a menor» over a chart already ascending flips it straight back.
    ascending_context = VisualContext("chart-2", after.spec, profile)
    back_intent = interpret_visual("ordena de mayor a menor", (ascending_context,))
    assert back_intent is not None and back_intent.patch is not None
    restored = apply_visual_intent(back_intent, ascending_context)
    restored_axis = next(
        item for item in restored.spec.encodings if item.channel == dimension_axis.channel
    )
    assert restored_axis.sort == EncodingSort("descending", "value_minor")
    assert restored.spec.sort is None
    assert restored.spec.sort_field is None


def test_a_temporal_axis_asked_to_sort_still_takes_the_chart_wide_path_and_is_refused() -> None:
    """F1 ruling: when no axis owns a sort, the merge still takes the chart-wide path exactly as
    it did before this fix. A temporal line's own axis never carries a per-encoding sort
    (`_sort_for` returns `None` for `order="TEMPORAL"`), so «ordena de menor a mayor» over one
    still reaches the verifier's `_order` rule the same way it always did, and is refused the
    same way -- this fix changes nothing about that family.
    """
    dataset = VisualDataset(
        metric="SUM", rows=(VisualRow("2026-01", 10_000), VisualRow("2026-02", 20_000))
    )
    profile = visual_profile_of(dataset, total_minor=None, group_by="MONTH")
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    dimension_axis = next(
        item for item in spec.encodings if item.channel in {"x", "y"} and item.type == "temporal"
    )
    assert dimension_axis.sort is None
    context = VisualContext("chart-1", spec, profile)
    intent = interpret_visual("ordena de menor a mayor", (context,))
    assert intent is not None and intent.patch is not None
    merged = apply_visual_intent(intent, context)
    # No axis owned a sort, so the merge fell back to the chart-wide setting, as today.
    assert merged.spec.sort == "ascending"
    assert merged.spec.sort_field == "value_minor"
    verdict = verify(draft_of(merged.spec), profile)
    assert isinstance(verdict, Rejection)
    assert verdict.rule == "order"


# The floor family, probed against the tree before this fix: which families' dimension axis is
# already ranked (`order="MEASURE_DESC"`) and so is exactly where this fix changes *how* the sort
# is applied (`breakdown`, `categorical_comparison`), and which were already refused on the
# chart-wide path this fix leaves untouched -- `cumulative`/`temporal`/`recurrence` (their
# dimension is ordered by time, not by magnitude), `frequency` (its dimension keeps the
# producer's order), `ratio_share` (its dimension sits in `color`, off any axis, so a chart-wide
# sort has no categorical/temporal x or y to land on). No family crosses from one column to the
# other: that is the regression this test exists to catch.
_ORDENA_ASCENDING_ACCEPTED_AT_BASE: frozenset[str] = frozenset(
    {"breakdown", "categorical_comparison"}
)


@pytest.mark.parametrize("family_name", sorted(FAMILY_DATASETS))
def test_ordena_de_menor_a_mayor_over_every_floor_family_verifies_wherever_it_did_at_base(
    family_name: str,
) -> None:
    """F1: every family the exporter renders (Task 3b's own eight, donut included below), run
    through «ordena de menor a mayor» and checked against what actually verified before this fix
    was written -- no family may newly refuse the patch, whatever its shape."""
    dataset, kwargs = FAMILY_DATASETS[family_name]
    profile = visual_profile_of(dataset, **{"total_minor": None, **kwargs})
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None, f"the floor produced no chart for family {family_name!r}"
    context = VisualContext(f"chart-{family_name}", spec, profile)
    intent = interpret_visual("ordena de menor a mayor", (context,))
    assert intent is not None and intent.patch is not None
    expect_accept = family_name in _ORDENA_ASCENDING_ACCEPTED_AT_BASE
    try:
        merged = apply_visual_intent(intent, context)
    except ValueError as exc:
        assert not expect_accept, f"{family_name}: newly refused by the merge ({exc})"
        return
    verdict = verify(draft_of(merged.spec), profile)
    if expect_accept:
        assert isinstance(verdict, ChartSpec), f"{family_name}: newly refused: {verdict}"
    else:
        assert isinstance(verdict, Rejection), f"{family_name}: unexpectedly accepted"


def test_ordena_de_menor_a_mayor_over_the_donut_family_is_still_refused_by_the_merge() -> None:
    """The eighth family: the floor never produces a donut, so the parametrised test above cannot
    cover it -- built the same way `test_the_donut_familys_chips_verify_too` below does. A donut
    has no x/y axis at all, so the chart-wide path this fix leaves alone still cannot find one to
    land the sort on, exactly as at BASE."""
    rows = (
        VisualRow(dimension="Alquiler", value_minor=60_000),
        VisualRow(dimension="Ocio", value_minor=40_000),
    )
    spec = ChartSpec(
        mark="donut",
        encodings=(
            ChartEncoding("theta", "value_minor", "quantitative", "Importe"),
            ChartEncoding("color", "dimension", "nominal", "Parte"),
            ChartEncoding("tooltip", "dimension", "nominal"),
            ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
        ),
    )
    dataset = VisualDataset(rows=rows, metric="SUM")
    profile = visual_profile_of(dataset, total_minor=100_000, group_by="NONE")
    context = VisualContext("chart-donut", spec, profile)
    intent = interpret_visual("ordena de menor a mayor", (context,))
    assert intent is not None and intent.patch is not None
    with pytest.raises(ValueError, match="categorical or temporal"):
        apply_visual_intent(intent, context)


def test_the_donut_familys_chips_verify_too() -> None:
    """The floor never produces a donut (`DeterministicVisualPlanner.plan` has no such branch),
    so the parametrised test above cannot cover it. Built the same way
    `export_chart_fixtures._donut_family` and the wire test beside it do.
    """
    rows = (
        VisualRow(dimension="Alquiler", value_minor=60_000),
        VisualRow(dimension="Ocio", value_minor=40_000),
    )
    spec = ChartSpec(
        mark="donut",
        encodings=(
            ChartEncoding("theta", "value_minor", "quantitative", "Importe"),
            ChartEncoding("color", "dimension", "nominal", "Parte"),
            ChartEncoding("tooltip", "dimension", "nominal"),
            ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
        ),
    )
    dataset = VisualDataset(rows=rows, metric="SUM")
    profile = visual_profile_of(dataset, total_minor=100_000, group_by="NONE")
    context = VisualContext("chart-donut", spec, profile)
    suggestions = _chart_suggestions(context)
    for chip in suggestions:
        intent = interpret_visual(chip, (context,))
        assert intent is not None and intent.patch is not None, chip
        merged = apply_visual_intent(intent, context)
        verified = verify(draft_of(merged.spec), context.profile)
        assert isinstance(verified, ChartSpec), (chip, verified)


def test_a_disabled_stack_emits_an_explicit_null_and_not_an_absent_property() -> None:
    """`stack=None` omits the property, and Vega-Lite's default for a bar with a quantitative
    measure and a colour series is to **stack**. So `None` means "let Vega-Lite decide" and
    Vega-Lite decides to stack. Only an explicit null turns it off."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal"),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "series", "nominal"),
            ChartEncoding("yOffset", "series", "nominal"),
        ),
        stack="disabled",
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["x"]["stack"] is None
    assert "yOffset" in emitted["spec"]["encoding"]


def test_an_absent_stack_is_still_absent() -> None:
    """The old behaviour has to stay reachable, because a temporal line needs no stack at all."""
    spec = ChartSpec(
        mark="line",
        encodings=(
            ChartEncoding("x", "dimension", "temporal"),
            ChartEncoding("y", "value_minor", "quantitative"),
        ),
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert "stack" not in emitted["spec"]["encoding"]["y"]


def test_an_offset_is_a_channel_the_vocabulary_knows() -> None:
    ChartEncoding("xOffset", "series", "nominal")  # must not raise
    with pytest.raises(ValueError, match="channel"):
        ChartEncoding("zOffset", "series", "nominal")


def test_a_stacked_spec_needs_a_quantitative_x_or_y_encoding() -> None:
    """`stack` only means something on the axis Vega-Lite draws bars along. The presenter's

    `next(...)` for the stacked channel has no default and raises `StopIteration` if that axis
    is missing -- so `ChartSpec` refuses the combination at construction instead, and the
    presenter's bare `next()` is provably safe. A donut cannot exercise this: `mark == "donut"`
    with a stack is already rejected by the guard above with its own message, so this uses a
    non-donut mark whose only quantitative encoding is on `theta`.
    """
    with pytest.raises(ValueError, match="quantitative"):
        ChartSpec(
            mark="point",
            encodings=(ChartEncoding("theta", "value_minor", "quantitative"),),
            stack="disabled",
        )


def test_a_chart_can_order_its_dimension_and_its_series_differently() -> None:
    """`ChartSpec` carries one `sort` and one `sort_field`, so two independent orders were not
    representable: a breakdown wants its dimension by magnitude and its legend in the order the
    producer built it. Without a per-encoding sort this design would promise two and ship one."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding(
                "y",
                "dimension",
                "nominal",
                sort=EncodingSort(mode="descending", field="value_minor"),
            ),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "series", "nominal", sort=EncodingSort(mode="input")),
        ),
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["y"]["sort"] == {
        "field": "value_minor",
        "order": "descending",
    }
    assert emitted["spec"]["encoding"]["color"]["sort"] is None  # Vega-Lite's "keep data order"


def test_a_sort_field_needs_a_mode_that_uses_one() -> None:
    with pytest.raises(ValueError, match="input"):
        EncodingSort(mode="input", field="value_minor")


def test_an_encoding_sort_rejects_an_unsupported_mode() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        EncodingSort(mode="sideways")  # type: ignore[arg-type]


def test_an_encoding_sort_field_must_be_an_identifier() -> None:
    with pytest.raises(ValueError, match="identifiers"):
        EncodingSort(mode="ascending", field="not an identifier")


def test_a_chart_wide_sort_field_must_be_an_identifier() -> None:
    """M4: `EncodingSort.field` and `ChartEncoding.field` both run `FIELD_NAME_PATTERN`;
    `ChartSpec.sort_field` checked only that it required `sort`, not that it was an identifier."""
    with pytest.raises(ValueError, match="identifiers"):
        ChartSpec(
            mark="bar",
            encodings=(
                ChartEncoding("y", "dimension", "nominal"),
                ChartEncoding("x", "value_minor", "quantitative"),
            ),
            sort="descending",
            sort_field="not an identifier",
        )


def test_a_per_encoding_sort_wins_over_the_chart_wide_sort_for_its_own_channel() -> None:
    """`ChartSpec.sort`/`sort_field` and a per-encoding `EncodingSort` can now both target the
    same Vega-Lite channel. Only one `"sort"` property can survive on that channel, and the
    encoding-level one wins -- it says something more specific about its own channel than the
    chart-wide default does. See the comment in `present()` where that precedence is decided."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding(
                "y",
                "dimension",
                "nominal",
                sort=EncodingSort(mode="ascending", field="movement_count"),
            ),
            ChartEncoding("x", "value_minor", "quantitative"),
        ),
        sort="descending",
        sort_field="value_minor",
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["y"]["sort"] == {
        "field": "movement_count",
        "order": "ascending",
    }


def test_a_per_encoding_input_sort_beats_a_colliding_chart_wide_sort() -> None:
    """`EncodingSort(mode="input")` writes a *present but falsy* `"sort": None`, so the
    precedence guard in `present()` has to check key membership and not truthiness -- a
    truthiness check would read that `None` as "nothing claimed this channel" and let the
    chart-wide `sort`/`sort_field` below overwrite it. This puts `input` on the very channel a
    colliding chart-wide sort targets, which the previous precedence test did not: that one used
    a *truthy* per-encoding sort, so a regression to truthiness would have passed it unchanged."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal", sort=EncodingSort(mode="input")),
            ChartEncoding("x", "value_minor", "quantitative"),
        ),
        sort="descending",
        sort_field="value_minor",
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["y"]["sort"] is None


def test_a_fieldless_encoding_sort_emits_the_bare_order_string() -> None:
    """The per-encoding translation has three wire shapes: the object form (a field), `null`
    (`input`), and this one -- a mode with no field, which Vega-Lite takes as a bare order
    string rather than an object."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal", sort=EncodingSort(mode="ascending")),
            ChartEncoding("x", "value_minor", "quantitative"),
        ),
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["spec"]["encoding"]["y"]["sort"] == "ascending"


def test_encoding_sort_from_payload_is_a_tripwire_not_a_filter() -> None:
    """`VisualContextPayload._value_free_spec` is the guard now: it constructs `EncodingSort`
    itself and refuses an unsupported mode with a 422 before `to_domain` ever runs. So a
    `ValueError` reaching `_encoding_sort_from_payload` is no longer a caller's malformed
    request -- every caller's is stopped at the DTO -- it is the guard and the domain rule it
    enforces coming apart, and this is the tripwire that proves it surfaces rather than being
    caught and turned back into a silent `None`, the way it used to be."""
    with pytest.raises(ValueError, match="unsupported sort mode"):
        _encoding_sort_from_payload({"mode": "sideways"})


def test_a_grouped_comparison_survives_round_trips_and_a_follow_up_patch() -> None:
    profile = visual_profile_of(
        VisualDataset(
            rows=(
                VisualRow("Comida", 12, series="julio"),
                VisualRow("Comida", 8, series="junio"),
                VisualRow("Casa", 5, series="julio"),
                VisualRow("Casa", 4, series="junio"),
            ),
            metric="COMPARISON",
        ),
        total_minor=None,
        group_by="CATEGORY",
        plan_metric="COUNT",
    )
    original_spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert original_spec is not None
    assert original_spec.stack == "disabled"

    conversation = Conversation(visual_contexts=(VisualContext("chart-1", original_spec, profile),))
    restored = to_domain(to_payload(conversation))
    restored_spec = restored.visual_contexts[0].spec
    assert restored_spec == original_spec
    assert decode_chart(encode_chart(original_spec)) == original_spec

    patched = merge_encoding_patch(restored_spec, EncodingPatch(limit=3), profile)
    quantitative = next(
        encoding.channel
        for encoding in patched.encodings
        if encoding.type == "quantitative" and encoding.channel in {"x", "y"}
    )
    assert present_spec(patched)["encoding"][quantitative]["stack"] is None
