"""What the interpreter may say a turn does. Closed: a turn is one or more of these, nothing else.

These are all that crosses from the model into code. They name fields rather than restate a plan,
so a correction carries only what changed and the transition keeps the rest. That is what `None`
means throughout: not said, and therefore kept.
"""

from dataclasses import dataclass
from typing import Literal

# `PlanFields` lives with the state, whose pending question holds one; it is named here too because
# it is what every operation carries.
from finguard_orchestrator.domain.engine.state import Measure, Missing, PlanFields

__all__ = [
    "Ask",
    "Balance",
    "ChangeMeasure",
    "Clarify",
    "Compare",
    "Delegate",
    "Operation",
    "Patch",
    "PlanFields",
    "Reset",
    "SelectOperand",
    "Smalltalk",
]


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
    """`about` is a name to quote back and `candidates` the holder's entries it could be, both
    from grounding, never prose: the question itself is a template."""

    missing: Missing
    about: str | None = None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Smalltalk:
    kind: Literal["GREETING", "THANKS", "IDENTITY", "HELP"]


@dataclass(frozen=True)
class Delegate:
    """Not the engine's turn. Money stays on the route that already guards it (spec decision 4)."""

    to: Literal["PAYMENT", "TRANSFER", "BOOKING", "CHART", "UNSUPPORTED"]


@dataclass(frozen=True)
class Visualize:
    """A first-class visual operation over the last query result or current chart."""

    target: Literal["ACTIVE_RESULT", "ACTIVE_CHART"]
    style: Literal["AUTO", "BAR", "LINE"] = "AUTO"


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
    | Visualize
)
