"""One test per rule, plus the one that matters most: the floor's own chart verifies."""

import pytest

from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ChartSpec,
    VisualDataset,
    Visualization,
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
from finguard_orchestrator.infrastructure.visualization.vega_lite import present


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
            metric="COMPARISON",
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
            metric="COMPARISON",
            rows=(
                VisualRow("2026-01", 100, series="A"),
                VisualRow("2026-02", 200, series="A"),
            ),
        ),
        total_minor=None,
        group_by="MONTH",
    )


def verified_breakdown():
    """A breakdown whose total was checked, not a RATIO_SHARE dataset -- probe B (C1) needs
    `verified_total=True` without the metric that already forces it, so the two probes exercise
    two different profiles the way the branch review's own reproduction does."""
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("ALQUILER", 60), VisualRow("OCIO", 40))),
        total_minor=100,
        group_by="NONE",
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


def _draft_from_spec(spec: ChartSpec) -> D:
    return D(
        spec.mark,
        tuple(
            E(e.channel, e.field, e.type, S(e.sort.mode, e.sort.field) if e.sort else None)
            for e in spec.encodings
        ),
        sort=spec.sort,
        sort_field=spec.sort_field,
        stack=spec.stack,
        limit=spec.limit,
    )


@pytest.mark.parametrize("build_profile", _FLOOR_FAMILIES.values(), ids=_FLOOR_FAMILIES.keys())
def test_every_authorised_spec_survives_present(build_profile) -> None:
    """C1's converse property, missing for eight task reviews: `verify()` returning a `ChartSpec`
    is not enough on its own -- it must also be something `present()` (`vega_lite.py`) can
    actually draw. Parametrised over every family the floor can emit, the same set
    `test_the_floors_own_chart_verifies_as_itself` uses."""
    profile = build_profile()
    floor = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert floor is not None
    verified = verify(_draft_from_spec(floor), profile)
    assert isinstance(verified, ChartSpec)
    present(Visualization(spec=verified, data=()))  # must not raise


_C1_PROBES = {
    # Probe A: a RATIO_SHARE profile's normalised bar -- the measure on `x`, the dimension in
    # `color`, no axis left for a chart-wide sort to land on.
    "ratio_share_normalised_bar": (
        share,
        D(
            "bar",
            (E("x", "value_minor", "quantitative"), E("color", "dimension", "nominal")),
            stack="normalize",
            sort="descending",
            sort_field="value_minor",
        ),
    ),
    # Probe B: a verified-total breakdown drawn as a donut -- `theta`/`color`, no axes at all.
    "verified_breakdown_donut": (
        verified_breakdown,
        D(
            "donut",
            (E("theta", "value_minor", "quantitative"), E("color", "dimension", "nominal")),
            sort="descending",
            sort_field="value_minor",
        ),
    ),
}


@pytest.mark.parametrize(("build_profile", "draft"), _C1_PROBES.values(), ids=_C1_PROBES.keys())
def test_c1_probes_are_rejected_instead_of_authorising_an_unrenderable_chart(
    build_profile, draft
) -> None:
    """The two probes the branch review reproduced at HEAD: both used to `verify()` into a
    `ChartSpec` that `present()` then refused with `ValueError("a sorted chart needs a
    categorical or temporal dimension")`. Both must now be `Rejection`s."""
    assert isinstance(verify(draft, build_profile()), Rejection)


def test_structure_is_the_first_rule() -> None:
    assert rule_of(verify(ranked_bar(mark="sideways"), breakdown())) == "structure"
    assert (
        rule_of(verify(ranked_bar(encodings=(E("zOffset", "dimension", "nominal"),)), breakdown()))
        == "structure"
    )


def test_a_type_error_shaped_draft_becomes_structure_not_an_exception() -> None:
    """Catching is not repairing: `field=None`, `limit="5"` and `encodings=None` raise
    `TypeError` rather than `ValueError`, and used to escape `verify` as exceptions."""
    assert rule_of(verify(ranked_bar(limit="5"), breakdown())) == "structure"


