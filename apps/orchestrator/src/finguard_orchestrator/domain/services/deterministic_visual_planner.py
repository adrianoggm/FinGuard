"""The chart a rule chooses, which is three things at once.

It is the **availability guarantee** (a deployment with no visual model behaves exactly as it
does today), the **fallback** when a model planner abstains or fails, and the **experimental
baseline** the model's proposal is compared against. That third role is why it is injected
separately rather than being the port's default implementation: computing "did the model choose
something different" needs both planners at once.

It is a **move**. Every rule below was `llm_mapper._chart_for_dataset`, a synchronous function
inside a transport mapper -- which is why the decision had to come up here before a model call
could ever be one of the things making it. Four chart families are decided before `derive_chart`
is reached and three of them never reach it at all; `derive_chart` itself stays where it is and
stays pure. All this adds is the profile as an input instead of loose arguments off a Result.
"""

from dataclasses import dataclass, replace
from typing import Literal

from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    EncodingSort,
    FieldOrder,
    PresentationContext,
    VisualProfile,
    derive_chart,
)
from finguard_orchestrator.domain.models.visual_plan import FallbackReason


def _sort_for(order: FieldOrder, measure_name: str) -> EncodingSort | None:
    """The per-encoding sort a dimension's `order` asks for, or `None` to leave it alone.

    The order comes from the profile, never from the field's type. FREQUENCY is nominal and
    deliberately `INPUT`: its dimension is weekday names in producer order, and sorting those by
    magnitude destroys the only thing that chart is for -- but FREQUENCY never calls this
    function, because it early-returns from `plan()` before `dimension` is even resolved. Within
    `plan()`'s reach, `order` is only ever `TEMPORAL` or `MEASURE_DESC`: both `INPUT` metrics
    (FREQUENCY, RECURRENCE) early-return, so this `INPUT` branch is unreachable through `plan()`
    today. It exists for the wave-3 planner, which may hand any order, and is pinned by a direct
    test rather than left untested because of that.
    """
    if order == "MEASURE_DESC":
        return EncodingSort(mode="descending", field=measure_name)
    if order == "INPUT":
        return EncodingSort(mode="input")
    return None


@dataclass(frozen=True)
class VisualDecision:
    """Which chart was chosen, from what, by whom, and why the other one was not.

    `source` is `llm | deterministic` and not `llm | deterministic_fallback`: in the
    deterministic and shadow modes the floor is the *intended* selection rather than a recovery.
    `fallback_reason` is filled only when an attempt was made and failed.

    `profile` is the decision's own input, carried rather than rebuilt. There is exactly one
    profile per turn because of it: `llm_mapper` used to build a second one from the same dataset
    for the conversational context, and a second construction is a second set of arguments that
    can be got wrong -- it was already omitting `plan_limited` and `plan_metric`, the two facts
    whose absence re-cuts a top-25 to eight and labels an axis of movements "Importe". Wave 3
    needs it here anyway: comparing a model's plan against the floor's needs the input both saw.
    """

    spec: ChartSpec
    profile: VisualProfile
    source: Literal["llm", "deterministic"]
    rationale: str | None = None
    fallback_reason: FallbackReason | None = None
    proposed: bool = False
    """The provider was reached; a timeout never learns whether it was."""
    valid: bool = False
    """The draft passed the verifier."""
    accepted: bool = False
    """What the user sees is the model's chart. Implies `source == "llm"`."""
    changed_from_baseline: bool | None = None
    """The verified proposal differs from the floor on `canonical()`. None when the floor was
    not computed (promoted) or there was no verified proposal to compare."""
    latency_ms: int | None = None
    proposed_spec: ChartSpec | None = None
    """The verified proposal, whatever was shown -- so suitability can be annotated afterwards
    from the record rather than reconstructed from a `changed` flag."""
    rejected_rule: str | None = None

    def __post_init__(self) -> None:
        if self.source not in {"llm", "deterministic"}:
            raise ValueError("unsupported visual decision source")
        if self.fallback_reason is not None and self.source == "llm":
            raise ValueError("a chosen model plan did not fall back")
        if self.accepted != (self.source == "llm"):
            raise ValueError("accepted means the model's chart is what is shown")


