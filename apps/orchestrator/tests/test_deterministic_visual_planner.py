"""The floor, measured as a move: today's chart, decided one layer up."""

import uuid
from datetime import date

import pytest

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ROWS_THAT_FIT,
    ChartEncoding,
    ChartSpec,
    EncodingSort,
    VisualDataset,
    Visualization,
    VisualProfile,
    VisualRow,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
    _sort_for,
)
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.mappers.llm_mapper import question_to_dto
from finguard_orchestrator.infrastructure.visualization.vega_lite import present


def some_profile() -> VisualProfile:
    return visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(VisualRow("GROCERIES", 24_908), VisualRow("RESTAURANTS", 10_385)),
        ),
        total_minor=None,
        group_by="NONE",
    )


def some_bar_spec() -> ChartSpec:
    return ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal", "Grupo"),
            ChartEncoding("x", "value_minor", "quantitative", "Importe"),
        ),
        sort="descending",
    )


def test_the_floor_reproduces_todays_chart_for_a_breakdown() -> None:
    """Task 4 is a move, not a behaviour change. This is the assertion that says so."""
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="GROCERIES", value_minor=24_908),
                VisualRow(dimension="RESTAURANTS", value_minor=10_385),
            ),
        ),
        total_minor=None,
        group_by="NONE",
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec is not None
    assert spec.mark == "bar"
    assert {(e.channel, e.field) for e in spec.encodings} >= {
        ("y", "dimension"),
        ("x", "value_minor"),
    }
    # No series, so the comparison rule's `if` and the plain-colour `elif` both decline: this
    # chart takes neither branch, and `derive_chart` never sets a stack of its own. Only the
    # positive direction of the comparison rule was pinned before -- this is the negative one,
    # and its absence is exactly how the original defect (an unwanted default stack) survived.
    assert spec.stack is None


def test_the_floor_declines_a_single_row() -> None:
    """One row is a KPI, and the eligibility guard keeps it out of the policy entirely. The
    floor returning None is what that guard reads."""
    profile = visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow(dimension="GROCERIES", value_minor=100),)),
        total_minor=None,
        group_by="NONE",
    )

    assert DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION) is None


def test_a_temporal_dimension_is_a_line_and_a_cumulative_one_is_an_area() -> None:
    for metric, mark in (("SUM", "line"), ("CUMULATIVE", "area")):
        profile = visual_profile_of(
            VisualDataset(
                metric=metric,
                rows=(
                    VisualRow(dimension="2026-01", value_minor=100),
                    VisualRow(dimension="2026-02", value_minor=200),
                ),
            ),
            total_minor=None,
            group_by="MONTH",
        )

        spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

        assert spec is not None
        assert spec.mark == mark, metric


def test_a_decision_records_where_it_came_from() -> None:
    # `profile` is required: a decision carries the input it was made from, so a chart and
    # the profile stored beside it cannot come from two different builds.
    decision = VisualDecision(spec=some_bar_spec(), profile=some_profile(), source="deterministic")

    assert decision.source == "deterministic"
    assert decision.rationale is None
    assert decision.fallback_reason is None  # nothing fell back; the floor was the plan


def test_a_decision_refuses_an_unsupported_source() -> None:
    with pytest.raises(ValueError, match="unsupported visual decision source"):
        VisualDecision(
            spec=some_bar_spec(),
            profile=some_profile(),
            source="heuristic",  # type: ignore[arg-type]
        )


def test_a_chosen_model_plan_did_not_fall_back() -> None:
    """`fallback_reason` is filled only when an attempt was made and failed. `source="llm"` means
    the model's own plan was chosen, so the two facts together would be a contradiction."""
    with pytest.raises(ValueError, match="did not fall back"):
        VisualDecision(
            spec=some_bar_spec(),
            profile=some_profile(),
            source="llm",
            fallback_reason="the model declined",
        )


