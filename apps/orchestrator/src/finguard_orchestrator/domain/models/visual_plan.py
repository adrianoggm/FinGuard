"""What the model proposes, before the domain has said whether it is a chart.

Every enumerated field is a plain `str` on purpose. `ChartEncoding` and `ChartSpec` validate
channel, type, mark and stack in their own `__post_init__`; typing them here would move the
refusal back into the adapter, and a model error would become an exception where it should be a
`Rejection` with a reason. The repository already holds this shape in `FinancialPlanDraft`.
"""

from dataclasses import dataclass
from typing import Literal

from finguard_orchestrator.domain.models.chart import ChartSpec

# The policy's closed sets. Defined here, not in `visual_planning_policy.py`, so
# `VisualDecision.fallback_reason` (`deterministic_visual_planner.py`) can be typed by the same
# name the policy constructs it from -- the policy already imports this module, so typing the
# field the other way round would be the cycle, not this one.
VisualMode = Literal["deterministic", "shadow", "hybrid", "promoted"]
FallbackReason = Literal["abstention", "timeout", "provider_error", "invalid_proposal"]


@dataclass(frozen=True)
class ProposedSort:
    mode: str  # input | ascending | descending -- validated by EncodingSort, not here
    field: str | None = None


@dataclass(frozen=True)
class ProposedEncoding:
    channel: str
    field: str
    type: str
    sort: ProposedSort | None = None


@dataclass(frozen=True)
class VisualPlanDraft:
    mark: str
    encodings: tuple[ProposedEncoding, ...]
    sort: str | None = None
    sort_field: str | None = None
    stack: str | None = None
    limit: int | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class Rejection:
    """Why a draft is not a chart. `rule` is the verifier's own name for the check, so a
    rejection rate can be read per rule, and `reason` is a sentence in the service's vocabulary."""

    rule: str
    reason: str


def canonical(spec: ChartSpec) -> tuple[object, ...]:
    """The part of a spec that is a visual *decision*.

    Titles are presentation, and the order encodings were appended in is an accident of which
    rule ran last. Everything else -- mark, which field sits on which channel with what type
    and what per-encoding order, stack, the chart-wide sort, the limit -- is what
    `changed_from_baseline` means by "different".
    """
    encodings = tuple(
        sorted(
            (
                item.channel,
                item.field,
                item.type,
                item.sort.mode if item.sort is not None else "",
                (item.sort.field or "") if item.sort is not None else "",
            )
            for item in spec.encodings
        )
    )
    # `present()` (`infrastructure/visualization/vega_lite.py`) picks the first nominal/temporal
    # x/y encoding and writes the chart-wide sort onto it only when that channel carries no
    # per-encoding sort of its own -- a per-encoding sort always wins on the wire. A spec whose
    # axis dimension is already sorted per-encoding draws identically whatever `spec.sort` /
    # `spec.sort_field` say, so counting them as a decision there would report a change that
    # never reaches the presenter.
    axis = next(
        (
            item
            for item in spec.encodings
            if item.channel in {"x", "y"} and item.type in {"nominal", "temporal"}
        ),
        None,
    )
    axis_has_own_sort = axis is not None and axis.sort is not None
    chart_sort = None if axis_has_own_sort else spec.sort
    chart_sort_field = None if axis_has_own_sort else spec.sort_field
    return (spec.mark, encodings, spec.stack, chart_sort, chart_sort_field, spec.limit)
