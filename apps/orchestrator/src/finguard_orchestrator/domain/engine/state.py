"""The engine's one canonical picture of a conversation.

Everything a later turn may refer to lives here: what is on screen (the focus), the question left
open (pending), the accounts being looked at (scope) and the last turns in words. One place, so
there is nothing for two copies of the same fact to disagree about.

`version` is what makes the state safe to share between requests: the store refuses a save made
against a version that is no longer current.
"""

import dataclasses
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from enum import StrEnum
from typing import ClassVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from finguard_orchestrator.domain.constants import MAX_PAYMENT_MINOR, MAX_SUPPORTING_MOVEMENTS
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, ResultKind
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.chart import ChartSpec, VisualDataset, VisualProfile
from finguard_orchestrator.domain.models.payment_result import PaymentPreview
from finguard_orchestrator.domain.models.query_answer import Group
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.movement_list_plan import MovementListPlan
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
    CORRECTION = "CORRECTION"
    TASK_LIMIT = "TASK_LIMIT"


@dataclass(frozen=True)
class QueryFocus:
    plan: FinancialPlan | TopMovements


@dataclass(frozen=True)
class MovementListFocus:
    plan: MovementListPlan


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


Focus = QueryFocus | MovementListFocus | ComparisonFocus | RatioFocus | BalanceFocus


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
    # Desired result shape is separate from metric: COUNT may be a scalar answer or accompany a
    # RECORDS collection. None is reserved for old checkpoints and non-query operations.
    result_kind: ResultKind | None = None
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
class Scope:
    """The accounts being looked at. Empty means every account, not none."""

    account_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class Pending:
    """An unfinished task: the clarification draft and its own account scope.

    The active screen may belong to an older task and have a different scope. Keeping scope here
    prevents a pending query from inheriting that screen's account filter or losing its own. The
    opaque id lets a later turn resume the task the person named when several drafts are suspended.
    """

    missing: Missing
    draft: PlanFields
    scope: Scope = Scope()
    # Equality compares the unfinished intent's content; routing uses task_id explicitly.
    task_id: str = field(default_factory=lambda: uuid4().hex, compare=False)

    def __post_init__(self) -> None:
        try:
            valid_id = UUID(hex=self.task_id).hex == self.task_id
        except (TypeError, ValueError):
            valid_id = False
        if not valid_id:
            raise InvalidValueError("pending task id must be an opaque UUID")


@dataclass(frozen=True)
class PreviousFocus:
    """The last answered focus and account selection, for an explicit return operation."""

    focus: Focus
    scope: Scope


@dataclass(frozen=True)
class FocusHistoryEntry:
    """A prior answered task and the account selection that produced it."""

    focus: Focus
    scope: Scope
    # Stable while this entry is in history. The model can resume a named task without guessing
    # which relative position the person meant; equality still deduplicates identical focus/scope.
    task_id: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.task_id:
            # DialogueState fills a restart-stable, conversation-scoped id during migration.
            return
        try:
            valid_id = UUID(hex=self.task_id).hex == self.task_id
        except (TypeError, ValueError):
            valid_id = False
        if not valid_id:
            raise InvalidValueError("focus task id must be an opaque UUID")


@dataclass(frozen=True)
class DialogueTurn:
    """`answered` is the plan in words and never its figures: a figure in the prompt is a figure
    a model can repeat, and every figure must come from the bank."""

    said: str
    answered: str


class ArtifactStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"


class ArtifactSource(StrEnum):
    BANKING_API = "BANKING_API"