def test_an_unrecognised_stack_or_sort_is_refused() -> None:
    """Fix round 2, item 7. `ChartSpec.__post_init__` checked `mark` against its literal values
    but never `stack` against `get_args(Stack)` or `sort` against `get_args(SortMode)`, so an
    unrecognised value passed `_structure` and reached the wire verbatim -- the DTO refuses this
    on the wire path (`stack in get_args(Stack)`), but the model-proposed path relied on a check
    at `ChartSpec` that was not there."""
    assert rule_of(verify(ranked_bar(stack="upside_down"), breakdown())) == "structure"
    assert rule_of(verify(ranked_bar(sort="sideways"), breakdown())) == "structure"


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
    # Refused via the top-of-function "theta belongs only to a donut" check, not the point
    # family's own rule below -- `theta` never reaches a non-donut mark's own checks at all.
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


def _normalised_bar(**overrides):
    fields = dict(
        mark="bar",
        encodings=(E("x", "value_minor", "quantitative"), E("color", "dimension", "nominal")),
        stack="normalize",
    )
    fields.update(overrides)
    return D(**fields)


def verified_monthly():
    """A temporal profile whose total was checked -- I2's probe needs `positive`,
    `mutually_exclusive` and `verified_total` all true on a *temporal* dimension, which `monthly`
    above does not carry (its `total_minor` is `None`, so `verified_total` is false)."""
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("2026-01", 100), VisualRow("2026-02", 200))),
        total_minor=300,
        group_by="MONTH",
    )


def test_a_normalised_line_or_area_is_refused_normalize_composes_a_bar() -> None:
    """I2 (= D3). `_mark`'s `line`/`area` branch checked axes, temporality and roles but never
    `stack`, so a normalised line on a qualifying temporal profile was authorised -- the only
    shape that can qualify with no series is a single-series 100% band that says nothing, and on
    `line` the stack is simply inert."""
    normalised_line = D(
        "line",
        (E("x", "dimension", "temporal"), E("y", "value_minor", "quantitative")),
        stack="normalize",
    )
    assert rule_of(verify(normalised_line, verified_monthly())) == "mark"
    normalised_area = D(
        "area",
        (E("x", "dimension", "temporal"), E("y", "value_minor", "quantitative")),
        stack="normalize",
    )
    assert rule_of(verify(normalised_area, verified_monthly())) == "mark"


def test_a_normalised_bar_claims_the_same_thing_a_donut_does() -> None:
    """Fix round 2, item 3, paired with item 2: both are "the normalised bar is a composition".
    `mark != "donut"` alone let a breakdown with `verified_total=False` -- refused as a donut for
    exactly that -- draw as a normalised bar instead, and a comparison's series are not disjoint
    parts of one whole (`mutually_exclusive=False`) yet still composed into one 100% bar."""
    assert rule_of(verify(_normalised_bar(), breakdown())) == "donut_claims"  # no verified total
    assert rule_of(verify(_normalised_bar(), comparison())) == "donut_claims"  # not exclusive


def test_a_normalised_bar_refuses_the_wrong_shape() -> None:
    """Fix round 2, item 6: no test set `stack="normalize"` and expected `_mark`'s new arm to
    refuse it. Both axes populated is the opposite of "no opposite axis"."""
    both_axes = D(
        "bar",
        (E("x", "value_minor", "quantitative"), E("y", "dimension", "nominal")),
        stack="normalize",
    )
    assert rule_of(verify(both_axes, share())) == "mark"


def test_limit_refuses_a_normalised_bar() -> None:
    """Fix round 2, item 2, paired with item 3. A normalised bar is a `bar`, so `mark != "bar"`
    alone let `limit` through onto it too -- the presenter's rank-and-filter transform over a
    composition, exactly the row Section D's limit table names."""
    assert rule_of(verify(_normalised_bar(limit=3), share())) == "limit"


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


