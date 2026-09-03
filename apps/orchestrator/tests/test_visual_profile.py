import pytest

from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    EncodingPatch,
    VisualDataset,
    VisualRow,
    merge_encoding_patch,
)
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of


def test_a_breakdown_profile_names_its_dimension_and_measure() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="GROCERIES", value_minor=24_908),
            VisualRow(dimension="RESTAURANTS", value_minor=10_385),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=35_293, group_by=None)

    assert profile.field("dimension").role == "dimension"
    assert profile.field("dimension").cardinality == 2
    assert profile.field("dimension").order == "MEASURE_DESC"
    assert profile.field("value_minor").role == "measure"
    assert profile.unit == "money"
    assert profile.positive is True
    assert profile.verified_total is True  # the rows sum to the stated total
    assert profile.comparison is False


def test_a_series_gets_its_own_cardinality_and_makes_this_a_comparison() -> None:
    """Two series are a comparison; the count is what later refuses fifteen of them."""
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="GROCERIES", value_minor=100, series="2026-07"),
            VisualRow(dimension="GROCERIES", value_minor=90, series="2026-06"),
            VisualRow(dimension="RESTAURANTS", value_minor=50, series="2026-07"),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None, group_by=None)

    assert profile.comparison is True
    assert profile.field("series").role == "series"
    assert profile.field("series").cardinality == 2
    assert profile.field("dimension").cardinality == 2
    assert profile.mutually_exclusive is False  # series means the rows are not disjoint parts


def test_a_frequency_dimension_keeps_the_order_the_dataset_built() -> None:
    """FREQUENCY is weekday names: lunes precedes martes because the producer said so, and
    sorting them by magnitude would destroy the only thing the chart is for."""
    dataset = VisualDataset(
        metric="FREQUENCY",
        rows=(
            VisualRow(dimension="lunes", value_minor=4),
            VisualRow(dimension="martes", value_minor=9),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None, group_by=None)

    assert profile.field("dimension").order == "INPUT"
    assert profile.unit == "count"


def test_a_temporal_dimension_is_ordered_temporally() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="2026-01", value_minor=100),
            VisualRow(dimension="2026-02", value_minor=200),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None, group_by="MONTH")

    assert profile.field("dimension").type == "temporal"
    assert profile.field("dimension").order == "TEMPORAL"
    assert profile.temporal() is True


def test_a_recurrence_dataset_carries_two_dimensions() -> None:
    dataset = VisualDataset(
        metric="RECURRENCE",
        rows=(
            VisualRow(dimension="Netflix", value_minor=1_299, occurred_at="2026-07-03"),
            VisualRow(dimension="Spotify", value_minor=1_099, occurred_at="2026-07-05"),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None, group_by=None)

    assert {item.name for item in profile.role("dimension")} == {"dimension", "occurred_at"}
    assert profile.field("occurred_at").type == "temporal"
    assert profile.field("occurred_at").order == "TEMPORAL"


def test_a_profile_carries_no_value_from_any_row() -> None:
    """The whole point of the type. A sentinel, because the profile legitimately holds counts."""
    dataset = VisualDataset(
        metric="SUM",
        rows=(VisualRow(dimension="GROCERIES", value_minor=734_291),),
    )

    profile = visual_profile_of(dataset, total_minor=734_291, group_by=None)

    assert "734291" not in repr(profile)
    assert "GROCERIES" not in repr(profile)


def test_a_month_grouped_dimension_is_temporal_and_a_frequency_one_is_not() -> None:
    """The signal is not on the dataset: `_chart_for_dataset` reads it from `plan_context`.
    Nothing exercised this path through the mapper, which is why dropping it would have been
    silent -- a month breakdown would have become a bar and no test would have said so."""
    rows = (
        VisualRow(dimension="2026-01", value_minor=100),
        VisualRow(dimension="2026-02", value_minor=200),
    )

    monthly = visual_profile_of(
        VisualDataset(rows=rows, metric="SUM"), total_minor=None, group_by="MONTH"
    )
    assert monthly.field("dimension").type == "temporal"
    assert monthly.field("dimension").order == "TEMPORAL"
    assert monthly.temporal() is True

    frequency = visual_profile_of(
        VisualDataset(
            rows=(
                VisualRow(dimension="lunes", value_minor=4),
                VisualRow(dimension="martes", value_minor=9),
            ),
            metric="FREQUENCY",
        ),
        total_minor=None,
        group_by="MONTH",
    )
    assert frequency.field("dimension").type == "nominal"
    assert frequency.field("dimension").order == "INPUT"


def _bar_spec() -> ChartSpec:
    return ChartSpec(
        "bar",
        (
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("y", "dimension", "nominal"),
        ),
    )


def test_a_donut_still_refuses_more_than_five_parts() -> None:
    """The bound moved from the profile to the nominal dimension's field. Same number, and this
    is what says so."""
    six = visual_profile_of(
        VisualDataset(
            rows=tuple(VisualRow(dimension=f"C{n}", value_minor=10) for n in range(6)),
            metric="SUM",
        ),
        total_minor=60,
        group_by="NONE",
    )
    with pytest.raises(ValueError, match="between 2 and 5"):
        merge_encoding_patch(_bar_spec(), EncodingPatch(mark="donut"), six)
