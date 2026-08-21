import pytest

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    EncodingPatch,
    VisualContext,
    Visualization,
    VisualProfile,
    merge_encoding_patch,
)
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.controllers.llm_controller import _visual_only_response
from finguard_orchestrator.infrastructure.mappers.conversation_mapper import to_domain, to_payload
from finguard_orchestrator.infrastructure.mappers.llm_mapper import question_to_dto
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
from finguard_orchestrator.infrastructure.visualization.interpreter import interpret_visual
from finguard_orchestrator.infrastructure.visualization.vega_lite import present


def _base() -> tuple[ChartSpec, VisualProfile]:
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("y", "dimension", "nominal"),
        ),
    )
    return spec, VisualProfile.from_spec(
        spec,
        cardinality=3,
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
    )


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
    with pytest.raises(ValueError):
        merge_encoding_patch(
            spec, EncodingPatch(mark="donut"), VisualProfile.from_spec(spec, cardinality=1)
        )


def test_ratio_share_can_become_a_donut_and_return_to_bars() -> None:
    spec = ChartSpec(
        "bar",
        (
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "dimension", "nominal"),
        ),
        stack="normalize",
    )
    profile = VisualProfile.from_spec(
        spec,
        cardinality=2,
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
    result = AskFinancialQuestionResult(
        outcome="PLANNED",
        text="Resultado",
        question="gastos por categoría",
        plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
        groups=[SpokenGroup("Comida", 100, 1), SpokenGroup("Casa", 50, 1)],
        visual_dataset=VisualDataset((VisualRow("Comida", 100), VisualRow("Casa", 50)), "SUM"),
    )
    response = question_to_dto(result)
    assert response.visualization is not None
    assert response.visualization.kind == "chart"
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
    result = AskFinancialQuestionResult(
        outcome="PLANNED",
        text="x",
        question="gastos",
        plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
        visual_dataset=VisualDataset((VisualRow("a", 100), VisualRow("b", 50)), "SUM"),
    )
    first = question_to_dto(result).visualization
    second = question_to_dto(result).visualization
    assert first is not None and second is not None
    assert first.kind == "chart" and second.kind == "chart"
    assert first.chart_id != second.chart_id


def test_breakdown_total_verification_requires_complete_total() -> None:
    def mapped(total: int | None, rows: tuple[VisualRow, ...]):
        return question_to_dto(
            AskFinancialQuestionResult(
                outcome="PLANNED",
                text="x",
                total_minor=total,
                plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
                visual_dataset=VisualDataset(rows, "SUM"),
            )
        ).conversation

    complete = mapped(150, (VisualRow("a", 100), VisualRow("b", 50)))
    absent = mapped(None, (VisualRow("a", 100), VisualRow("b", 50)))
    truncated = mapped(150, (VisualRow("a", 100), VisualRow("b", 20)))
    ratio = question_to_dto(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="x",
            visual_dataset=VisualDataset(
                (VisualRow("Parte", 100), VisualRow("Resto", 50)), "RATIO_SHARE"
            ),
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
    assert response.visualization_update is not None
    assert response.visualization_update.spec["$schema"]
    assert response.visualization_update.spec["encoding"]
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
    updated = tokens.verify(response.save_candidate.definition_token, "holder")
    assert updated.chart.mark == "line"
    assert updated.plan == definition.plan