def test_two_offsets_are_refused() -> None:
    """Only `offsets[0]` used to be compared against `color`, so a draft carrying both
    `xOffset` and `yOffset` slipped through with the second one unchecked."""
    both_offsets = D(
        "bar",
        (
            E("y", "dimension", "nominal", S("descending", "value_minor")),
            E("x", "value_minor", "quantitative"),
            E("color", "series", "nominal", S("input")),
            E("xOffset", "series", "nominal", S("input")),
            E("yOffset", "series", "nominal", S("input")),
        ),
        stack="disabled",
    )
    assert rule_of(verify(both_offsets, comparison())) == "grouped"


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


def test_limit_refuses_a_series_and_an_unranked_dimension() -> None:
    """`test_limit_belongs_to_one_family` only ever exercised `mark != "bar"`; the other two ways
    to fail "categorical bar, single series, MEASURE_DESC only" want their own drafts."""
    with_series = D(
        "bar",
        (
            E("y", "dimension", "nominal", S("descending", "value_minor")),
            E("x", "value_minor", "quantitative"),
            E("color", "series", "nominal", S("input")),
            E("yOffset", "series", "nominal", S("input")),
        ),
        stack="disabled",
        limit=5,
    )
    assert rule_of(verify(with_series, comparison())) == "limit"
    not_measure_desc = D(
        "bar",
        (E("x", "dimension", "nominal", S("input")), E("y", "value_minor", "quantitative")),
        limit=5,
    )
    assert rule_of(verify(not_measure_desc, frequency())) == "limit"


@pytest.mark.parametrize(("plan_limited", "expected_rule"), [(False, None), (True, "limit")])
def test_limit_refuses_a_ranking_the_plan_already_limited(
    plan_limited: bool, expected_rule: str | None
) -> None:
    """Fix round 1, finding 5b: the model cannot be trusted to obey the prompt's "do not limit
    again" prose on its own -- a domain rule can. Same draft both times; only `plan_limited`
    changes, so this isolates the one new branch from the family checks above it."""
    profile = visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))),
        total_minor=None,
        group_by="NONE",
        plan_limited=plan_limited,
    )
    result = verify(ranked_bar(limit=5), profile)
    assert rule_of(result) == expected_rule
    if expected_rule is None:
        assert isinstance(result, ChartSpec)


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


def test_measure_desc_off_axis_may_omit_its_sort_but_not_contradict_it() -> None:
    """Fix round 2, item 4. `if held.order == "MEASURE_DESC" and is_axis_dimension:` headed an
    `elif` chain, so an off-axis field fell out with no check at all when it had a sort of its
    own: a legend ranked ascending -- backwards from what `MEASURE_DESC` means -- was
    unrefusable, on a normalised bar and on a donut alike."""
    backwards_bar = _normalised_bar(
        encodings=(
            E("x", "value_minor", "quantitative"),
            E("color", "dimension", "nominal", S("ascending", "value_minor")),
        )
    )
    assert rule_of(verify(backwards_bar, share())) == "order"
    backwards_donut = D(
        "donut",
        (
            E("theta", "value_minor", "quantitative"),
            E("color", "dimension", "nominal", S("ascending", "value_minor")),
        ),
    )
    assert rule_of(verify(backwards_donut, share())) == "order"


def test_frequency_with_no_sort_at_all_is_refused() -> None:
    """Fix round 1, I3: `INPUT` requires an explicit `input` sort, or Vega-Lite alphabetises the
    weekday axis -- domingo, jueves, lunes -- destroying the only thing the chart is for."""
    no_sort = D("bar", (E("x", "dimension", "nominal"), E("y", "value_minor", "quantitative")))
    assert rule_of(verify(no_sort, frequency())) == "order"


def test_a_temporal_axis_refuses_a_chart_wide_sort_by_another_field() -> None:
    """Fix round 1, I3: a monthly line ranked chart-wide by magnitude is the same "an explicit
    sort on another field is refused" the per-encoding branch already enforces, read off the
    spec-wide property instead of the encoding's own."""
    ranked_wide = D(
        "line",
        (E("x", "dimension", "temporal"), E("y", "value_minor", "quantitative")),
        sort="descending",
        sort_field="value_minor",
    )
    assert rule_of(verify(ranked_wide, monthly())) == "order"