@dataclass(frozen=True)
class FinancialArtifact:
    """Bounded, bank-backed financial data with semantic identity and verified provenance.

    This first artifact version retains the aggregate shapes needed for chart edits and comparison
    views. It deliberately carries no movement rows; record artifacts will use a separately scoped
    data reference instead of copying a transaction ledger into conversational checkpoints.
    """

    plan: FinancialPlan
    currency: str
    total_minor: int
    movement_count: int
    groups: tuple[Group, ...]
    # Stable identities let follow-up operations address the plan and its bank-authored result
    # without treating an aggregate, a chart, and a query as interchangeable.
    query_id: str = ""
    artifact_id: str = ""
    period_start: date | None = None
    period_end: date | None = None
    result_version: int = 1
    comparison: ComparisonPlan | None = None
    comparison_groups: tuple[tuple[Group, ...], tuple[Group, ...]] | None = None
    comparison_values: tuple[int, int] | None = None
    result_kind: ResultKind = ResultKind.SCALAR
    status: ArtifactStatus = ArtifactStatus.VERIFIED
    source: ArtifactSource = ArtifactSource.BANKING_API
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.result_kind is ResultKind.COMPARISON) != (self.comparison is not None):
            raise ValueError("comparison artifact kind and operands must agree")
        if self.status is ArtifactStatus.EMPTY and self.movement_count != 0:
            raise ValueError("empty financial artifact cannot contain movements")
        if len(self.groups) > 100 or any(len(group.key) > 256 for group in self.groups):
            raise ValueError("financial artifact exceeds its bounded result size")
        if self.comparison_groups is not None and any(
            len(side) > 100 or any(len(group.key) > 256 for group in side)
            for side in self.comparison_groups
        ):
            raise ValueError("financial comparison artifact exceeds its bounded result size")
        if (self.comparison is None) != (self.comparison_groups is None):
            raise ValueError("comparison plan and aggregates must be stored together")
        if self.comparison_values is not None and self.comparison is None:
            raise ValueError("comparison values need a comparison plan")
        if self.comparison_values is not None and (
            len(self.comparison_values) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in self.comparison_values
            )
        ):
            raise ValueError("comparison values must contain two integer sides")


@dataclass(frozen=True)
class MovementListArtifact:
    """A bounded chart-ready summary of one bank-returned movement page.

    It retains grouped labels and amounts, never ledger rows, descriptions, or transaction ids.
    The title and count keep the scope honest: this visual describes the displayed page, not every
    movement matching a wider query.
    """

    dataset: VisualDataset
    currency: str
    movement_count: int
    group_by: GroupBy
    result_version: int
    query_id: str
    artifact_id: str

    def __post_init__(self) -> None:
        if not 1 <= self.movement_count <= MAX_SUPPORTING_MOVEMENTS or self.result_version < 1:
            raise ValueError("movement visual artifact has invalid count or version")
        if len(self.dataset.rows) > MAX_SUPPORTING_MOVEMENTS or any(
            not row.dimension.strip()
            or len(row.dimension) > 256
            or isinstance(row.value_minor, bool)
            or not isinstance(row.value_minor, int)
            for row in self.dataset.rows
        ):
            raise ValueError("movement visual artifact exceeds its bounded result size")
        if not self.dataset.rows or self.dataset.metric != "SUM":
            raise ValueError("movement visual artifact requires summarized amount rows")
        if not self.currency.strip():
            raise ValueError("movement visual artifact requires a currency")
        if self.group_by not in {GroupBy.CATEGORY, GroupBy.MERCHANT}:
            raise ValueError("movement visual artifact needs a supported ledger dimension")


@dataclass(frozen=True)
class ActiveVisual:
    """The latest verified visual over a persisted result artifact, with explicit lineage.

    Versions bind edits to the exact source result.
    """

    result_version: int
    version: int
    spec: ChartSpec
    profile: VisualProfile
    view_id: str = ""

    def __post_init__(self) -> None:
        if self.result_version < 1 or self.version < 1:
            raise ValueError("active visual versions must be positive")


@dataclass(frozen=True)
class PaymentDraftPending:
    """A payment paused while it still needs a recipient or amount.

    `suspended` means the holder changed subjects after the draft asked a question. A bare amount
    or contact then stays ambiguous until the holder explicitly resumes the payment.
    """

    draft: PaymentDraft
    candidate_contact_ids: tuple[UUID, ...] = ()
    suspended: bool = False

    def __post_init__(self) -> None:
        if len(set(self.candidate_contact_ids)) != len(self.candidate_contact_ids):
            raise InvalidValueError("payment contact candidates must be unique")


@dataclass(frozen=True)
class OwnTransferDraftPending:
    """A transfer between the holder's accounts, waiting for a destination and/or amount."""

    to_account_id: UUID | None = None
    amount_minor: int | None = None
    candidate_account_ids: tuple[UUID, ...] = ()
    workflow_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if self.amount_minor is not None and (
            isinstance(self.amount_minor, bool)
            or not isinstance(self.amount_minor, int)
            or not 0 < self.amount_minor <= MAX_PAYMENT_MINOR
        ):
            raise InvalidValueError("transfer amount must be a positive bounded integer")
        if len(set(self.candidate_account_ids)) != len(self.candidate_account_ids):
            raise InvalidValueError("transfer destination candidates must be unique")
        try:
            is_uuid_hex = UUID(hex=self.workflow_id).hex == self.workflow_id
        except (TypeError, ValueError):
            is_uuid_hex = False
        if not is_uuid_hex:
            raise InvalidValueError("transfer workflow id must be an opaque UUID")


