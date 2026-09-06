"""What a turn wants from the chart it follows — five words, read before anything is changed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VisualFollowUpKind = Literal[
    "PRESENTATION_ONLY", "ANALYTICAL_RESHAPE", "DRILL_DOWN", "UNRELATED", "AMBIGUOUS"
]
"""§5 of the continuous-visual-conversation design. AMBIGUOUS is a kind, not a failure: it is the
row that asks instead of guessing (4.2); in 4.1 it is only recorded."""

Claimant = Literal["interpret_visual", "follow_up_plan", "drill_down_reader", "both", "none"]
"""Which deterministic reader claimed the turn. `both` is the disagreement §5 says a model may one
day settle; `none` is the ordinary turn that has nothing to do with the chart."""


@dataclass(frozen=True)
class VisualFollowUp:
    kind: VisualFollowUpKind
    claimant: Claimant