def test_two_rows_carrying_the_same_amount_are_still_two_rows() -> None:
    """The measure's cardinality counts distinct amounts, so it is not a row count. Passing it
    to `derive_chart` would decline this chart for having "one row"."""
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="GROCERIES", value_minor=10_000),
                VisualRow(dimension="RESTAURANTS", value_minor=10_000),
            ),
        ),
        total_minor=None,
        group_by="NONE",
    )

    assert profile.row_count == 2
    assert DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION) is not None


def test_a_plan_that_already_cut_its_rows_is_not_cut_again() -> None:
    """`_chart_for_dataset` read `plan_context.group_limit` and passed it as `plan_limited`, and
    `derive_chart` reads that beside the row count on the same line. Dropped, "mis top 25
    comercios" would be answered with eight -- and the eight would be signed into the saved
    metric. It travels on the profile for the same reason `row_count` does: the floor takes a
    profile and nothing else."""
    rows = tuple(
        VisualRow(dimension=f"Comercio {position}", value_minor=1_000 - position)
        for position in range(30)
    )
    dataset = VisualDataset(metric="SUM", rows=rows)

    ranked = visual_profile_of(dataset, total_minor=None, group_by="MERCHANT", plan_limited=True)
    unranked = visual_profile_of(dataset, total_minor=None, group_by="MERCHANT")

    ranked_spec = DeterministicVisualPlanner().plan(ranked, DEFAULT_PRESENTATION)
    unranked_spec = DeterministicVisualPlanner().plan(unranked, DEFAULT_PRESENTATION)

    assert ranked_spec is not None
    assert unranked_spec is not None
    assert ranked_spec.limit is None
    assert unranked_spec.limit == ROWS_THAT_FIT


def test_a_comparison_of_counts_labels_its_axis_movements() -> None:
    """`dataset.metric` is a shape marker and not the measure.

    A comparison of two *counts* carries `metric="COMPARISON"`, and only the plan says the numbers
    are movements. The floor reading the shape alone put "Importe" on an axis of movement counts
    and turned a cumulative comparison from an area into a bar -- 32 charts in this suite, not one
    of which asserted an axis title. Which is why `plan_metric` exists, and why this test does.
    """
    rows = (
        VisualRow(dimension="agosto", value_minor=12),
        VisualRow(dimension="julio", value_minor=9),
    )

    counted = DeterministicVisualPlanner().plan(
        visual_profile_of(
            VisualDataset(metric="COMPARISON", rows=rows),
            total_minor=None,
            group_by="NONE",
            plan_metric="COUNT",
        ),
        DEFAULT_PRESENTATION,
    )
    running = DeterministicVisualPlanner().plan(
        visual_profile_of(
            VisualDataset(metric="COMPARISON", rows=rows),
            total_minor=None,
            group_by="MONTH",
            plan_metric="CUMULATIVE",
        ),
        DEFAULT_PRESENTATION,
    )

    assert counted is not None and running is not None
    assert [item.title for item in counted.encodings if item.channel == "x"] == ["Movimientos"]
    assert running.mark == "area"


