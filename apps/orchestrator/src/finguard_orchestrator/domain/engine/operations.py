"""What the interpreter may say a turn does. Closed: a turn is one or more of these, nothing else.

These are all that crosses from the model into code. They name fields rather than restate a plan,
so a correction carries only what changed and the transition keeps the rest. That is what `None`
means throughout: not said, and therefore kept.
"""

import dataclasses
from dataclasses import dataclass
from typing import Literal

from finguard_orchestrator.domain.engine.state import Measure, Missing
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, RelativePeriod


@dataclass(frozen=True)
class PlanFields:
    """A plan described field by field, every field optional. None is "not said"; an empty tuple
    is said, and means none. Names are as the model wrote them until grounding replaces them."""

    direction: Direction | None = None
    window: RelativePeriod | AbsoluteRange | None = None
    categories: tuple[str, ...] | None = None
    merchants: tuple[str, ...] | None = None
    people: tuple[str, ...] | None = None
    accounts: tuple[str, ...] | None = None
    excluded_categories: tuple[str, ...] | None = None
    excluded_merchants: tuple[str, ...] | None = None
    metric: Metric | None = None
    group_by: GroupBy | None = None
    limit: int | None = None

    def named(self) -> frozenset[str]:
        """The fields that were said."""
        return frozenset(
            field.name
            for field in dataclasses.fields(self)
            if getattr(self, field.name) is not None
        )

    def merged(self, changes: "PlanFields") -> "PlanFields":
        """These fields with every field `changes` names replaced, and no other."""
        return dataclasses.replace(
            self, **{name: getattr(changes, name) for name in changes.named()}
        )


@dataclass(frozen=True)
class Ask:
    """A new question, complete in itself: nothing is inherited from what is on screen."""

    fields: PlanFields


@dataclass(frozen=True)
class Patch:
    """A correction. Only the fields `changes` names move; `target` says which plan they move on."""

    target: Literal["focus", "earlier", "latest", "pending"]
    changes: PlanFields


@dataclass(frozen=True)
class Compare:
    """Two sides measuring the same thing. `shared` applies to both and a side's own fields win;
    whatever neither names is the focus's, because «¿y comparado con junio?» is said about it."""

    shared: PlanFields
    earlier: PlanFields
    latest: PlanFields


@dataclass(frozen=True)
class ChangeMeasure:
    measure: Measure


@dataclass(frozen=True)
class SelectOperand:
    which: Literal["earlier", "latest"]


@dataclass(frozen=True)
class Reset:
    """Start again: clears what is on screen and the open question, keeps the accounts."""


@dataclass(frozen=True)
class Balance:
    accounts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Clarify:
    """`about` is a name to quote back, never prose: the question itself is a template."""

    missing: Missing
    about: str | None = None


@dataclass(frozen=True)
class Smalltalk:
    kind: Literal["GREETING", "THANKS", "IDENTITY", "HELP"]


@dataclass(frozen=True)
class Delegate:
    """Not the engine's turn. Money stays on the route that already guards it (spec decision 4)."""

    to: Literal["PAYMENT", "TRANSFER", "BOOKING", "CHART", "UNSUPPORTED"]


Operation = (
    Ask
    | Patch
    | Compare
    | ChangeMeasure
    | SelectOperand
    | Reset
    | Balance
    | Clarify
    | Smalltalk
    | Delegate
)
