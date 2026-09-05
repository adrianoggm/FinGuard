"""What the model proposes, before the domain has said whether it is a chart.

Every enumerated field is a plain `str` on purpose. `ChartEncoding` and `ChartSpec` validate
channel, type, mark and stack in their own `__post_init__`; typing them here would move the
refusal back into the adapter, and a model error would become an exception where it should be a
`Rejection` with a reason. The repository already holds this shape in `FinancialPlanDraft`.
"""

from dataclasses import dataclass

from finguard_orchestrator.domain.models.chart import ChartSpec


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
    return (spec.mark, encodings, spec.stack, spec.sort, spec.sort_field, spec.limit)