def test_a_series_keeps_the_limit_the_append_used_to_drop() -> None:
    """The colour channel is appended to a spec `derive_chart` already built. The old append
    rebuilt `ChartSpec` field by field -- mark, encodings, stack, sort, responsive -- and so
    silently dropped `limit` on every dataset that had a series. (`sort_field` and `title`
    were never reachable: `derive_chart` never sets `sort_field`, and the mapper applied
    `title` after the call returned.) `dataclasses.replace` is the whole fix."""
    rows = tuple(
        VisualRow(dimension=f"Comercio {position}", value_minor=1_000 - position, series=window)
        for window in ("agosto", "julio")
        for position in range(15)
    )
    profile = visual_profile_of(
        VisualDataset(metric="SUM", rows=rows), total_minor=None, group_by="MERCHANT"
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec is not None
    assert ("color", "series") in {(item.channel, item.field) for item in spec.encodings}
    assert spec.limit == ROWS_THAT_FIT


def test_a_categorical_comparison_groups_its_series_instead_of_stacking_them() -> None:
    """The rubric review names this defect. Stacking two periods adds figures that are not parts
    of one total: the bar's length becomes a number nobody asked for, and it renders cleanly."""
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="GROCERIES", value_minor=100, series="2026-07"),
                VisualRow(dimension="GROCERIES", value_minor=90, series="2026-06"),
                VisualRow(dimension="RESTAURANTS", value_minor=50, series="2026-07"),
                VisualRow(dimension="RESTAURANTS", value_minor=70, series="2026-06"),
            ),
        ),
        total_minor=None,
        group_by="NONE",
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec is not None
    assert spec.stack == "disabled"
    channels = {(e.channel, e.field) for e in spec.encodings}
    assert ("color", "series") in channels
    # The offset must match the dimension's axis: dimension on y means the offset is yOffset.
    dimension_channel = next(e.channel for e in spec.encodings if e.field == "dimension")
    assert (f"{dimension_channel}Offset", "series") in channels


def test_a_temporal_comparison_keeps_its_plain_colour_and_no_offset() -> None:
    """The other side of the `not profile.temporal()` fork, and until now nothing exercised it:
    every `series=` construction in this suite either hit RECURRENCE's own block above or the
    categorical rule this test is not. A coloured line has nothing for an offset to separate --
    each series is already its own line -- and nothing for a disabled stack to mean, so this path
    keeps the plain colour append the categorical rule replaced, unconditionally, as before."""
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="2026-06", value_minor=100, series="2026"),
                VisualRow(dimension="2026-07", value_minor=120, series="2026"),
                VisualRow(dimension="2026-06", value_minor=90, series="2025"),
                VisualRow(dimension="2026-07", value_minor=95, series="2025"),
            ),
        ),
        total_minor=None,
        group_by="MONTH",
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec is not None
    assert spec.mark == "line"
    assert spec.stack is None
    channels = {(e.channel, e.field) for e in spec.encodings}
    assert ("color", "series") in channels
    assert not any(e.channel in {"xOffset", "yOffset"} for e in spec.encodings)


def test_a_normalised_share_is_still_stacked() -> None:
    """RATIO_SHARE early-returns from `plan()` before the comparison rule is ever reached, so its
    `normalize` comes from its own family branch rather than the new rule declining to fire. This
    proves the rule does not over-reach into a chart it was never meant to touch."""
    profile = visual_profile_of(
        VisualDataset(
            metric="RATIO_SHARE",
            rows=(
                VisualRow(dimension="GROCERIES", value_minor=70),
                VisualRow(dimension="RESTAURANTS", value_minor=30),
            ),
        ),
        total_minor=100,
        group_by="NONE",
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec.stack == "normalize"


def test_a_breakdown_sorts_its_categories_by_magnitude() -> None:
    """The second defect the review names. Ten categories in label order put the largest
    anywhere, which is the one thing a breakdown exists to show."""
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="ALQUILER", value_minor=51_300),
                VisualRow(dimension="GROCERIES", value_minor=24_908),
                VisualRow(dimension="ZAPATOS", value_minor=1_200),
            ),
        ),
        total_minor=None,
        group_by="NONE",
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec is not None
    dimension = next(e for e in spec.encodings if e.field == "dimension")
    assert dimension.sort == EncodingSort(mode="descending", field="value_minor")


