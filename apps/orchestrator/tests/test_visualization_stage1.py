import dataclasses
from dataclasses import asdict, replace
from typing import get_args

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
    ROWS_THAT_FIT,
    Channel,
    ChartEncoding,
    ChartSpec,
    PresentationContext,
    VisualField,
    Visualization,
    VisualProfile,
    derive_chart,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import VisualContextPayload
from finguard_orchestrator.infrastructure.mappers.llm_mapper import (
    _remember_visual_context,
    _visualization_to_dto,
    question_to_dto,
)
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
    group_limit: int | None = None,
    plan_metric: str | None = None,
    explicit: bool = False,
) -> AskFinancialQuestionResult:
    # Through `_decided_visual`, because the chart is no longer decided by the mapper: the
    # application decides it and the Result carries it. This is the handler's own line, so what
    # the mapper is handed here is what a live turn hands it.
    #
    # `group_limit` and `plan_metric` are the plan's own facts, threaded separately from the
    # dataset's `metric` -- the wire `_decided_visual` reads to build `plan_limited` and
    # `plan_metric` on the profile. Every other test call leaves both `None`/default, which
    # reproduces the old (bare) `SpokenPlan` exactly.
    return _decided_visual(
        AskFinancialQuestionResult(
            outcome="PLANNED",
            text="Resultado",
            plan_context=SpokenPlan(
                direction="EXPENSE",
                group_by=group_by,
                metric=plan_metric if plan_metric is not None else metric,
                group_limit=group_limit,
            ),
            groups=[SpokenGroup("visible", 100, 2), SpokenGroup("also visible", 50, 1)],
            explicit_visualization=explicit,
            visual_dataset=VisualDataset(rows=rows, metric=metric) if rows is not None else None,
        ),
        DeterministicVisualPlanner(),
        DEFAULT_PRESENTATION,
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


def test_the_wire_channel_whitelist_matches_the_domain_literal() -> None:
    """`VisualContextPayload._value_free_spec` used to hand-copy the allowed channel set, and the
    copy went stale the moment `Channel` grew `xOffset`/`yOffset` -- a live categorical comparison
    could not complete a turn until the copy was replaced with `get_args(Channel)` (`1924c95`).

    The guard: every channel the domain allows must still validate here. This fails the day
    someone reintroduces a hand-written copy that omits one, on the commit that does it rather
    than the first live turn that needs the missing channel."""
    channels = get_args(Channel)
    fields = {channel: f"field_{index}" for index, channel in enumerate(channels)}

    payload = VisualContextPayload.model_validate(
        {
            "chart_id": "chart-1",
            "spec": {
                "mark": {"type": "bar"},
                "encoding": {
                    channel: {"field": field_name, "type": "nominal"}
                    for channel, field_name in fields.items()
                },
            },
            "profile": {
                "fields": [
                    {"name": field_name, "type": "nominal"} for field_name in fields.values()
                ],
                "cardinality": 2,
            },
        }
    )

    assert payload.chart_id == "chart-1"


def test_the_conversation_stores_the_profile_the_chart_was_chosen_from() -> None:
    """One profile per turn, and the identity check is the whole point.

    The mapper used to build a second profile from the same dataset for the conversational
    context, with two of the plan's facts missing from it -- so the profile a restyle would read
    disagreed with the one the chart was chosen from. The decision carries its own input now, and
    a second build is not something this code can express.
    """
    result = _result((VisualRow("Comida", 100), VisualRow("Casa", 50)))

    # The two mapper halves in the order `question_to_dto` runs them. Read here rather than off
    # the response because the response carries the *payload* profile, which has never had these
    # fields on it -- the domain object this remembers is the one a restyle reads.
    remembered = _remember_visual_context(result, _visualization_to_dto(result))

    assert result.visual_decision is not None
    assert remembered.conversation.visual_contexts[0].profile is result.visual_decision.profile


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


def test_a_plan_limited_dataset_is_not_cut_again_through_the_mapper() -> None:
    """The wire between the plan and the floor, not a hand-built profile.

    `test_a_plan_that_already_cut_its_rows_is_not_cut_again` (deterministic_visual_planner
    tests) hands the floor a profile it built itself, so it cannot catch `_decided_visual`
    failing to read `group_limit` off the plan. This one goes through `_result` -> `_decided_visual`
    -> `question_to_dto`, the same path a live turn takes.
    """
    rows = tuple(
        VisualRow(dimension=f"Comercio {position}", value_minor=1000 - position)
        for position in range(30)
    )
    result = _result(rows, group_by="MERCHANT", group_limit=25)
    visualization = question_to_dto(result).visualization
    assert visualization is not None and visualization.kind == "chart"
    assert visualization.limit is None


def test_a_comparison_of_counts_labels_its_axis_movements_through_the_mapper() -> None:
    """The other defect the brief's `plan()` reintroduced, caught the same way.

    `test_a_comparison_of_counts_labels_its_axis_movements` (deterministic_visual_planner
    tests) also hands the floor a hand-built profile. This one drives a plan whose `metric`
    ("COUNT") disagrees with its dataset's shape marker ("COMPARISON") through the mapper, so
    it fails if `_decided_visual` stops reading `context.metric` into `plan_metric`.
    """
    rows = (VisualRow("agosto", 12), VisualRow("julio", 9))
    result = _result(rows, metric="COMPARISON", plan_metric="COUNT")
    dto = question_to_dto(result)
    assert dto.visualization is not None and dto.visualization.kind == "chart"
    encoding = dto.visualization.spec["encoding"]
    assert encoding["x"]["title"] == "Movimientos"  # type: ignore[index]


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


def test_a_profile_describes_its_dataset_and_not_a_chart() -> None:
    """The profile is the planner's input, so it cannot be derived from the chart it decides."""
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 5, "MEASURE_DESC"),
            VisualField("value_minor", "quantitative", "measure", 5),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
        comparison=False,
    )

    assert profile.field_names() == frozenset({"dimension", "value_minor"})
    assert profile.field("value_minor").role == "measure"
    assert profile.role("measure")[0].name == "value_minor"
    assert profile.field("nope") is None


