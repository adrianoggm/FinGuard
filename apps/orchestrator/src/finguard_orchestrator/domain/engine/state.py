"""The engine's one canonical picture of a conversation.

Everything a later turn may refer to lives here: what is on screen (the focus), the question left
open (pending), the accounts being looked at (scope) and the last turns in words. One place, so
there is nothing for two copies of the same fact to disagree about.

`version` is what makes the state safe to share between requests: the store refuses a save made
against a version that is no longer current.
"""

import dataclasses
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric
from finguard_orchestrator.domain.models.chart import ChartSpec, VisualProfile
from finguard_orchestrator.domain.models.payment_result import PaymentPreview
from finguard_orchestrator.domain.models.query_answer import Group
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements


class Measure(StrEnum):
    """How the figure on screen is shown. PERCENT_CHANGE and SHARE are the two no metric says:
    each is arithmetic over two reads (a pair, or a part and its whole), which is why they live on
    the focus and not on the plans."""

    AMOUNT = "AMOUNT"
    COUNT = "COUNT"
    # Per movement, against PERIOD_AVERAGE per calendar bucket: two different figures, as in
    # `Metric`, so a label that merged them would let «en media» quietly change the divisor.
    AVERAGE = "AVERAGE"
    PERIOD_AVERAGE = "PERIOD_AVERAGE"
    PERCENT_CHANGE = "PERCENT_CHANGE"
    SHARE = "SHARE"


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
class PlanFields:
    """A plan described field by field, every field optional. None is "not said"; an empty tuple
    is said, and means none. Names are as the model wrote them until grounding replaces them.

    Defined here rather than beside the operations that carry it because a pending question holds
    one, and a type another module defines would be a forward reference nobody can resolve.
    """

    direction: Direction | None = None
    window: RelativePeriod | AbsoluteRange | None = None
    categories: tuple[str, ...] | None = None
    merchants: tuple[str, ...] | None = None
    merchant_terms: tuple[str, ...] | None = None
    people: tuple[str, ...] | None = None
    accounts: tuple[str, ...] | None = None
    excluded_categories: tuple[str, ...] | None = None
    excluded_merchants: tuple[str, ...] | None = None
    metric: Metric | None = None
    group_by: GroupBy | None = None
    limit: int | None = None
    clear_limit: bool | None = None

    def named(self) -> frozenset[str]:
        """The fields that were said."""
        return frozenset(
            field.name
            for field in dataclasses.fields(self)
            if getattr(self, field.name) is not None
        )

    def merged(self, changes: "PlanFields") -> "PlanFields":
        """These fields with every field `changes` names replaced, and no other."""
        updates = {
            name: getattr(changes, name) for name in changes.named() if name != "clear_limit"
        }
        if changes.clear_limit:
            updates["limit"] = None
        updates["clear_limit"] = None
        return dataclasses.replace(self, **updates)


@dataclass(frozen=True)
class Pending:
    """A question asked back, holding everything already said, so the reply only fills the gap."""

    missing: Missing
    draft: PlanFields


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
class ActiveQueryResult:
    """Bounded, aggregate-only result that later graph operations may visualize or refine.

    It deliberately carries no movement rows. The original plan and aggregates let a chart edit
    reuse what banking already answered without copying a transaction ledger into checkpoints.
    """

    plan: FinancialPlan
    currency: str
    total_minor: int
    movement_count: int
    groups: tuple[Group, ...]
    period_start: date | None = None
    period_end: date | None = None
    result_version: int = 1

    def __post_init__(self) -> None:
        if len(self.groups) > 100 or any(len(group.key) > 256 for group in self.groups):
            raise ValueError("active query result exceeds its bounded artifact size")


@dataclass(frozen=True)
class ActiveVisual:
    """The latest verified visual over an aggregate result, with explicit lineage/versioning."""

    result_version: int
    version: int
    spec: ChartSpec
    profile: VisualProfile

    def __post_init__(self) -> None:
        if self.result_version < 1 or self.version < 1:
            raise ValueError("active visual versions must be positive")


@dataclass(frozen=True)
class PaymentDraftPending:
    """A payment paused while it still needs a recipient or amount."""

    draft: PaymentDraft


@dataclass(frozen=True)
class PaymentConfirmationPending:
    """The exact bank preview awaiting an explicit confirmation reply.

    The preview is bank-authored and includes its intent id, destination, amount, currency,
    expiry, and resulting balance. Keeping it in account-scoped canonical state lets a later graph
    turn resume the confirmation without trusting client-supplied payment details.
    """

    preview: PaymentPreview


PaymentWorkflow = PaymentDraftPending | PaymentConfirmationPending


@dataclass(frozen=True)
class DialogueState:
    conversation_id: UUID
    version: int = 0
    focus: Focus | None = None
    pending: Pending | None = None
    scope: Scope = Scope()
    turns: tuple[DialogueTurn, ...] = ()
    active_result: ActiveQueryResult | None = None
    active_visual: ActiveVisual | None = None
    payment: PaymentWorkflow | None = None

    # What bounds the state the interpreter's prompt carries.
    MAX_TURNS: ClassVar[int] = 8

    def remembered(self, turn: DialogueTurn) -> "DialogueState":
        """This state with `turn` added, keeping only the last MAX_TURNS."""
        return replace(self, turns=(*self.turns, turn)[-self.MAX_TURNS :])