def test_a_weekday_frequency_keeps_the_order_the_producer_built() -> None:
    """The counter-test, and the reason the rule reads the profile instead of the field type.

    FREQUENCY never reaches the new rule at all: it early-returns from `plan()` with its order
    carried at *spec* level (`ChartSpec.sort="input"`), before a `dimension` local is even
    resolved. So this cannot assert `dimension.sort == EncodingSort(mode="input")` -- that would
    pass only by accident, for a mechanism the rule does not use, and it would fail (for the
    wrong reason) the moment FREQUENCY's dispatch changed shape. What actually holds is the
    early return itself: the chart-wide sort is "input" and the per-encoding sort the new rule
    would have written is simply never written.
    """
    profile = visual_profile_of(
        VisualDataset(
            metric="FREQUENCY",
            rows=(
                VisualRow(dimension="lunes", value_minor=4),
                VisualRow(dimension="martes", value_minor=9),
            ),
        ),
        total_minor=None,
        group_by="NONE",
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec is not None
    assert spec.sort == "input"
    dimension = next(e for e in spec.encodings if e.field == "dimension")
    assert dimension.sort is None


def test_a_temporal_dimension_is_not_sorted_by_magnitude() -> None:
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="2026-01", value_minor=900),
                VisualRow(dimension="2026-02", value_minor=100),
            ),
        ),
        total_minor=None,
        group_by="MONTH",
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec is not None
    dimension = next(e for e in spec.encodings if e.field == "dimension")
    assert dimension.sort is None


def test_sort_for_pins_the_order_to_sort_mapping() -> None:
    """`plan()` can only ever hand `_sort_for` `TEMPORAL` or `MEASURE_DESC`: both `INPUT` metrics
    (FREQUENCY, RECURRENCE) early-return before this rule runs, so the `INPUT` branch is
    unreachable through `plan()` today. It exists for the wave-3 planner, which may hand any
    order, so the mapping is pinned here directly rather than left untested."""
    assert _sort_for("MEASURE_DESC", "value_minor") == EncodingSort(
        mode="descending", field="value_minor"
    )
    assert _sort_for("INPUT", "value_minor") == EncodingSort(mode="input")
    assert _sort_for("TEMPORAL", "value_minor") is None
    assert _sort_for("NONE", "value_minor") is None


def test_a_breakdowns_sort_lands_on_the_axis_and_never_the_tooltip() -> None:
    """Fix round 1, items 1 and 2. `derive_chart` always emits a `tooltip` encoding for the same
    field as the axis, beside the axis encoding itself -- so a filter on `field == dimension.name`
    alone catches both: the axis, which is the point, and the tooltip, which is not. Vega-Lite
    ignores `sort` on a tooltip channel, so a leaked sort there renders identically and is
    invisible on screen -- which is exactly why it has to be read off `present()`'s own wire
    output rather than the domain `EncodingSort`, where it never shows up as a distinguishable
    fact in the first place."""
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="ALQUILER", value_minor=51_300),
                VisualRow(dimension="GROCERIES", value_minor=24_908),
                VisualRow(dimension="ZAPATOS", value_minor=1_200),
            ),
        ),
        total_minor=None,
        group_by="NONE",
    )
    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert spec is not None
    dimension_channel = next(e.channel for e in spec.encodings if e.field == "dimension")

    payload = present(Visualization(spec=spec, data=()))

    assert payload["spec"]["encoding"][dimension_channel]["sort"] == {
        "field": "value_minor",
        "order": "descending",
    }
    tooltip = payload["spec"]["encoding"]["tooltip"]
    assert isinstance(tooltip, list)
    assert all("sort" not in entry for entry in tooltip)