@dataclass(frozen=True)
class PaymentConfirmationPending:
    """The exact bank preview awaiting an explicit confirmation reply.

    The preview is bank-authored and includes its intent id, destination, amount, currency,
    expiry, and resulting balance. Keeping it in account-scoped canonical state lets a later graph
    turn resume the confirmation without trusting client-supplied payment details.
    """

    preview: PaymentPreview
    # Stable conversation workflow identity, independent from LangGraph checkpoint threads. It
    # never authorizes a payment; the exact preview and bank command remain authoritative.
    workflow_id: str | None = None
    # The user-supplied, bank-grounded fields that produced this preview. Retaining the draft lets
    # the planner interpret a later correction without asking the user to repeat the payee or note.
    # The preview remains authoritative for the confirmation that is currently open.
    draft: PaymentDraft | None = None


PaymentWorkflow = PaymentDraftPending | PaymentConfirmationPending


@dataclass(frozen=True)
class DialogueState:
    conversation_id: UUID
    version: int = 0
    focus: Focus | None = None
    pending: Pending | None = None
    # Clarifications belong to tasks, not to the conversation's single current focus. Keep
    # bounded suspended drafts when a semantically new task takes the floor; when full, the
    # transition refuses a new task instead of evicting one. The interpreter must name a task ID
    # when the user returns to one, so recency never chooses the resumed goal.
    suspended_pending: tuple[Pending, ...] = ()
    scope: Scope = Scope()
    turns: tuple[DialogueTurn, ...] = ()
    active_artifact: FinancialArtifact | None = None
    active_movement_artifact: MovementListArtifact | None = None
    active_visual: ActiveVisual | None = None
    payment: PaymentWorkflow | None = None
    own_transfer: OwnTransferDraftPending | None = None
    previous_focus: PreviousFocus | None = None
    focus_history: tuple[FocusHistoryEntry, ...] = ()

    # Keep enough answered tasks for the product's 10–20 turn banking sessions while still
    # bounding both the canonical payload and the planner context.
    MAX_FOCUS_HISTORY: ClassVar[int] = 16
    MAX_SUSPENDED_PENDING: ClassVar[int] = 8

    # Recent utterances are clipped before entering the interpreter prompt. Keep enough for a
    # 10–20 turn banking session while the canonical typed state remains the source of truth.
    MAX_TURNS: ClassVar[int] = 16

    def __post_init__(self) -> None:
        history = tuple(
            entry
            if entry.task_id
            else replace(
                entry,
                task_id=_migrated_focus_task_id(self.conversation_id, entry),
            )
            for entry in self.focus_history
        )
        if history != self.focus_history:
            object.__setattr__(self, "focus_history", history)
        if len(self.focus_history) > self.MAX_FOCUS_HISTORY:
            raise ValueError("focus history exceeds its bounded size")
        if len(self.suspended_pending) > self.MAX_SUSPENDED_PENDING:
            raise ValueError("suspended clarification history exceeds its bounded size")
        task_ids = [task.task_id for task in self.suspended_pending]
        task_ids.extend(task.task_id for task in self.focus_history)
        if self.pending is not None:
            task_ids.append(self.pending.task_id)
        if len(task_ids) != len(set(task_ids)):
            raise InvalidValueError("unfinished conversation tasks must have unique ids")

    def remembered(self, turn: DialogueTurn) -> "DialogueState":
        """This state with `turn` added, keeping only the last MAX_TURNS."""
        return replace(self, turns=(*self.turns, turn)[-self.MAX_TURNS :])


def _migrated_focus_task_id(conversation_id: UUID, entry: FocusHistoryEntry) -> str:
    """Derive an old entry's id from its typed data, not Python's display representation.

    The versioned canonical JSON keeps a checkpoint's task selector stable across process restarts
    and harmless changes to dataclass ``repr`` output. UUIDs and dates are serialized as strings;
    StrEnum values remain their stable wire values.
    """
    identity = {
        "focus_type": f"{type(entry.focus).__module__}.{type(entry.focus).__qualname__}",
        "focus": asdict(entry.focus),
        "scope": asdict(entry.scope),
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return uuid5(
        NAMESPACE_URL,
        f"finguard:focus-task:v1:{conversation_id}:{canonical}",
    ).hex
