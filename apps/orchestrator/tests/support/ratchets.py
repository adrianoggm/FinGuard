"""A number a check compares against, carrying the unit it is in.

Seven of these are spread across six files — corpus episodes, paraphrase variants, handed-over
sentences, contested turns — and one of them lied in green for as long as it existed. `UNMEASURED`
was 107, written from a count of *turns*, while the assertion under it counted *distinct sentences*:
the two were 104 and 69 the day it was noticed, so the ratchet carried thirty-five sentences of
slack and would have passed while half as many again appeared.

Nothing about that was hard to see and nothing in the suite could see it, because a bare `int` says
nothing about what it counts. So a ratchet is declared with its unit and it does the counting
itself: the number compared is always `len()` of the collection handed to it, and the unit is in the
failure message where the next person reads it.

`tests/test_every_ratchet_declares_its_unit.py` is what keeps the category closed — a new bare
integer in a test file has to become one of these or be excused by name.
"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Ceiling:
    """A number that may only go down: unmeasured sentences, mute turns, contested turns.

    Down by doing the work, never by widening what is counted — which is why the count comes from
    here rather than from the caller.
    """

    limit: int
    unit: str

    def exceeded_by(self, counted: Collection[Any]) -> str:
        """The complaint, or an empty string. Written for `assert not ...`, so the message and the
        comparison cannot drift apart into two statements about different collections."""
        if len(counted) <= self.limit:
            return ""
        excess = "\n".join(f"  {item}" for item in list(counted)[:20])
        return f"{len(counted)} {self.unit}, above the ceiling of {self.limit}:\n{excess}"


@dataclass(frozen=True)
class Floor:
    """A number that may only go up: episodes in the corpus, paraphrases that dispatch correctly.

    The failure it exists for is a glob that matches nothing — a check with no work to do reads
    exactly like a check that passed.
    """

    limit: int
    unit: str

    def undercut_by(self, counted: Collection[Any] | int) -> str:
        reached = counted if isinstance(counted, int) else len(counted)
        if reached >= self.limit:
            return ""
        return f"{reached} {self.unit}, below the floor of {self.limit}"