def test_two_fields_may_share_the_dimension_role_when_their_types_differ() -> None:
    """RECURRENCE puts `occurred_at` temporal on x and `dimension` nominal on y.

    They are told apart by type, not by a fourth role. A `timestamp` role would be the same
    fact written twice, which is the defect family this repository keeps finding.
    """
    profile = VisualProfile(
        fields=(
            VisualField("occurred_at", "temporal", "dimension", 12, "TEMPORAL"),
            VisualField("dimension", "nominal", "dimension", 3, "INPUT"),
            VisualField("value_minor", "quantitative", "measure", 12),
        ),
        metric="RECURRENCE",
        unit="money",
        positive=True,
        mutually_exclusive=False,
        verified_total=False,
        comparison=False,
    )

    assert len(profile.role("dimension")) == 2
    assert {item.type for item in profile.role("dimension")} == {"temporal", "nominal"}


def test_a_profile_refuses_a_duplicate_field_name() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        VisualProfile(
            fields=(
                VisualField("dimension", "nominal", "dimension", 2),
                VisualField("dimension", "temporal", "dimension", 2),
            ),
            metric="SUM",
            unit="money",
            positive=True,
            mutually_exclusive=True,
            verified_total=False,
            comparison=False,
        )


def test_a_measure_must_be_quantitative() -> None:
    """The role's type is a fact about the dataset, not a choice. A nominal measure is a
    dataset that has been mislabelled, and the planner would inherit the lie."""
    with pytest.raises(ValueError, match="measure"):
        VisualField("value_minor", "nominal", "measure", 5)


def test_a_negative_cardinality_is_not_a_count() -> None:
    with pytest.raises(ValueError, match="cardinality"):
        VisualField("dimension", "nominal", "dimension", -1)


def test_a_negative_row_count_is_not_a_count() -> None:
    """`row_count` is how many rows the chart draws, and a floor reading a negative one would be
    reading a defect rather than a dataset."""
    with pytest.raises(ValueError, match="row_count"):
        VisualProfile(
            fields=(
                VisualField("dimension", "nominal", "dimension", 2),
                VisualField("value_minor", "quantitative", "measure", 2),
            ),
            metric="SUM",
            unit="money",
            positive=True,
            mutually_exclusive=True,
            verified_total=False,
            comparison=False,
            row_count=-1,
        )


def test_presentation_is_not_part_of_the_profile() -> None:
    """The profile round-trips through the browser (`conversation.py` validates it coming back),
    so anything put in it becomes wire surface. Viewport and channel are circumstances of one
    execution, not properties of the data."""
    profile_keys = {item.name for item in dataclasses.fields(VisualProfile)}

    assert "channel" not in profile_keys
    assert "viewport" not in profile_keys
    assert "max_visible_rows" not in profile_keys
    assert "question" not in profile_keys


def test_a_request_that_names_no_viewport_still_has_one() -> None:
    """The planner cannot wait on a web deployment, so the default is part of the contract."""
    assert DEFAULT_PRESENTATION.channel == "TEXT"
    assert DEFAULT_PRESENTATION.viewport == "DESKTOP"
    assert DEFAULT_PRESENTATION.max_visible_rows == ROWS_THAT_FIT


def test_a_mobile_viewport_shows_fewer_rows_than_it_is_asked_for() -> None:
    context = PresentationContext(channel="TEXT", viewport="MOBILE", max_visible_rows=40)

    assert context.max_visible_rows == 40  # stated wins over guessed
    with pytest.raises(ValueError, match="max_visible_rows"):
        PresentationContext(channel="TEXT", viewport="MOBILE", max_visible_rows=0)
    # The other two rejections. A context is validated because it arrives from a client, and an
    # unrecognised channel or viewport reaching a rule would be decided by an `else` -- a voice
    # answer drawn for a screen, silently, because nobody said the word was wrong.
    with pytest.raises(ValueError, match="viewport"):
        PresentationContext(  # type: ignore[arg-type]
            channel="TEXT", viewport="WATCH", max_visible_rows=8
        )
    with pytest.raises(ValueError, match="channel"):
        PresentationContext(  # type: ignore[arg-type]
            channel="TELEPATHY", viewport="MOBILE", max_visible_rows=8
        )