@pytest.mark.parametrize(
    ("metric", "mark", "stack", "sort"),
    (
        ("FREQUENCY", "bar", None, "input"),
        ("RATIO_SHARE", "bar", "normalize", None),
    ),
)
def test_the_families_that_own_their_dimension_keep_their_own_chart(
    metric: str, mark: str, stack: str | None, sort: str | None
) -> None:
    """Three metrics never reach `derive_chart`: they are dispatched before it, exactly as
    `_chart_for_dataset` dispatched them. A calendar `group_by` must not move them."""
    profile = visual_profile_of(
        VisualDataset(
            metric=metric,
            rows=(
                VisualRow(dimension="Parte", value_minor=100),
                VisualRow(dimension="Resto", value_minor=200),
            ),
        ),
        total_minor=None,
        group_by="MONTH",
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec is not None
    assert (spec.mark, spec.stack, spec.sort) == (mark, stack, sort)


def test_a_recurrence_timeline_is_dispatched_before_the_derived_chart() -> None:
    """`x` is the date and `y` is the stream, so the amount is tooltip-only: a recurring charge
    is about *when*, and putting the money on an axis would rank the streams instead."""
    profile = visual_profile_of(
        VisualDataset(
            metric="RECURRENCE",
            rows=(
                VisualRow("Netflix", 1_299, series="MONTHLY", occurred_at="2026-07-01"),
                VisualRow("Netflix", 1_299, series="MONTHLY", occurred_at="2026-08-01"),
            ),
        ),
        total_minor=None,
        group_by="MONTH",
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec is not None
    assert spec.mark == "point"
    assert {(item.channel, item.field) for item in spec.encodings} == {
        ("x", "occurred_at"),
        ("y", "dimension"),
        ("color", "series"),
        ("tooltip", "occurred_at"),
        ("tooltip", "value_minor"),
    }


# --- the wiring, driven rather than asserted ----------------------------------------------------
#
# The decision moved out of `llm_mapper`, so the thing that can rot now is the *call*: a handler
# that stops deciding leaves `visual_decision` at `None`, the mapper draws nothing, and every test
# that hands the mapper a Result it decided itself stays green. So this one drives a real turn.

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 21)


class _Planner:
    def __init__(self, plan: FinancialPlan) -> None:
        self._plan = plan

    async def plan(self, sentence: str, *, context: object) -> PlanningResult:
        return PlanningResult.planned(self._plan)


class _Ledger:
    async def run(
        self,
        plan: object,
        *,
        account_id: uuid.UUID,
        today: date,
        reporting_timezone: str = "Europe/Madrid",
        **kw: object,
    ) -> QueryAnswer:
        return QueryAnswer(
            currency="EUR",
            total_minor=35_293,
            movement_count=5,
            groups=(Group("GROCERIES", 24_908, 3), Group("RESTAURANTS", 10_385, 2)),
        )

    async def ranked(self, ranking: object, **kw: object) -> None:
        return None


class _Accounts:
    async def of(self, account_id: uuid.UUID, **kw: object) -> None:
        raise AssertionError("not this path")


class _Contacts:
    async def all_of(self, account_id: uuid.UUID) -> tuple[()]:
        return ()


class _NoCards:
    async def all_of(self, account_id: uuid.UUID, **kw: object) -> Wallet:
        return Wallet(since=date(2026, 7, 7))

    async def charges_on(self, account_id: uuid.UUID, card_id: object, **kw: object) -> tuple[()]:
        return ()


@pytest.mark.asyncio
async def test_a_real_turn_carries_the_decision_and_the_mapper_draws_it() -> None:
    """The handler decides and the mapper renders. Both halves, on one turn."""
    handler = AskFinancialQuestionHandler(
        _Planner(
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=RelativePeriod(Period.LAST_MONTH),
                group_by=GroupBy.CATEGORY,
                metric=Metric.SUM,
            )
        ),
        _Ledger(),
        accounts=_Accounts(),
        contacts=_Contacts(),
        cards=_NoCards(),
    )

    result = await handler.handle(
        AskFinancialQuestionQuery(
            sentence="¿Cuánto gasté por categoría el mes pasado?",
            account_id=ACCOUNT,
            today=TODAY,
        )
    )

    assert result.visual_dataset is not None
    assert result.visual_decision is not None
    assert result.visual_decision.source == "deterministic"
    assert result.visual_decision.spec.mark == "bar"
    visualization = question_to_dto(result).visualization
    assert visualization is not None and visualization.kind == "chart"
