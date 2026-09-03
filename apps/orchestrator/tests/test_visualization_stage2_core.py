from dataclasses import replace

import pytest

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
    VisualContext,
    VisualField,
    Visualization,
    VisualProfile,
    merge_encoding_patch,
)
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.controllers.llm_controller import _visual_only_response
from finguard_orchestrator.infrastructure.dto.llm_dto import VisualizationDerivation
from finguard_orchestrator.infrastructure.mappers.conversation_mapper import to_domain, to_payload
from finguard_orchestrator.infrastructure.mappers.llm_mapper import (
    _chart_suggestions,
    question_to_dto,
)
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
from finguard_orchestrator.infrastructure.visualization.interpreter import interpret_visual
from finguard_orchestrator.infrastructure.visualization.vega_lite import present, present_spec


def _decided(result: AskFinancialQuestionResult) -> AskFinancialQuestionResult:
    """The Result as the handler hands it to the mapper.

    The chart is no longer decided inside `question_to_dto`; the application decides it and the
    Result carries it. This is the handler's own line, called with the handler's own arguments, so
    a Result built here is the one a live turn produces.
    """
    return _decided_visual(result, DeterministicVisualPlanner(), DEFAULT_PRESENTATION)


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
            VisualField("dimension", "nominal", "dimension", 3),
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


def test_temporal_chart_supports_bar_then_value_sort_follow_ups() -> None:
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

    with_bars = to_domain(bars_response.conversation)
    assert [item.chart_id for item in with_bars.visual_contexts] == ["chart-months", chart_b]
    assert with_bars.visual_contexts[0].spec.mark == "line"
    assert with_bars.visual_contexts[1].spec.mark == "bar"
    ordered = interpret_visual("ahora ordénalo de mayor a menor", with_bars.visual_contexts)
    assert ordered is not None
    ordered_response = _visual_only_response(ordered, with_bars)
    assert ordered_response.outcome == "VISUAL_UPDATED"
    assert ordered_response.visualization_derivation is not None
    chart_c = ordered_response.visualization_derivation.chart_id
    assert ordered_response.visualization_derivation.source_chart_id == chart_b
    assert len({"chart-months", chart_b, chart_c}) == 3
    with_order = to_domain(ordered_response.conversation)
    assert [item.chart_id for item in with_order.visual_contexts] == [
        "chart-months",
        chart_b,
        chart_c,
    ]
    assert with_order.visual_contexts[0].spec.mark == "line"
    assert with_order.visual_contexts[1].spec.sort is None
    assert with_order.visual_contexts[2].spec.sort == "descending"
    encoding = ordered_response.visualization_derivation.spec["encoding"]
    assert encoding["x"]["type"] == "nominal"
    assert encoding["x"]["sort"] == {"field": "value_minor", "order": "descending"}
    wire = ordered_response.model_dump(mode="json")
    assert "data" not in repr(wire["visualization_derivation"])
    assert "values" not in repr(wire["visualization_derivation"])


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


def test_chart_response_emits_id_and_structural_context_without_rows() -> None:
    result = _decided(
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


def test_identical_chart_answers_receive_distinct_ids() -> None:
    result = _decided(
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


def test_chart_unit_declares_count_and_ratio_without_changing_minor_rows() -> None:
    count = question_to_dto(
        _decided(
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
        _decided(
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


def test_breakdown_total_verification_requires_complete_total() -> None:
    def mapped(total: int | None, rows: tuple[VisualRow, ...]):
        return question_to_dto(
            _decided(
                AskFinancialQuestionResult(
                    outcome="PLANNED",
                    text="x",
                    total_minor=total,
                    plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
                    visual_dataset=VisualDataset(rows, "SUM"),
                )
            )
        ).conversation

    complete = mapped(150, (VisualRow("a", 100), VisualRow("b", 50)))
    absent = mapped(None, (VisualRow("a", 100), VisualRow("b", 50)))
    truncated = mapped(150, (VisualRow("a", 100), VisualRow("b", 20)))
    ratio = question_to_dto(
        _decided(
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
    spec, profile = _base()
    conversation = Conversation(visual_contexts=(VisualContext("chart-1", spec, profile),))
    intent = interpret_visual("ponlo en línea", conversation.visual_contexts)
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
    assert updated.chart.mark == "line"
    assert updated.plan == definition.plan


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
    """Every chip round-trips through the interpreter that has to read it back."""
    spec, profile = _limited()
    contexts = (VisualContext("chart-1", spec, profile),)
    suggestions = _chart_suggestions(spec)
    assert "Ver todos" in suggestions
    assert "Ponlo en línea" in suggestions
    assert "Ordena de menor a mayor" in suggestions
    for chip in suggestions:
        intent = interpret_visual(chip, contexts)
        assert intent is not None and intent.patch is not None, chip
        merge_encoding_patch(spec, intent.patch, profile)

    # Nothing to lift when nothing was cut, and a series is not reordered by its measure.
    assert "Ver todos" not in _chart_suggestions(replace(spec, limit=None))
    temporal = ChartSpec(
        "line",
        (
            ChartEncoding("x", "dimension", "temporal"),
            ChartEncoding("y", "value_minor", "quantitative"),
        ),
    )
    assert _chart_suggestions(temporal) == ["Ponlo en barras"]


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
