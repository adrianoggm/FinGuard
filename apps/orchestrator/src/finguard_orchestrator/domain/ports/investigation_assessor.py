"""The graph's second model call: given what banking answered, investigate further or stop.

Separate from the interpreter (`TurnOperationsPort`) and never combined with it in one request: the
interpreter reads the person's sentence, this reads only a bounded summary of banking's results and
never the sentence. It may ask for one operation from an allow-list the graph computed, or stop with
a short narrative whose numbers the graph checks against the grounded answer before anything leaves.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from finguard_orchestrator.domain.errors import DomainError


@dataclass(frozen=True)
class MerchantSummary:
    """One risen merchant, value-minimised: its name, the cause the graph computed, and the two
    movement counts. No amounts: the model needs none to choose, and a figure it never sees is one
    it cannot misquote."""

    merchant: str
    cause: str
    earlier_count: int
    latest_count: int


@dataclass(frozen=True)
class InspectedSummary:
    """What inspecting a merchant found: the finding and the largest purchase as the answer says
    it, so a narrative quoting it quotes the grounded text."""

    merchant: str
    finding: str
    largest: str


@dataclass(frozen=True)
class InvestigationSummary:
    risen: tuple[MerchantSummary, ...]
    inspected: tuple[InspectedSummary, ...]
    # The merchants an inspection may name. Empty means the only answer is to stop.
    may_inspect: tuple[str, ...]


@dataclass(frozen=True)
class Inspect:
    merchant: str


@dataclass(frozen=True)
class Stop:
    why: str = ""


class AssessmentFailedError(DomainError):
    """The assessor's model could not answer. The graph answers with what it already has."""

    code = "assessment_failed"


@runtime_checkable
class InvestigationAssessorPort(Protocol):
    async def assess(self, summary: InvestigationSummary) -> Inspect | Stop:
        """The next step. An answer that cannot be read is `Stop()`, not an error."""
        ...
