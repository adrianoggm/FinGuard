"""The proposal vocabulary: plain strings on purpose, and the equality the baseline uses."""

from finguard_orchestrator.domain.models.chart import ChartEncoding, ChartSpec, EncodingSort
from finguard_orchestrator.domain.models.visual_plan import (  # noqa: F401
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
        encodings=(ProposedEncoding("zOffset", "dimension", "nominal"),),
        stack="upside_down",
    )
    assert draft.mark == "sideways"
    assert draft.encodings[0].channel == "zOffset"


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
    assert canonical(swapped) != base  # orientation is a decision
    assert canonical(unsorted) != base  # the sort is a decision


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
