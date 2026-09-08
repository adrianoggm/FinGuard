import importlib.util
import sys
from pathlib import Path

import pytest

from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    DrawnLabels,
    EncodingPatch,
    VisualDataset,
    VisualRow,
    merge_encoding_patch,
)
from finguard_orchestrator.domain.services.visual_profile import drawn_labels, visual_profile_of

# Loaded the way `test_visualization_stage1.py` loads the same script: `scripts/` is not a
# package, so the exporter's own dataset+kwargs pairs -- the ones the web compile gate renders --
# are read off the file directly rather than duplicated here.
_EXPORTER_SPEC = importlib.util.spec_from_file_location(
    "export_chart_fixtures",
    Path(__file__).resolve().parents[1] / "scripts" / "export_chart_fixtures.py",
)
assert _EXPORTER_SPEC and _EXPORTER_SPEC.loader
export_chart_fixtures = importlib.util.module_from_spec(_EXPORTER_SPEC)
sys.modules["export_chart_fixtures"] = export_chart_fixtures
_EXPORTER_SPEC.loader.exec_module(export_chart_fixtures)
FAMILY_DATASETS = export_chart_fixtures.FAMILY_DATASETS


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
    """A COMPARISON dataset's series is the comparison; the count is what later refuses fifteen
    of them."""
    dataset = VisualDataset(
        metric="COMPARISON",
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


def test_a_recurrence_with_series_is_not_a_comparison() -> None:
    """RECURRENCE uses `series` for the cadence. Deriving `comparison` from the presence of a
    series told the model two contradictory things at once -- "grouped bars" and "points on
    occurred_at". The shape marker is the authority."""
    dataset = VisualDataset(
        metric="RECURRENCE",
        rows=(
            VisualRow(
                dimension="Netflix", value_minor=1_299, series="MONTHLY", occurred_at="2026-07-03"
            ),
            VisualRow(
                dimension="Spotify", value_minor=1_099, series="MONTHLY", occurred_at="2026-07-05"
            ),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None, group_by=None)

    assert profile.comparison is False
    assert profile.mutually_exclusive is False  # a series still means slices of one dimension


def test_a_series_alone_does_not_make_a_comparison() -> None:
    """The marker decides, not the rows: a dataset nobody marked as a comparison is not one."""
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="GROCERIES", value_minor=100, series="2026-07"),
            VisualRow(dimension="GROCERIES", value_minor=90, series="2026-06"),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None, group_by=None)

    assert profile.comparison is False
    assert profile.mutually_exclusive is False


def test_a_comparison_dataset_is_a_comparison_by_its_marker() -> None:
    dataset = VisualDataset(
        metric="COMPARISON",
        rows=(
            VisualRow(dimension="GROCERIES", value_minor=100, series="2026-07"),
            VisualRow(dimension="GROCERIES", value_minor=90, series="2026-06"),
        ),
    )

    assert visual_profile_of(dataset, total_minor=None, group_by=None).comparison is True