class DeterministicVisualPlanner:
    def plan(self, profile: VisualProfile, presentation: PresentationContext) -> ChartSpec | None:
        """Today's chart, from the profile alone.

        `presentation` is deliberately unread here. The parameter exists so `VisualPlanningPolicy`
        can hand the floor and the model planner the same inputs -- not because this is an
        implementation of `VisualPlannerPort`, whose `plan` is async, also takes the question and a
        budget, and returns a draft rather than a spec. The model planner is what will consume
        `max_visible_rows`; reading it here would change what this draws, and this is a move.
        """
        fields = profile.field_names()
        # `field_names()` is a union of every field any row carries. The mapper's own guard
        # (`llm_mapper._visualization_to_dto`'s `valid_for(fields)`) checks the *intersection* of
        # the serialised rows' keys instead, so a `"series" in fields` test below (there are two)
        # could pass on the union while some row lacks the field the resulting spec then names.
        # Safe today because `series` is set for every row or none, at every construction site in
        # `visual_profile_of` -- a dataset that ever set it on only some rows would reopen this.

        # Dispatched before `derive_chart`, exactly as the mapper dispatched them. These three
        # metrics build their own dimension -- `visual_profile.py` names them `_OWN_DIMENSION` --
        # and their profiles are never temporal whatever the plan grouped by, so no branch below
        # this point can reach them.
        if profile.metric == "RECURRENCE":
            if not {"occurred_at", "dimension", "value_minor"} <= fields:
                return None
            # `x` is the date and `y` is the stream, so the amount is tooltip-only: a recurring
            # charge is a question about *when*, and an axis of money would rank the streams.
            encodings: tuple[ChartEncoding, ...] = (
                ChartEncoding("x", "occurred_at", "temporal", "Fecha"),
                ChartEncoding("y", "dimension", "nominal", "Recurrente"),
                ChartEncoding("tooltip", "occurred_at", "temporal"),
                ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
            )
            if "series" in fields:
                encodings += (ChartEncoding("color", "series", "nominal", "Cadencia"),)
            return ChartSpec(mark="point", encodings=encodings)

        if profile.metric == "FREQUENCY":
            return ChartSpec(
                mark="bar",
                encodings=(
                    ChartEncoding("x", "dimension", "nominal", "Día"),
                    ChartEncoding("y", "value_minor", "quantitative", "Movimientos"),
                    ChartEncoding("tooltip", "dimension", "nominal"),
                    ChartEncoding("tooltip", "value_minor", "quantitative"),
                ),
                # Weekday names in the order the producer built them. Ranking them by magnitude
                # destroys the only thing the chart is for.
                sort="input",
            )

        if profile.metric == "RATIO_SHARE":
            return ChartSpec(
                mark="bar",
                encodings=(
                    ChartEncoding("x", "value_minor", "quantitative", "Porcentaje"),
                    ChartEncoding("color", "dimension", "nominal", "Parte"),
                    ChartEncoding("tooltip", "dimension", "nominal"),
                    ChartEncoding("tooltip", "value_minor", "quantitative", "Importe"),
                ),
                stack="normalize",
            )

        measure = profile.role("measure")
        dimension = next(
            (item for item in profile.role("dimension") if item.type != "temporal"),
            None,
        ) or next(iter(profile.role("dimension")), None)
        if not measure or dimension is None:
            return None
        spec = derive_chart(
            # `temporal()` is true if *any* dimension-role field is temporal, and `occurred_at`
            # (added with role="dimension") would make it true here even when `group_by` names no
            # calendar bucket. Safe today because only RECURRENCE datasets set `occurred_at`
            # (`visual_profile_of`), and RECURRENCE returns above, before this line is reached.
            group_by="MONTH" if profile.temporal() else "NONE",
            # `measure_metric()`, not `profile.metric` alone: that is the dataset's shape marker,
            # and a `COMPARISON` of counts would label its axis "Importe" -- the exact expression
            # `_chart_for_dataset` used, now named so that mistake has to be written on purpose.
            metric=profile.measure_metric(),
            # The rows drawn, which is not the measure's cardinality: that counts distinct
            # amounts, and two rows carrying the same amount are still two rows.
            rows=profile.row_count,
            fields=fields,
            dimension_field=dimension.name,
            value_field=measure[0].name,
            plan_limited=profile.plan_limited,
        )
        # A ranked dimension sorts by its measure -- on its *axis*, not its tooltip. `derive_chart`
        # always emits a `ChartEncoding("tooltip", dimension_field, ...)` beside the axis encoding
        # for the same field, and Vega-Lite ignores `sort` on a tooltip channel, so a filter on
        # `field == dimension.name` alone would write an inert-but-present "sort" onto the wire
        # there too: invisible on screen, a real, unintended change to what gets sent. `dimension`
        # is only ever placed on `x` or `y` by `derive_chart`, so restricting to those two channels
        # is exact, not a heuristic. `dimension.order` came from the profile above, not from
        # `dimension.type`, so this is only ever a per-encoding sort on the one axis encoding it
        # names -- Task 7's comparison block below adds its own `color`/offset encodings with their
        # own `EncodingSort(mode="input")`, and the two never collide.
        if spec is not None:
            wanted = _sort_for(dimension.order, measure[0].name)
            if wanted is not None:
                spec = replace(
                    spec,
                    encodings=tuple(
                        replace(item, sort=wanted)
                        if item.channel in {"x", "y"} and item.field == dimension.name
                        else item
                        for item in spec.encodings
                    ),
                )
        series = profile.role("series")
        # `profile.comparison` is `has_series` under a name that reads as "this is a comparison" --
        # true for RECURRENCE too, since a cadence is a series, which is safe to read here only
        # because RECURRENCE returns above, before this line is ever reached. Nothing else sets
        # `comparison` for a reason other than an actual comparison.
        if spec is not None and profile.comparison and not profile.temporal():
            # Grouped, and both halves are required: `disabled` stops Vega-Lite's default stack,
            # which for a bar with a colour series is to stack; the offset is what separates the
            # bars instead of overlapping them. The offset's axis follows the dimension's, or the
            # groups would be split along the measure.
            # `derive_chart` also emits a `ChartEncoding("tooltip", dimension_field, ...)` beside
            # the axis encoding for the same field (see the sort rule's comment above), so a
            # filter on `field == dimension.name` alone would pick whichever of the two comes
            # first in the tuple -- today the axis one, by construction order, not by any channel
            # check. Restricting to `{"x", "y"}` is what actually selects the axis encoding.
            dimension_channel = next(
                item.channel
                for item in spec.encodings
                if item.channel in {"x", "y"} and item.field == dimension.name
            )
            # The f-string that built `offset_channel` before lost its `x`/`y` origin to a bare
            # `str`, which the `Channel` literal refuses -- hence the explicit annotation below.
            offset_channel: Literal["xOffset", "yOffset"] = (
                "yOffset" if dimension_channel == "y" else "xOffset"
            )
            spec = replace(
                spec,
                stack="disabled",
                encodings=spec.encodings
                + (
                    ChartEncoding(
                        "color", series[0].name, "nominal", sort=EncodingSort(mode="input")
                    ),
                    ChartEncoding(
                        offset_channel,
                        series[0].name,
                        "nominal",
                        sort=EncodingSort(mode="input"),
                    ),
                ),
            )
        elif spec is not None and "series" in fields:
            # `replace` and not a rebuild. The mapper listed the fields it carried over -- mark,
            # encodings, stack, sort, responsive -- and so dropped `limit` from every chart that
            # had a series, including the row cut it had just computed one line above.
            # `sort_field` and `title` were never at risk: `derive_chart` never sets `sort_field`,
            # and the mapper applied `title` after this call returned.
            #
            # Reached only when the comparison rule above did not fire -- a temporal dimension
            # with a series, where the lines are already distinguishable by colour and neither an
            # offset nor a disabled stack means anything on a line/area mark.
            spec = replace(
                spec,
                encodings=spec.encodings + (ChartEncoding("color", "series", "nominal", "Serie"),),
            )
        return spec
