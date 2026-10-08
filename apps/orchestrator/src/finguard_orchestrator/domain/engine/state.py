"""The engine's one canonical picture of a conversation.

Everything a later turn may refer to lives here: what is on screen (the focus), the question left
open (pending), the accounts being looked at (scope) and the last turns in words. One place, so
there is nothing for two copies of the same fact to disagree about.

`version` is what makes the state safe to share between requests: the store refuses a save made
against a version that is no longer current.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

if TYPE_CHECKING:
    # Operations name the state's enums and a pending question holds an operation's fields; one
    # side of that pair has to be a type-only import.
    from finguard_orchestrator.domain.engine.operations import PlanFields


class Measure(StrEnum):
    """How a comparison is shown. PERCENT_CHANGE is the one no metric says: it is arithmetic over
    the pair, which is why it lives on the focus and not on the plans."""

    AMOUNT = "AMOUNT"
    COUNT = "COUNT"
    AVERAGE = "AVERAGE"
    PERCENT_CHANGE = "PERCENT_CHANGE"


class Missing(StrEnum):
    """What a clarification asks for. Closed, so the question is a template and never prose."""

    WINDOW = "WINDOW"
    SUBJECT = "SUBJECT"
    ACCOUNT = "ACCOUNT"
    PERSON = "PERSON"
    OPERAND = "OPERAND"


@dataclass(frozen=True)
class QueryFocus:
    plan: FinancialPlan | TopMovements


@dataclass(frozen=True)
class ComparisonFocus:
    """A pair on screen. The measure sits beside the pair rather than replacing it, so «en
    porcentaje» and then «en euros» are two views of one comparison and not two comparisons."""

    comparison: ComparisonPlan
    measure: Measure = Measure.AMOUNT


@dataclass(frozen=True)
class RatioFocus:
    ratio: RatioPlan


@dataclass(frozen=True)
class BalanceFocus:
    account_ids: tuple[UUID, ...] = ()


Focus = QueryFocus | ComparisonFocus | RatioFocus | BalanceFocus


@dataclass(frozen=True)
class Pending:
    """A question asked back, holding everything already said, so the reply only fills the gap."""

    missing: Missing
    draft: "PlanFields"


@dataclass(frozen=True)
class Scope:
    """The accounts being looked at. Empty means every account, not none."""

    account_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class DialogueTurn:
    """`answered` is the plan in words and never its figures: a figure in the prompt is a figure
    a model can repeat, and every figure must come from the bank."""

    said: str
    answered: str


@dataclass(frozen=True)
class DialogueState:
    conversation_id: UUID
    version: int = 0
    focus: Focus | None = None
    pending: Pending | None = None
    scope: Scope = Scope()
    turns: tuple[DialogueTurn, ...] = ()

    # What bounds the state the interpreter's prompt carries.
    MAX_TURNS: ClassVar[int] = 8

    def remembered(self, turn: DialogueTurn) -> "DialogueState":
        """This state with `turn` added, keeping only the last MAX_TURNS."""
        return replace(self, turns=(*self.turns, turn)[-self.MAX_TURNS :])
