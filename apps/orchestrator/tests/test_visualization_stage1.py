from dataclasses import asdict, replace

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.chart import (
    ROWS_THAT_FIT,
    ChartEncoding,
    ChartSpec,
    Visualization,
    derive_chart,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.mappers.llm_mapper import question_to_dto
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
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


def _spec(group_by: str, rows: int, *, plan_limited: bool = False) -> ChartSpec:
    spec = derive_chart(
        group_by=group_by,
        metric="SUM",
        rows=rows,
        fields=frozenset({"dimension", "value_minor"}),
        dimension_field="dimension",
        value_field="value_minor",
        plan_limited=plan_limited,
    )
    assert spec is not None
    return spec


def test_a_wide_breakdown_draws_what_fits_and_yields_to_a_number_somebody_said() -> None:
    """The default is what fits; anything explicit beats it.

    Thirty merchants in a plot sized by its own rows is thirty threads, so a breakdown nobody put a
    number on draws the rows that fit. But cutting is a presentation decision, and it has to lose to
    every other kind: a plan that already ranked its rows has answered this question, and cutting
    again would answer "mis top 25 comercios" with eight -- and persist an eight nobody asked for
    into the saved metric, since the spec is what gets signed.
    """
    assert _spec("MERCHANT", 30).limit == ROWS_THAT_FIT
    assert _spec("MERCHANT", 30, plan_limited=True).limit is None
    # Nothing to cut: fewer rows than fit says nothing rather than claiming a sample.
    assert _spec("MERCHANT", ROWS_THAT_FIT).limit is None
    # A temporal axis is never trimmed. A ranking tails off; a series would lose months out of its
    # middle and still look like a series.
    assert _spec("MONTH", 30).limit is None


def test_the_wire_carries_the_limit_so_the_client_can_admit_to_it() -> None:
    """The number is in the Vega transform already, but a client should not have to parse that."""
    rows = tuple(
        VisualRow(dimension=f"Comercio {position}", value_minor=1000 - position)
        for position in range(30)
    )
    result = _result(rows, group_by="MERCHANT")
    visualization = question_to_dto(result).visualization
    assert visualization is not None and visualization.kind == "chart"
    assert visualization.limit == ROWS_THAT_FIT
    # Every row still travels: the limit hides rows from the plot, never from the evidence.
    assert len(visualization.data) == 30


def test_a_figure_is_offered_for_saving_even_with_no_chart_to_hang_it_from() -> None:
    """What makes an answer keepable is a plan and a figure, not a chart.

    "Cuanto he gastado este mes" is one number, so it has no chart -- and it is the most obvious
    metric anybody would save. The offer used to be gated on a `ChartVisualizationResponse`, which
    made it the single answer with no way to keep it.
    """
    tokens = MetricTokenCodec({"active": b"z" * 32}, "active")
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
    figure = replace(
        _result(None),
        total_minor=48945,
        movement_count=12,
        metric_definition=plan,
    )

    response = question_to_dto(figure, holder_reference="holder", metric_tokens=tokens)

    assert response.visualization is None
    assert response.save_candidate is not None
    # Null rather than invented: there is no chart, so nothing a restyle could target.
    assert response.save_candidate.chart_id is None
    saved = tokens.verify(response.save_candidate.definition_token, "holder")
    assert saved.plan == plan
    assert saved.chart is None


def test_an_answer_with_no_figure_is_not_offered_for_saving() -> None:
    """A payment preview carries the last plan too, and it is not a metric."""
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
    tokens = MetricTokenCodec({"active": b"z" * 32}, "active")
    without_figure = replace(_result(None), metric_definition=plan)

    response = question_to_dto(without_figure, holder_reference="holder", metric_tokens=tokens)

    assert response.save_candidate is None