def test_a_comparison_of_two_totals_without_a_series_is_not_grouped() -> None:
    """The handler's ungrouped comparison: two rows, one per period, no series. Two bars, not a
    grouped chart -- there is no series field to put on color, and the floor's grouped block
    would index one that does not exist."""
    dataset = VisualDataset(
        metric="COMPARISON",
        rows=(
            VisualRow(dimension="agosto", value_minor=12),
            VisualRow(dimension="julio", value_minor=9),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None, group_by="NONE", plan_metric="COUNT")

    assert profile.comparison is False
    assert profile.role("series") == ()


# --- Task 1 (Wave 4.3): a dataset knows the labels it draws -------------------------------------

# One expectation per family the exporter renders -- the same families the web compile gate
# checks -- so a family added later to `FAMILY_DATASETS` without an entry here fails loudly
# (`KeyError` on the missing key) instead of silently going unmeasured.
_EXPECTED_DRAWN_LABELS: dict[str, DrawnLabels] = {
    "breakdown": DrawnLabels(labels=("ALQUILER", "GROCERIES", "ZAPATOS"), ranked=True),
    "temporal": DrawnLabels(labels=("2026-02", "2026-03", "2026-01"), ranked=True),
    "cumulative": DrawnLabels(labels=("2026-03", "2026-02", "2026-01"), ranked=True),
    "frequency": DrawnLabels(labels=("Vie", "Mar", "Jue", "Lun", "Mié", "Sáb", "Dom"), ranked=True),
    # Netflix appears twice (two months) and sums to 2598; Spotify appears once at 999.
    "recurrence": DrawnLabels(
        labels=("Netflix", "Spotify"), series_labels=("MONTHLY",), ranked=True
    ),
    "ratio_share": DrawnLabels(labels=("Parte", "Resto"), ranked=True),
    # GROCERIES sums 100+90=190 over both windows, RESTAURANTS sums 50+70=120.
    "categorical_comparison": DrawnLabels(
        labels=("GROCERIES", "RESTAURANTS"), series_labels=("2026-07", "2026-06"), ranked=True
    ),
}


@pytest.mark.parametrize("family_name", sorted(FAMILY_DATASETS))
def test_every_family_dataset_knows_the_labels_it_draws(family_name: str) -> None:
    """Every family the exporter renders -- the same ones the web compile gate checks -- because
    the property this function exists for ("the labels match the bars the chart actually draws")
    is exactly the one a single hand-picked fixture cannot prove for every shape a real dataset
    takes: a plain sum, a temporal series, a cumulative one, an INPUT-ordered weekday list, a
    dimension summed across repeated rows (recurrence), a two-row share, and a grouped comparison
    whose ranking is by the total across both windows."""
    dataset, _ = FAMILY_DATASETS[family_name]
    assert drawn_labels(dataset) == _EXPECTED_DRAWN_LABELS[family_name]


def test_a_tie_at_the_top_is_not_ranked_but_keeps_first_appearance_order() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="B", value_minor=10),
            VisualRow(dimension="A", value_minor=10),
            VisualRow(dimension="C", value_minor=5),
        ),
    )
    result = drawn_labels(dataset)
    assert result.labels == ("B", "A", "C")
    assert result.ranked is False


def test_a_tie_at_the_bottom_only_is_not_ranked() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="A", value_minor=10),
            VisualRow(dimension="B", value_minor=5),
            VisualRow(dimension="C", value_minor=5),
        ),
    )
    result = drawn_labels(dataset)
    assert result.labels == ("A", "B", "C")
    assert result.ranked is False


def test_a_tie_in_the_middle_only_is_still_ranked() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="A", value_minor=10),
            VisualRow(dimension="B", value_minor=5),
            VisualRow(dimension="C", value_minor=5),
            VisualRow(dimension="D", value_minor=1),
        ),
    )
    result = drawn_labels(dataset)
    assert result.labels == ("A", "B", "C", "D")
    assert result.ranked is True


def test_one_row_is_ranked() -> None:
    dataset = VisualDataset(metric="SUM", rows=(VisualRow(dimension="A", value_minor=10),))
    assert drawn_labels(dataset) == DrawnLabels(labels=("A",), ranked=True)


def test_an_empty_dataset_draws_no_labels() -> None:
    dataset = VisualDataset(metric="SUM", rows=())
    assert drawn_labels(dataset) == DrawnLabels()


def test_101_distinct_dimensions_are_capped_at_100_and_left_unranked() -> None:
    """The true bottom label falls off the list once the cap cuts it, so `ranked` cannot claim
    one -- `label[100-1]` is not `dataset`'s actual last bar any more."""
    dataset = VisualDataset(
        metric="SUM",
        rows=tuple(VisualRow(dimension=f"D{n}", value_minor=101 - n) for n in range(101)),
    )
    result = drawn_labels(dataset)
    assert len(result.labels) == 100
    assert result.labels[:3] == ("D0", "D1", "D2")
    assert result.ranked is False
