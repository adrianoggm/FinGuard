"""What the model proposes, before the domain has said whether it is a chart.

Every enumerated field is a plain `str` on purpose. `ChartEncoding` and `ChartSpec` validate
channel, type, mark and stack in their own `__post_init__`; typing them here would move the
refusal back into the adapter, and a model error would become an exception where it should be a
`Rejection` with a reason. The repository already holds this shape in `FinancialPlanDraft`.
"""

from dataclasses import dataclass
from typing import Literal, get_args

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.chart import ChartSpec
from finguard_orchestrator.domain.models.chart import FallbackReason as FallbackReason
from finguard_orchestrator.domain.models.chart import VisualSource as VisualSource

# The policy's closed sets. Defined here, not in `visual_planning_policy.py`, so
# `VisualDecision.fallback_reason` (`deterministic_visual_planner.py`) can be typed by the same
# name the policy constructs it from -- the policy already imports this module, so typing the
# field the other way round would be the cycle, not this one.
VisualMode = Literal["deterministic", "shadow", "hybrid", "promoted"]

# `FallbackReason` and `VisualSource` re-exported from `chart.py` (Task 1, Wave 4.5): they now type
# `VisualContext.fallback_reason`/`.source` there too, and `chart.py` cannot import this module
# back (this module already imports `ChartSpec` from it) -- so `chart.py` is the one definition,
# and this import keeps every existing `from .visual_plan import VisualSource, FallbackReason`
# in this codebase working unchanged.

# `Rejection.rule`'s closed set. The first nine are `visual_plan_verifier.py`'s own names, one per
# rule function in the order `verify` runs them; a name that module does not construct never
# belongs here without also appearing in a `Rejection(...)` there, and the reverse never happens
# silently -- `Rejection.__post_init__` below refuses anything outside this set. `"merge"` is the
# tenth and the only one no verifier rule produces: it is `llm_controller._visual_only_response`'s
# own name for the older refusal path, the `ValueError` `merge_encoding_patch`/`apply_visual_intent`
# raise before a merged spec ever reaches `verify` (I3). One closed set for both refusal paths, so
# `llm_controller._REFUSAL_TEXT` can be keyed by it and checked against `get_args` too.
RejectionRule = Literal[
    "structure",
    "fields",
    "types",
    "matrix",
    "mark",
    "donut_claims",
    "grouped",
    "limit",
    "order",
    "merge",
]


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
    """Why a draft is not a chart. `rule` is the verifier's own name for the check (or `merge`,
    the older refusal path's own name for itself -- I3), so a rejection rate can be read per rule,
    and `reason` is a sentence in the service's vocabulary.

    `__post_init__` refuses a `rule` outside `RejectionRule`: a name typed by hand at a
    construction site is exactly the unbounded-cardinality risk `Step` (`observability/
    decisions.py`) already argues against for a metric label, and `llm_controller._REFUSAL_TEXT`
    is keyed by this same closed set -- a rule this class let through and that dict did not know
    would `KeyError` at the one moment a holder is waiting for an answer, rather than here.
    """

    rule: RejectionRule
    reason: str

    def __post_init__(self) -> None:
        if self.rule not in get_args(RejectionRule):
            raise InvalidValueError(f"unknown rejection rule: {self.rule!r}")


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


def draft_of(spec: ChartSpec) -> VisualPlanDraft:
    """The plain-string draft that names exactly what a spec names -- so a spec the code built
    (a merged patch, a saved metric's chart) can be judged by the same verifier a model's
    proposal is. The title is dropped: a title is presentation, not a plan, and the verifier has
    no rule for it."""
    return VisualPlanDraft(
        mark=spec.mark,
        encodings=tuple(
            ProposedEncoding(
                channel=item.channel,
                field=item.field,
                type=item.type,
                sort=None if item.sort is None else ProposedSort(item.sort.mode, item.sort.field),
            )
            for item in spec.encodings
        ),
        sort=spec.sort,
        sort_field=spec.sort_field,
        stack=spec.stack,
        limit=spec.limit,
    )
