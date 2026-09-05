"""One test per rule, plus the one that matters most: the floor's own chart verifies."""

import pytest

from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ChartSpec,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedEncoding as E,
)
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedSort as S,
)
from finguard_orchestrator.domain.models.visual_plan import (
    Rejection,
    canonical,
)
from finguard_orchestrator.domain.models.visual_plan import (
    VisualPlanDraft as D,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of


def breakdown():
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))),
        total_minor=None,
        group_by="NONE",
    )


def monthly():
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("2026-01", 100), VisualRow("2026-02", 200))),
        total_minor=None,
        group_by="MONTH",
    )


def comparison():
    return visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow("ALQUILER", 100, series="2026-07"),
                VisualRow("ALQUILER", 90, series="2026-06"),
                VisualRow("OCIO", 50, series="2026-07"),
                VisualRow("OCIO", 70, series="2026-06"),
            ),
        ),
        total_minor=None,
        group_by="NONE",
    )


def share():
    return visual_profile_of(
        VisualDataset(metric="RATIO_SHARE", rows=(VisualRow("Parte", 70), VisualRow("Resto", 30))),
        total_minor=100,
        group_by="NONE",
    )


def frequency():
    return visual_profile_of(
        VisualDataset(metric="FREQUENCY", rows=(VisualRow("lunes", 4), VisualRow("martes", 9))),
        total_minor=None,
        group_by="NONE",
    )


def recurrence_with_series():
    """Mirrors `ask_financial_question.py`'s own construction: one row per recurring charge,
    carrying the counterparty as `dimension`, the cadence as `series`, and the date it last
    landed as `occurred_at`."""
    return visual_profile_of(
        VisualDataset(
            metric="RECURRENCE",
            rows=(
                VisualRow(
                    dimension="Netflix",
                    value_minor=1_299,
                    series="MENSUAL",
                    occurred_at="2026-07-03",
                ),
                VisualRow(
                    dimension="Spotify",
                    value_minor=1_099,
                    series="MENSUAL",
                    occurred_at="2026-07-05",
                ),
            ),
        ),
        total_minor=None,
        group_by="NONE",
    )


def temporal_comparison():
    """A comparison over calendar months: `profile.temporal()` and `profile.comparison` both
    hold at once, the combination `_grouped` must not mistake for a categorical one."""
    return visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow("2026-01", 100, series="A"),
                VisualRow("2026-02", 200, series="A"),
            ),
        ),
        total_minor=None,
        group_by="MONTH",
    )


def twelve_row_breakdown():
    """More rows than `ROWS_THAT_FIT`, so the floor's own chart carries a `limit`."""
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=tuple(VisualRow(f"CAT_{i}", 100 + i) for i in range(12))),
        total_minor=None,
        group_by="NONE",
    )


def ranked_bar(**overrides):
    fields = dict(
        mark="bar",
        encodings=(
            E("y", "dimension", "nominal", S("descending", "value_minor")),
            E("x", "value_minor", "quantitative"),
        ),
    )
    fields.update(overrides)
    return D(**fields)


def rule_of(result) -> str | None:
    return result.rule if isinstance(result, Rejection) else None


_FLOOR_FAMILIES = {
    "breakdown": breakdown,
    "temporal_line": monthly,
    "frequency": frequency,
    "ratio_share": share,
    "recurrence_with_series": recurrence_with_series,
    "grouped_comparison": comparison,
    "temporal_comparison": temporal_comparison,
    "limited_breakdown": twelve_row_breakdown,
}


@pytest.mark.parametrize("build_profile", _FLOOR_FAMILIES.values(), ids=_FLOOR_FAMILIES.keys())
def test_the_floors_own_chart_verifies_as_itself(build_profile) -> None:
    """If the verifier refused what the floor draws, the baseline would be uncomparable.

    Every family the floor can emit, not just the plain breakdown a first pass tested: the
    normalised RATIO_SHARE bar, RECURRENCE's point chart with a series, a categorical grouped
    comparison, a temporal one, and a breakdown large enough to carry the floor's own `limit`.
    """
    profile = build_profile()
    floor = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert floor is not None
    draft = D(
        floor.mark,
        tuple(
            E(e.channel, e.field, e.type, S(e.sort.mode, e.sort.field) if e.sort else None)
            for e in floor.encodings
        ),
        sort=floor.sort,
        sort_field=floor.sort_field,
        stack=floor.stack,
        limit=floor.limit,
    )
    verified = verify(draft, profile)
    assert isinstance(verified, ChartSpec)
    assert canonical(verified) == canonical(floor)


def test_structure_is_the_first_rule() -> None:
    assert rule_of(verify(ranked_bar(mark="sideways"), breakdown())) == "structure"
    assert (
        rule_of(verify(ranked_bar(encodings=(E("zOffset", "dimension", "nominal"),)), breakdown()))
        == "structure"
    )


