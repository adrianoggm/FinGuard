"""The proposal vocabulary: plain strings on purpose, and the equality the baseline uses."""

import pytest

from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    EncodingSort,
    SortMode,
    Stack,
)
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedEncoding,
    ProposedSort,
    Rejection,
    VisualPlanDraft,
    canonical,
)


def test_a_draft_accepts_any_string_because_the_domain_refuses_later() -> None:
    """`ChartSpec.__post_init__` is where "sideways" gets refused. A draft that raised here would
    turn a model error into an adapter exception, which is the split this type exists to prevent."""
    draft = VisualPlanDraft(
        mark="sideways",
        encodings=(
            ProposedEncoding("zOffset", "dimension", "nominal", sort=ProposedSort(mode="sideways")),
        ),
        stack="upside_down",
    )
    assert draft.mark == "sideways"
    assert draft.encodings[0].channel == "zOffset"
    assert draft.encodings[0].sort is not None
    assert draft.encodings[0].sort.mode == "sideways"


def test_a_rejection_names_its_rule() -> None:
    rejection = Rejection(rule="matrix", reason="a measure never goes in color")
    assert rejection.rule == "matrix"


def _bar(*, titled: bool, order: tuple[int, ...] = (0, 1, 2)) -> ChartSpec:
    encodings = (
        ChartEncoding(
            "y",
            "dimension",
            "nominal",
            "Categoría" if titled else None,
            sort=EncodingSort(mode="descending", field="value_minor"),
        ),
        ChartEncoding("x", "value_minor", "quantitative", "Importe" if titled else None),
        ChartEncoding("tooltip", "dimension", "nominal"),
    )
    return ChartSpec(mark="bar", encodings=tuple(encodings[i] for i in order))


def test_canonical_ignores_titles_and_encoding_order() -> None:
    """Reordering encodings or retitling an axis is not a different visual decision, and counting
    it as one would inflate the number that is supposed to prove the planner adds something."""
    assert canonical(_bar(titled=True)) == canonical(_bar(titled=False, order=(2, 0, 1)))


def test_canonical_sees_a_different_mark_channel_or_sort() -> None:
    base = canonical(_bar(titled=False))
    different_mark = ChartSpec(mark="line", encodings=_bar(titled=False).encodings)
    swapped = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding(
                "x",
                "dimension",
                "nominal",
                sort=EncodingSort(mode="descending", field="value_minor"),
            ),
            ChartEncoding("y", "value_minor", "quantitative"),
            ChartEncoding("tooltip", "dimension", "nominal"),
        ),
    )
    unsorted = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal"),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("tooltip", "dimension", "nominal"),
        ),
    )
    assert canonical(different_mark) != base  # the mark is a decision
    assert canonical(swapped) != base  # orientation is a decision
    assert canonical(unsorted) != base  # the sort is a decision


_default_base = _bar(titled=False)
# The `sort` and `sort_field` cases need a base whose axis carries no per-encoding sort of its
# own: fix round 2 (item 8) makes `canonical()` ignore `spec.sort`/`spec.sort_field` whenever the
# presenter's own axis pick (the first nominal/temporal x/y encoding) already sorts itself, since
# a per-encoding sort always wins on the wire -- and `_default_base`'s "y" does exactly that
# (`_bar`'s own `EncodingSort(mode="descending", field="value_minor")`), which used to make these
# two rows indistinguishable from their base again once that rule existed.
_unsorted_base = ChartSpec(
    mark="bar",
    encodings=(
        ChartEncoding("y", "dimension", "nominal"),
        ChartEncoding("x", "value_minor", "quantitative"),
        ChartEncoding("tooltip", "dimension", "nominal"),
    ),
)
# The sort_field case needs its own base: comparing against `_unsorted_base` (sort=None) would
# change `sort` too, and the `sort` case above already proves *that* delta. A canonical() that
# never read `spec.sort_field` would still pass such a comparison, because `sort` alone moved.
_unsorted_sort_field_base = ChartSpec(
    mark=_unsorted_base.mark, encodings=_unsorted_base.encodings, sort="descending"
)


