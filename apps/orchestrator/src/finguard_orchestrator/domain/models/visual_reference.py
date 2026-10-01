"""What a sentence points at, among the bars a chart already drew.

Three shapes, and only three: one bar found (`Resolved`), several bars fit and the caller must ask
which (`Ambiguous`), or the sentence is not a reference to the chart at all (`NoMatch` — today's
path, unchanged). Nothing here executes a plan or reads an amount; a resolved reference is a label,
never a sentence, so this module adds no Spanish user-visible text.
"""

from dataclasses import dataclass
from typing import Literal

ReferenceKind = Literal["month", "category", "rank"]
"""Which reading found the bar: a calendar month (R7), a category name, or a rank (the highest or
the lowest bar, read off the chart's own order rather than named)."""


@dataclass(frozen=True)
class Resolved:
    """One bar of the chart in context: its label as drawn, and which reading found it."""

    label: str
    kind: ReferenceKind


@dataclass(frozen=True)
class Ambiguous:
    """Two or more bars fit; the caller asks which. Candidates in the chart's own rank order."""

    candidates: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.candidates) < 2:
            raise ValueError("an ambiguity names at least two candidates")


@dataclass(frozen=True)
class NoMatch:
    """The sentence points at nothing the chart drew -- not a chart reference; today's path."""


Reference = Resolved | Ambiguous | NoMatch
