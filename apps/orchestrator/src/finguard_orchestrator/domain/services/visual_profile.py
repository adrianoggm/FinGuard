"""The dataset described so a planner can choose a chart for it, and nothing more.

This logic lived inline in `llm_mapper._visualization_to_dto`, feeding
`VisualProfile.from_spec`. It moves here for two reasons: a mapper builds transport DTOs and
should not compute domain facts, and the profile is now the *input* to a decision that the
application makes rather than a summary of one the mapper already made.

**Nothing here reads a value.** Cardinality is a count of distinct values, `positive` is a
comparison against zero, and `verified_total` is an equality. No amount and no dimension label
survives into the returned object, and a test asserts that with sentinels.
"""

from typing import Final

from finguard_orchestrator.domain.models.chart import (
    DrawnLabels,
    FieldOrder,
    VisualDataset,
    VisualField,
    VisualProfile,
)

# The wire's own per-list cap (`VisualContextPayload.labels`/`.series_labels`, `infrastructure/dto/
# llm_dto.py`, M10 fix wave: imported there rather than a second `100` written by hand) -- named
# once here because `drawn_labels` is where the cut actually happens, and a dataset with a hundred
# distinct dimensions is not a shape the deterministic floor draws today, but a later plan
# (`group_limit`) could ask for exactly that many rows.
MAX_DRAWN_LABELS: Final = 100

# Group keys that name a calendar bucket. Same set `derive_chart` has always used; stated once
# here so the profile and the floor cannot disagree about what "temporal" means.
_TEMPORAL_GROUPS = frozenset({"DAY", "WEEK", "MONTH", "PERIOD", "DATE"})

# Metrics whose measure is a number of movements rather than money.
_COUNTING = frozenset({"COUNT", "FREQUENCY"})

# Metrics whose producer builds its own dimension, and whose chart is built before `group_by` is
# ever read -- `llm_mapper._chart_for_dataset` early-returns for each of them. Their dimension is
# nominal whatever the plan grouped by: weekday names, stream names, "Parte"/"Resto". A calendar
# `group_by` must not relabel it temporal, or this profile would contradict the chart beside it
# and RATIO_SHARE would lose its donut for having no nominal field left.
_OWN_DIMENSION = frozenset({"FREQUENCY", "RECURRENCE", "RATIO_SHARE"})


def _unit(metric: str, plan_metric: str | None) -> str:
    # COMPARISON describes the dataset shape, not its measure. Its plan is the authority for
    # whether the compared values are money or movement counts. Other shape families retain their
    # own semantics (notably RATIO_SHARE), irrespective of the surrounding plan.
    measure = plan_metric if metric == "COMPARISON" and plan_metric is not None else metric
    if measure in _COUNTING:
        return "count"
    return "ratio" if measure == "RATIO_SHARE" else "money"


def _dimension_order(metric: str, temporal: bool) -> FieldOrder:
    """What ordering the dimension asks for, which is a property of the question, not the chart.

    FREQUENCY is the row that earns this function: its dimension is nominal weekday names in the
    order the producer built them, and sorting those by magnitude destroys the only thing the
    chart is for. RECURRENCE is `NONE`, not `INPUT`: its dimension is the `y` axis of a `point`
    chart, and Section D puts the whole `point` family under `NONE` -- ordering the axis does not
    apply. An earlier version of this function gave it `INPUT`, which would have forced a
    `sort: "input"` the floor never emits.
    """
    if temporal:
        return "TEMPORAL"
    if metric == "FREQUENCY":
        return "INPUT"
    if metric == "RECURRENCE":
        return "NONE"
    return "MEASURE_DESC"


def visual_profile_of(
    dataset: VisualDataset,
    *,
    total_minor: int | None,
    group_by: str | None,
    plan_limited: bool = False,
    plan_metric: str | None = None,
) -> VisualProfile:
    """Build the profile from the dataset -- plus `group_by`, which the dataset does not carry.

    `group_by` is not a `VisualDataset` field: it lives on `SpokenPlan`, a sibling of the dataset
    on the query's Result, not on the dataset itself. Nothing about the dataset's own rows says
    whether "2026-01" is a month bucket or a label that happens to look like one -- the same
    `dimension` field carries a raw ISO group key on one path and a Spanish weekday name on
    another, so guessing from the string would be exactly the kind of untested rule that made
    this parameter necessary in the first place. The caller already holds it (`llm_mapper` reads
    `result.plan_context.group_by`) and passes it through, the same way it already passes
    `total_minor`.

    `plan_limited` and `plan_metric` arrive the same way and for the same reason: they are
    `group_limit is not None` and `metric` on the plan, and no row of the dataset can tell you
    about either. `plan_metric` matters because `dataset.metric` is overloaded -- a comparison of
    two counts says `COMPARISON`, and only the plan says the numbers are movements. The defaults
    are the shape of a dataset nobody grouped, nobody cut, and whose plan is not to hand.
    """
    rows = dataset.rows
    temporal = group_by in _TEMPORAL_GROUPS and dataset.metric not in _OWN_DIMENSION
    has_series = any(row.series is not None for row in rows)
    has_occurred_at = any(row.occurred_at is not None for row in rows)

    fields: list[VisualField] = [
        VisualField(
            "dimension",
            "temporal" if temporal else "nominal",
            "dimension",
            len({row.dimension for row in rows}),
            _dimension_order(dataset.metric, temporal),
        ),
        VisualField(
            "value_minor", "quantitative", "measure", len({row.value_minor for row in rows})
        ),
    ]
    if has_series:
        # `INPUT` and not a magnitude order: a legend of periods reads in the order the producer
        # built them, and the producer builds the latest window first.
        fields.append(
            VisualField(
                "series",
                "nominal",
                "series",
                len({row.series for row in rows if row.series is not None}),
                "INPUT",
            )
        )
    if has_occurred_at:
        fields.append(
            VisualField(
                "occurred_at",
                "temporal",
                "dimension",
                len({row.occurred_at for row in rows if row.occurred_at is not None}),
                "TEMPORAL",
            )
        )

    return VisualProfile(
        fields=tuple(fields),
        metric=dataset.metric,
        unit=_unit(dataset.metric, plan_metric),
        positive=all(row.value_minor > 0 for row in rows),
        # A series means the rows are slices of one dimension rather than disjoint parts of a
        # whole, so they cannot be the parts a donut claims sum to the total.
        mutually_exclusive=not has_series,
        verified_total=(
            dataset.metric == "RATIO_SHARE"
            or (total_minor is not None and sum(row.value_minor for row in rows) == total_minor)
        ),
        # The dataset's shape marker AND a series to group by -- not the presence of a series
        # alone: RECURRENCE carries a series for its cadence and is not a comparison. Not the
        # marker alone either: an ungrouped comparison of two totals (`ask_financial_question.py`,
        # the `else` branch beside `comparison_visual_rows`) is two bars over two periods, with
        # no series field to put on color -- a plain bar, not a grouped one. Only the handler's
        # comparison path sets the marker, and the exporter's `categorical_comparison` mirrors it.
        comparison=dataset.metric == "COMPARISON" and has_series,
        # The number of rows drawn, which is what `derive_chart` means by `rows`. The measure's
        # cardinality above is a count of *distinct amounts* and is not this number: two rows
        # carrying the same amount are two rows. Still value-free -- a count of rows says nothing
        # about what is in them.
        row_count=len(rows),
        plan_limited=plan_limited,
        plan_metric=plan_metric,
    )