@pytest.mark.parametrize(
    ("base", "stack", "sort", "sort_field", "limit"),
    [
        (_default_base, "disabled", None, None, None),
        (_unsorted_base, None, "descending", None, None),
        (_unsorted_sort_field_base, None, "descending", "value_minor", None),
        (_default_base, None, None, None, 5),
    ],
    ids=["stack", "sort", "sort_field", "limit"],
)
def test_canonical_sees_stack_sort_sort_field_and_limit(
    base: ChartSpec,
    stack: Stack | None,
    sort: SortMode | None,
    sort_field: str | None,
    limit: int | None,
) -> None:
    """The docstring claims stack, the chart-wide sort/sort_field and limit are decisions; no
    test varied any of them. `sort_field` cannot be set without `sort`
    (`ChartSpec.__post_init__` enforces it), so that case is compared against
    `_unsorted_sort_field_base` -- a base that already carries `sort="descending"` -- so the pair
    differs only in `sort_field`, isolating it rather than merely carrying it alongside `sort`.
    Both this and the `sort` row use an axis with no per-encoding sort of its own (fix round 2,
    item 8): `canonical()` now ignores the chart-wide sort/sort_field whenever the presenter
    would too, so `_default_base` -- whose axis is already sorted -- could no longer isolate
    either delta."""
    changed = ChartSpec(
        mark=base.mark,
        encodings=base.encodings,
        stack=stack,
        sort=sort,
        sort_field=sort_field,
        limit=limit,
    )
    assert canonical(changed) != canonical(base)


def test_canonical_ignores_a_chart_wide_sort_the_presenter_would_drop() -> None:
    """Fix round 2, item 8. `present()` (`vega_lite.py`) writes the chart-wide sort onto the
    first nominal/temporal x/y encoding only when that channel carries no per-encoding sort of
    its own -- a per-encoding sort always wins on the wire. A FREQUENCY-shaped spec whose axis is
    already sorted `input` draws identically with or without a contradicting chart-wide sort, so
    `canonical()` must not count the latter as a decision, or `changed_from_baseline` would
    report a change that never reaches the presenter."""
    axis_sorted = (
        ChartEncoding("x", "dimension", "nominal", sort=EncodingSort(mode="input")),
        ChartEncoding("y", "value_minor", "quantitative"),
    )
    with_dead_chart_sort = ChartSpec(
        mark="bar", encodings=axis_sorted, sort="descending", sort_field="value_minor"
    )
    without_it = ChartSpec(mark="bar", encodings=axis_sorted)
    assert canonical(with_dead_chart_sort) == canonical(without_it)

    # The same chart-wide sort is a live decision the moment the axis has no per-encoding sort
    # of its own to override it with.
    axis_unsorted = (
        ChartEncoding("x", "dimension", "nominal"),
        ChartEncoding("y", "value_minor", "quantitative"),
    )
    live_chart_sort = ChartSpec(mark="bar", encodings=axis_unsorted, sort="descending")
    assert canonical(live_chart_sort) != canonical(ChartSpec(mark="bar", encodings=axis_unsorted))


def test_canonical_stays_comparable_when_two_tooltips_differ_only_by_sort() -> None:
    """`ChartSpec.__post_init__` enforces channel uniqueness everywhere except `tooltip`, so two
    tooltip encodings can share field and type and differ only in `sort`. Sorting a bare `None`
    against a 2-tuple used to raise `TypeError` here -- unreachable today, but `canonical()` runs
    inside `changed_from_baseline` on every shadow turn, so a comparison that can raise is a
    fallback that can crash."""
    one_sorted = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal"),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("tooltip", "dimension", "nominal", sort=EncodingSort(mode="input")),
            ChartEncoding("tooltip", "dimension", "nominal"),
        ),
    )
    neither_sorted = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal"),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("tooltip", "dimension", "nominal"),
            ChartEncoding("tooltip", "dimension", "nominal"),
        ),
    )
    assert canonical(one_sorted) != canonical(neither_sorted)
