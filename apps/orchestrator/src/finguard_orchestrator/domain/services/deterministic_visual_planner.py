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
    PresentationContext,
    VisualProfile,
    derive_chart,
)


@dataclass(frozen=True)
class VisualDecision:
    """Which chart was chosen, by whom, and why the other one was not.

    `source` is `llm | deterministic` and not `llm | deterministic_fallback`: in the
    deterministic and shadow modes the floor is the *intended* selection rather than a recovery.
    `fallback_reason` is filled only when an attempt was made and failed.
    """

    spec: ChartSpec
    source: Literal["llm", "deterministic"]
    rationale: str | None = None
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        if self.source not in {"llm", "deterministic"}:
            raise ValueError("unsupported visual decision source")
        if self.fallback_reason is not None and self.source == "llm":
            raise ValueError("a chosen model plan did not fall back")


class DeterministicVisualPlanner:
    def plan(self, profile: VisualProfile, presentation: PresentationContext) -> ChartSpec | None:
        """Today's chart, from the profile alone.

        `presentation` is deliberately unread here. The parameter exists because wave 3's
        `VisualPlannerPort` shares this signature, and the model planner is what will consume
        `max_visible_rows`; reading it here would change what this draws, and this is a move.
        """
        fields = profile.field_names()

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
            group_by="MONTH" if profile.temporal() else "NONE",
            # The plan's metric when there is one, falling back to the dataset's -- the exact
            # expression `_chart_for_dataset` used. Not `profile.metric` alone: that is the
            # dataset's shape marker, and a `COMPARISON` of counts would label its axis "Importe".
            metric=profile.plan_metric or profile.metric,
            # The rows drawn, which is not the measure's cardinality: that counts distinct
            # amounts, and two rows carrying the same amount are still two rows.
            rows=profile.row_count,
            fields=fields,
            dimension_field=dimension.name,
            value_field=measure[0].name,
            plan_limited=profile.plan_limited,
        )
        if spec is not None and "series" in fields:
            # `replace` and not a rebuild. The mapper listed the fields it carried over -- mark,
            # encodings, stack, sort, responsive -- and so dropped `limit`, `sort_field` and
            # `title` from every chart that had a series, including the row cut it had just
            # computed one line above.
            spec = replace(
                spec,
                encodings=spec.encodings + (ChartEncoding("color", "series", "nominal", "Serie"),),
            )
        return spec