def drawn_labels(dataset: VisualDataset) -> DrawnLabels:
    """The labels `dataset` draws, ranked the way the chart itself ranks its bars.

    `totals` sums `value_minor` per distinct `dimension`, in first-appearance order -- a `dict`
    preserves that order on its own, so nothing here sorts to get it. Ranking then sorts those
    totals descending with a stable sort, so any tie keeps the first-appearance order rather than
    an arbitrary one. `series_labels` never enters the ranking -- a legend is not a set of bars --
    it is simply the distinct `series` values (skipping rows with none) in first-appearance order.

    Both lists are capped at `MAX_DRAWN_LABELS`; `ranked` answers a narrower question than "is
    this sorted" -- whether the *top* and the *bottom* are each unique, so «la barra más alta» and
    «la más baja» each have one answer. It is `False` whenever the dimension list had to be cut at
    the cap (the true bottom is off the list) or the dataset carried no rows at all.

    A-11 (Wave 3 hardening): when the dataset has MORE THAN ONE distinct `series` value (a
    comparison, or any grouped bar with a colour split), the *chart* draws one bar per (dimension,
    series) pair -- `len(dimensions) * len(series)` bars -- never one bar per dimension. A single
    repeated series value (RECURRENCE's cadence, e.g. every row tagged `"MONTHLY"`) draws no extra
    bar and is not this case -- summing across it is summing across nothing, the same total the
    no-series branch below would compute. `deterministic_visual_planner.py`'s own dimension-axis
    sort (`_sort_for`) asks Vega-Lite to order the axis by `{"field": measure, "order":
    "descending"}` with no `"op"` of its own, and Vega-Lite's documented default for a sort field
    that repeats per axis position is `"mean"`, not `"sum"` -- so summing `value_minor` across two
    or more series, the way the single-series/no-series branch below does, is not even guaranteed
    to be the *axis's own* order once series sizes differ, let alone a claim about any single bar.
    Ranking here would be inventing a number nobody's screen shows for a value nobody asked for
    (the total across periods), which is exactly the "guess with a confident shape fact attached"
    the finding names. So a multi-series dataset never ranks: the dimension order is
    first-appearance (the one order this module can state as fact, needing no aggregate at all) and
    `ranked` is always `False`, refusing every superlative rather than answering one about a bar
    nobody drew.
    """
    totals: dict[str, int] = {}
    series_seen: set[str] = set()
    series_order: list[str] = []
    for row in dataset.rows:
        totals[row.dimension] = totals.get(row.dimension, 0) + row.value_minor
        if row.series is not None and row.series not in series_seen:
            series_seen.add(row.series)
            series_order.append(row.series)

    if not totals:
        return DrawnLabels()

    series_labels = tuple(series_order[:MAX_DRAWN_LABELS])

    if len(series_seen) > 1:
        # First-appearance order, the one fact this module can state -- `ranked` is always `False`
        # regardless of the cap below, so there is no "true bottom off the list" case to track here
        # the way the no-series branch's `cut` tracks it.
        dimensions = tuple(totals)
        labels = dimensions[:MAX_DRAWN_LABELS]
        return DrawnLabels(labels=labels, series_labels=series_labels, ranked=False)

    ranked_dimensions = sorted(totals, key=lambda label: totals[label], reverse=True)
    cut = len(ranked_dimensions) > MAX_DRAWN_LABELS
    labels = tuple(ranked_dimensions[:MAX_DRAWN_LABELS])

    if cut:
        ranked = False
    elif len(labels) == 1:
        ranked = True
    else:
        ranked = totals[labels[0]] > totals[labels[1]] and totals[labels[-1]] < totals[labels[-2]]

    return DrawnLabels(labels=labels, series_labels=series_labels, ranked=ranked)
