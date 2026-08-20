from dataclasses import asdict

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    Visualization,
    derive_chart,
)
from finguard_orchestrator.infrastructure.mappers.llm_mapper import question_to_dto
from finguard_orchestrator.infrastructure.visualization.vega_lite import present


def test_chart_references_only_result_fields_and_never_values() -> None:
    spec = derive_chart(
        group_by="CATEGORY",
        metric="SUM",
        rows=2,
        fields=frozenset({"key", "value_minor", "movement_count"}),
        value_field="value_minor",
    )
    assert spec is not None
    assert spec.valid_for(frozenset({"key", "value_minor", "movement_count"}))
    serialized = asdict(spec)
    assert not {"data", "datum", "value", "values"} & set(repr(serialized).split("'"))
    assert all(isinstance(encoding.field, str) for encoding in spec.encodings)
    assert not spec.valid_for(frozenset({"key", "other_value"}))


def test_missing_field_is_rejected() -> None:
    spec = ChartSpec(mark="line", encodings=(ChartEncoding("x", "absent", "nominal"),))
    assert not spec.valid_for(frozenset({"key"}))


def test_presenter_keeps_rows_outside_vega_spec() -> None:
    spec = derive_chart(
        group_by="DAY",
        metric="SUM",
        rows=2,
        fields=frozenset({"key", "value_minor"}),
        value_field="value_minor",
    )
    assert spec is not None
    payload = present(Visualization(spec=spec, data=({"key": "lunes", "value_minor": 1},)))
    assert "values" not in payload["spec"]
    assert payload["data"] == [{"key": "lunes", "value_minor": 1}]


def test_single_row_abstains() -> None:
    assert (
        derive_chart(
            group_by="CATEGORY",
            metric="SUM",
            rows=1,
            fields=frozenset({"key", "value_minor"}),
            value_field="value_minor",
        )
        is None
    )


def test_deterministic_defaults_distinguish_time_cumulative_and_categories() -> None:
    fields = frozenset({"dimension", "value_minor", "movement_count"})
    temporal = derive_chart(
        group_by="MONTH",
        metric="SUM",
        rows=2,
        fields=fields,
        dimension_field="dimension",
        value_field="value_minor",
    )
    cumulative = derive_chart(
        group_by="MONTH",
        metric="CUMULATIVE",
        rows=2,
        fields=fields,
        dimension_field="dimension",
        value_field="value_minor",
    )
    categorical = derive_chart(
        group_by="CATEGORY",
        metric="SUM",
        rows=2,
        fields=fields,
        dimension_field="dimension",
        value_field="value_minor",
    )
    assert temporal is not None and temporal.mark == "line"
    assert cumulative is not None and cumulative.mark == "area"
    assert categorical is not None and categorical.mark == "bar"
    assert categorical.encodings[0].channel == "y"


def _result(
    rows: tuple[VisualRow, ...] | None,
    *,
    metric: str = "SUM",
    group_by: str = "CATEGORY",
    explicit: bool = False,
) -> AskFinancialQuestionResult:
    return AskFinancialQuestionResult(
        outcome="PLANNED",
        text="Resultado",
        plan_context=SpokenPlan(direction="EXPENSE", group_by=group_by, metric=metric),
        groups=[SpokenGroup("visible", 100, 2), SpokenGroup("also visible", 50, 1)],
        explicit_visualization=explicit,
        visual_dataset=VisualDataset(rows=rows, metric=metric) if rows is not None else None,
    )


def test_mapper_uses_typed_series_and_every_encoding_names_a_real_field() -> None:
    dto = question_to_dto(
        _result(
            (
                VisualRow("Comida", 100, series="agosto"),
                VisualRow("Comida", 80, series="julio"),
            ),
            metric="COMPARISON",
        )
    )
    assert dto.visualization is not None
    assert dto.visualization.kind == "chart"
    assert dto.visualization.spec["encoding"]["color"]["field"] == "series"  # type: ignore[index]
    assert {row["series"] for row in dto.visualization.data} == {"agosto", "julio"}
    encoded = dto.visualization.spec["encoding"]
    fields = {
        item["field"]
        for channel in encoded.values()  # type: ignore[union-attr]
        for item in (channel if isinstance(channel, list) else [channel])
    }
    assert all(fields <= set(row) for row in dto.visualization.data)


def test_mapper_never_infers_a_chart_from_public_groups() -> None:
    assert question_to_dto(_result(None)).visualization is None


def test_explicit_single_row_is_a_kpi_and_never_a_vega_mark() -> None:
    dto = question_to_dto(_result((VisualRow("total", 1234),), explicit=True))
    assert dto.visualization is not None
    assert dto.visualization.kind == "kpi"
    assert dto.visualization.value_field == "value_minor"
    assert dto.visualization.data == [{"dimension": "total", "value_minor": 1234}]


def test_share_ratio_is_a_normalized_bar_bound_to_verified_operands() -> None:
    dto = question_to_dto(
        _result(
            (VisualRow("Parte", 25), VisualRow("Resto", 75)),
            metric="RATIO_SHARE",
        )
    )
    assert dto.visualization is not None
    assert dto.visualization.kind == "chart"
    assert dto.visualization.spec["encoding"]["x"]["stack"] == "normalize"  # type: ignore[index]


def test_frequency_keeps_weekday_input_order_and_recurrence_is_a_timeline() -> None:
    frequency = question_to_dto(
        _result(
            tuple(VisualRow(day, count) for day, count in (("Lun", 2), ("Mar", 0))),
            metric="FREQUENCY",
        )
    )
    assert frequency.visualization is not None
    assert frequency.visualization.kind == "chart"
    assert frequency.visualization.spec["encoding"]["x"]["sort"] is None  # type: ignore[index]

    recurrence = question_to_dto(
        _result(
            (
                VisualRow("Netflix", 1299, series="MONTHLY", occurred_at="2026-07-01"),
                VisualRow("Netflix", 1299, series="MONTHLY", occurred_at="2026-08-01"),
            ),
            metric="RECURRENCE",
        )
    )
    assert recurrence.visualization is not None
    assert recurrence.visualization.kind == "chart"
    assert recurrence.visualization.spec["mark"] == {"type": "point"}
    assert recurrence.visualization.spec["encoding"]["x"]["field"] == "occurred_at"  # type: ignore[index]