def test_an_absent_field_and_a_wrong_type_are_refused() -> None:
    assert (
        rule_of(
            verify(
                ranked_bar(
                    encodings=(
                        E("y", "categoria", "nominal"),
                        E("x", "value_minor", "quantitative"),
                    )
                ),
                breakdown(),
            )
        )
        == "fields"
    )
    assert (
        rule_of(
            verify(
                ranked_bar(
                    encodings=(
                        E("y", "dimension", "quantitative"),
                        E("x", "value_minor", "quantitative"),
                    )
                ),
                breakdown(),
            )
        )
        == "types"
    )


def test_the_matrix_refuses_a_measure_in_color() -> None:
    draft = D(
        "bar",
        (
            E("y", "dimension", "nominal"),
            E("x", "value_minor", "quantitative"),
            E("color", "value_minor", "quantitative"),
        ),
    )
    assert rule_of(verify(draft, breakdown())) == "matrix"


def test_a_bar_needs_opposite_axes_and_a_line_needs_time() -> None:
    same_axis = D(
        "bar",
        (
            E("x", "dimension", "nominal", S("descending", "value_minor")),
            E("y", "value_minor", "quantitative"),
            E("tooltip", "dimension", "nominal"),
        ),
    )
    assert isinstance(verify(same_axis, breakdown()), ChartSpec)  # x/y either way round is fine
    both_on_x = D(
        "bar", (E("x", "dimension", "nominal"), E("tooltip", "value_minor", "quantitative"))
    )
    assert rule_of(verify(both_on_x, breakdown())) == "mark"
    line_over_categories = D(
        "line", (E("x", "dimension", "nominal"), E("y", "value_minor", "quantitative"))
    )
    assert rule_of(verify(line_over_categories, breakdown())) == "mark"
    line_over_months = D(
        "line", (E("x", "dimension", "temporal"), E("y", "value_minor", "quantitative"))
    )
    assert isinstance(verify(line_over_months, monthly()), ChartSpec)


def test_point_refuses_the_amount_off_tooltip_and_a_non_nominal_y() -> None:
    accepted = D(
        "point",
        (
            E("x", "occurred_at", "temporal"),
            E("y", "dimension", "nominal"),
            E("tooltip", "value_minor", "quantitative"),
        ),
    )
    assert isinstance(verify(accepted, recurrence_with_series()), ChartSpec)
    amount_in_theta = D(
        "point",
        (
            E("x", "occurred_at", "temporal"),
            E("y", "dimension", "nominal"),
            E("theta", "value_minor", "quantitative"),
        ),
    )
    assert rule_of(verify(amount_in_theta, recurrence_with_series())) == "mark"
    non_nominal_y = D(
        "point",
        (
            E("x", "occurred_at", "temporal"),
            E("y", "occurred_at", "temporal"),
            E("tooltip", "value_minor", "quantitative"),
        ),
    )
    assert rule_of(verify(non_nominal_y, recurrence_with_series())) == "mark"


def test_a_donut_needs_the_three_claims() -> None:
    # RATIO_SHARE derives MEASURE_DESC for its dimension, so the legend carries the ranking too.
    donut = D(
        "donut",
        (
            E("theta", "value_minor", "quantitative"),
            E("color", "dimension", "nominal", S("descending", "value_minor")),
        ),
    )
    assert isinstance(verify(donut, share()), ChartSpec)
    assert rule_of(verify(donut, breakdown())) == "donut_claims"  # no verified total


def test_a_grouped_comparison_needs_offset_and_disabled_stack() -> None:
    grouped = D(
        "bar",
        (
            E("y", "dimension", "nominal", S("descending", "value_minor")),
            E("x", "value_minor", "quantitative"),
            E("color", "series", "nominal", S("input")),
            E("yOffset", "series", "nominal", S("input")),
        ),
        stack="disabled",
    )
    assert isinstance(verify(grouped, comparison()), ChartSpec)
    assert (
        rule_of(verify(D(grouped.mark, grouped.encodings, stack=None), comparison())) == "grouped"
    )
    wrong_axis = D(
        grouped.mark,
        grouped.encodings[:3] + (E("xOffset", "series", "nominal", S("input")),),
        stack="disabled",
    )
    assert rule_of(verify(wrong_axis, comparison())) == "grouped"


def test_limit_belongs_to_one_family() -> None:
    assert isinstance(verify(ranked_bar(limit=5), breakdown()), ChartSpec)
    assert (
        rule_of(
            verify(
                D(
                    "line",
                    (E("x", "dimension", "temporal"), E("y", "value_minor", "quantitative")),
                    limit=5,
                ),
                monthly(),
            )
        )
        == "limit"
    )


def test_order_follows_the_profile_not_the_type() -> None:
    by_value = D(
        "bar",
        (
            E("x", "dimension", "nominal", S("descending", "value_minor")),
            E("y", "value_minor", "quantitative"),
        ),
    )
    assert rule_of(verify(by_value, frequency())) == "order"
    in_order = D(
        "bar", (E("x", "dimension", "nominal", S("input")), E("y", "value_minor", "quantitative"))
    )
    assert isinstance(verify(in_order, frequency()), ChartSpec)
    unranked = D("bar", (E("y", "dimension", "nominal"), E("x", "value_minor", "quantitative")))
    assert rule_of(verify(unranked, breakdown())) == "order"  # MEASURE_DESC wants the ranking