def test_a_chart_wide_sort_targets_the_presenters_axis_pick_not_sort_fields_name() -> None:
    """Fix round 2, item 1. `present()` (`vega_lite.py`) picks the first nominal/temporal x/y
    encoding and writes the chart-wide sort onto it regardless of what `sort_field` names --
    `sort_field` is the key to sort *by*, never the target. Comparing `sort_field` against
    `item.field` (fix round 1's reading) let a chart-wide sort naming a real, different field
    (`occurred_at`) read as not targeting `dimension` at all, though the presenter lands it there:
    `dimension` is listed first and is nominal, so it is the presenter's pick, and its `NONE`
    order refuses being targeted regardless of what the sort ranks by."""
    reproducer = D(
        "point",
        (
            E("y", "dimension", "nominal"),
            E("x", "occurred_at", "temporal"),
            E("tooltip", "value_minor", "quantitative"),
        ),
        sort="descending",
        sort_field="occurred_at",
    )
    assert rule_of(verify(reproducer, recurrence_with_series())) == "order"


def test_none_refuses_a_sort_whether_per_encoding_or_chart_wide() -> None:
    """Fix round 1, I3: RECURRENCE's `y` dimension is `NONE` (Section D puts the whole `point`
    family there), so neither an encoding's own sort nor the chart-wide one may target it."""
    encoding_sort = D(
        "point",
        (
            E("x", "occurred_at", "temporal"),
            E("y", "dimension", "nominal", S("descending", "value_minor")),
            E("tooltip", "value_minor", "quantitative"),
        ),
    )
    assert rule_of(verify(encoding_sort, recurrence_with_series())) == "order"
    # No `sort_field`: the presenter's fallback then targets *every* nominal/temporal axis field
    # at once (`occurred_at` and `dimension` both qualify on a `point` chart), so `occurred_at`'s
    # own `TEMPORAL` order reads this as sorting itself -- fine -- while `dimension`'s `NONE`
    # still refuses being targeted at all, isolating the one rejection this test is for.
    chart_wide_sort = D(
        "point",
        (
            E("x", "occurred_at", "temporal"),
            E("y", "dimension", "nominal"),
            E("tooltip", "value_minor", "quantitative"),
        ),
        sort="descending",
    )
    assert rule_of(verify(chart_wide_sort, recurrence_with_series())) == "order"


def test_a_series_keeps_its_own_order_even_when_the_dimension_ranks() -> None:
    """Fix round 1, I2: the series' order is not the dimension's. A comparison whose legend
    carries a descending-by-value sort is authorised today, though `series` fields are always
    `INPUT` -- a declared order the model may not substitute a magnitude ranking for."""
    grouped = D(
        "bar",
        (
            E("y", "dimension", "nominal", S("descending", "value_minor")),
            E("x", "value_minor", "quantitative"),
            E("color", "series", "nominal", S("descending", "value_minor")),
            E("yOffset", "series", "nominal", S("input")),
        ),
        stack="disabled",
    )
    assert rule_of(verify(grouped, comparison())) == "order"


def test_an_unknown_sort_field_is_refused() -> None:
    """Fix round 1, I4: a sort field is a field too. `sort_field`/`sort.field` naming a column
    the profile does not have reaches the presenter the same way an unknown encoding field would,
    and `merge_encoding_patch` already refuses exactly that on the user-patch path."""
    unknown_chart_wide = ranked_bar(sort="input", sort_field="mercadona_gasto_4500")
    assert rule_of(verify(unknown_chart_wide, breakdown())) == "fields"
    unknown_per_encoding = D(
        "bar",
        (
            E("y", "dimension", "nominal", S("descending", "mercadona_gasto_4500")),
            E("x", "value_minor", "quantitative"),
        ),
    )
    assert rule_of(verify(unknown_per_encoding, breakdown())) == "fields"
