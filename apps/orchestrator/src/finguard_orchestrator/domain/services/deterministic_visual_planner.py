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
from typing import Final, Literal, get_args

from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    EncodingSort,
    FieldOrder,
    PresentationContext,
    VisualProfile,
    derive_chart,
)
from finguard_orchestrator.domain.models.visual_plan import FallbackReason, VisualSource


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


# `VisualDecision.__post_init__`'s `accepted` invariant, over the closed set rather than a
# hand-typed copy of two of its three members (M6/D-4.2-1). Every source but the floor's own --
# derived from `VisualSource` (Global Constraint 7: closed sets are `Literal`s read through
# `get_args`) so a fourth source is admitted here the moment it is admitted there, rather than
# silently inheriting "must not be accepted" from a literal nobody remembered to widen.
_ACCEPTED_SOURCES: Final[frozenset[str]] = frozenset(get_args(VisualSource)) - {"deterministic"}


@dataclass(frozen=True)
class VisualDecision:
    """Which chart was chosen, from what, by whom, and why the other one was not.

    `source` is `llm | deterministic | patch` and not `llm | deterministic_fallback`: in the
    deterministic and shadow modes the floor is the *intended* selection rather than a recovery.
    `patch` is a verified edit to what is already shown, not a recovery either -- it carries no
    `fallback_reason` for the same reason `llm` does not. `fallback_reason` is filled only when an
    attempt was made and failed.

    `profile` is the decision's own input, carried rather than rebuilt. There is exactly one
    profile per turn because of it: `llm_mapper` used to build a second one from the same dataset
    for the conversational context, and a second construction is a second set of arguments that
    can be got wrong -- it was already omitting `plan_limited` and `plan_metric`, the two facts
    whose absence re-cuts a top-25 to eight and labels an axis of movements "Importe". Wave 3
    needs it here anyway: comparing a model's plan against the floor's needs the input both saw.
    """

    spec: ChartSpec
    profile: VisualProfile
    source: VisualSource
    rationale: str | None = None
    fallback_reason: FallbackReason | None = None
    proposed: bool = False
    """The provider was reached; a timeout never learns whether it was."""
    valid: bool = False
    """The draft passed the verifier."""
    accepted: bool = False
    """What the user sees is the model's or the patch's chart. Implies
    `source in _ACCEPTED_SOURCES` -- every source but `"deterministic"`."""
    changed_from_baseline: bool | None = None
    """The verified proposal differs from the floor on `canonical()`. None when the floor was
    not computed (promoted) or there was no verified proposal to compare."""
    latency_ms: int | None = None
    proposed_spec: ChartSpec | None = None
    """The verified proposal, whatever was shown -- so suitability can be annotated afterwards
    from the record rather than reconstructed from a `changed` flag."""
    rejected_rule: str | None = None
    error_detail: str | None = None
    """U-8 (Wave 3 hardening): `"code_error:<ExcType>"` when the fallback was forced by a bug in
    the model adapter itself (anything the planner raises beside its own two named failures,
    `ProviderFailedError`/`TimeoutError`) rather than by the provider. `FallbackReason` stays the
    closed four the wire seals (Task 1 of 4.5) -- this still records `provider_error` -- so a
    later reader can tell "the provider was down" from "our own code broke" without a fifth
    reason ever reaching the wire."""

    def __post_init__(self) -> None:
        # Constraint 7 ("closed sets are `Literal`s read through `get_args`") was true of this
        # first check alone; M6 (fix wave) makes it true of the two below as well -- both now
        # read a set derived from `VisualSource`, never a hand-typed copy of part of it.
        if self.source not in get_args(VisualSource):
            raise ValueError("unsupported visual decision source")
        if self.fallback_reason is not None and self.source not in {"deterministic"}:
            raise ValueError(
                "only a deterministic chart fell back -- a chosen model or patch plan did not "
                "fall back"
            )
        if self.accepted != (self.source in _ACCEPTED_SOURCES):
            raise ValueError("accepted means the model's or the patch's chart is what is shown")
        if self.error_detail is not None and self.fallback_reason != "provider_error":
            raise ValueError("error_detail only accompanies a provider_error fallback")


class DeterministicVisualPlanner:
    def plan(self, profile: VisualProfile, presentation: PresentationContext) -> ChartSpec | None:
        """Today's chart, from the profile alone.

        `presentation.max_visible_rows` controls the number of ranked rows retained in the chart.
        Viewport and channel do not change the chart decision here. The deterministic floor reads
        this display limit so a caller can preserve a small, complete category set.
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
            max_visible_rows=presentation.max_visible_rows,
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
        # `profile.comparison` is the dataset's shape marker plus a series to group by
        # (`metric == "COMPARISON" and has_series`), so `series[0]` below always exists, a
        # RECURRENCE profile -- whose series is a cadence -- never reaches this block as a
        # comparison, and an ungrouped comparison of two totals stays the plain bar it is.
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
