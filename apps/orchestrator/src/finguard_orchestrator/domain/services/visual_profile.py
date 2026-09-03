"""The dataset described so a planner can choose a chart for it, and nothing more.

This logic lived inline in `llm_mapper._visualization_to_dto`, feeding
`VisualProfile.from_spec`. It moves here for two reasons: a mapper builds transport DTOs and
should not compute domain facts, and the profile is now the *input* to a decision that the
application makes rather than a summary of one the mapper already made.

**Nothing here reads a value.** Cardinality is a count of distinct values, `positive` is a
comparison against zero, and `verified_total` is an equality. No amount and no dimension label
survives into the returned object, and a test asserts that with sentinels.
"""

from finguard_orchestrator.domain.models.chart import (
    FieldOrder,
    VisualDataset,
    VisualField,
    VisualProfile,
)

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
    chart is for. RECURRENCE likewise names streams rather than ranking them.
    """
    if temporal:
        return "TEMPORAL"
    if metric in {"FREQUENCY", "RECURRENCE"}:
        return "INPUT"
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
        comparison=has_series,
        # The number of rows drawn, which is what `derive_chart` means by `rows`. The measure's
        # cardinality above is a count of *distinct amounts* and is not this number: two rows
        # carrying the same amount are two rows. Still value-free -- a count of rows says nothing
        # about what is in them.
        row_count=len(rows),
        plan_limited=plan_limited,
        plan_metric=plan_metric,
    )
