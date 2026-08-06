"""A turn described before any workflow is allowed to act on it.

An intent says *what* the words resemble.  It does not say whether they answer an open
question, amend the last result, or contain two independent requests.  Those are different
decisions and keeping them in one enum is what made the old router depend increasingly on order.
"""

from dataclasses import dataclass
from enum import StrEnum

from finguard_orchestrator.domain.services.intent_router import Intent


class TurnRelation(StrEnum):
    NEW_REQUEST = "NEW_REQUEST"
    ANSWER_TO_SLOT = "ANSWER_TO_SLOT"
    FOLLOW_UP = "FOLLOW_UP"
    CORRECTION = "CORRECTION"
    COMPOUND = "COMPOUND"


class TurnFocus(StrEnum):
    NEW_REQUEST = "NEW_REQUEST"
    ACTIVE_WORKFLOW = "ACTIVE_WORKFLOW"
    LAST_ANSWER = "LAST_ANSWER"
    MULTIPLE_REQUESTS = "MULTIPLE_REQUESTS"


@dataclass(frozen=True)
class TurnAnalysis:
    """The routing facts downstream code needs, without exposing model prose."""

    intent: Intent
    relation: TurnRelation
    focus: TurnFocus
    clauses: tuple[str, ...] = ()

    @property
    def is_compound(self) -> bool:
        return self.relation is TurnRelation.COMPOUND
