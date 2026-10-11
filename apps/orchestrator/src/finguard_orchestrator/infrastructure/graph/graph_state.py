"""Typed state and per-invocation dependencies for the LangGraph runtime.

Keeping checkpoint shape and runtime-only ports outside the node module gives workflow slices a
shared contract without importing the whole graph implementation or creating cycles.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, TypedDict
from uuid import UUID

from langgraph.runtime import Runtime

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.domain.ports.account_directory import AccountDirectoryPort
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.domain.ports.dialogue_store import DialogueStorePort
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.ports.investigation_assessor import InvestigationAssessorPort
from finguard_orchestrator.domain.ports.turn_operations import TurnOperationsPort
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy

Status = Literal[
    "loaded",
    "planned",
    "delegated",
    "visualize",
    "needs_visualization",
    "replied",
    "canonical",
    "compare",
    "investigate",
    "compare_latest",
    "inspect",
    "refused",
    "executed",
    "committed",
    "payment",
    "paused_payment",
    "payment_waiting",
    "payment_interpretation",
    "cancel_payment_draft",
    "payment_complete",
]


class TurnGraphState(TypedDict, total=False):
    """The checkpointed state: plain data only (see the graph module docstring)."""

    graph_version: str
    schema_version: int
    turn_id: str
    account_id: str
    conversation_id: str
    expected_version: int
    ops: str  # codec.dumps of the interpreter's operations, once planned
    status: Status
    model_calls: int
    # Every banking port call this turn made, failed ones included (R44).
    bank_calls: int
    # Earlier window rows as [group, total, count], then its total and currency;
    # cleared after latest.
    earlier_groups: list[list[Any]]
    comparison_groups: list[list[list[Any]]]
    comparison_counts: list[int]
    earlier_movement_count: int
    earlier_total: int
    earlier_currency: str
    # Merchant, earlier total/count, latest total/count, explanation, decreased; plus both totals.
    changes: list[list[Any]]
    totals: list[int]
    currency: str
    # Per inspected merchant: name, largest amount or None, and its ISO datetime or None.
    inspected: list[list[Any]]
    # Index into the current bank-derived allow-list, or -1 if the model requested an unknown name.
    next_inspect: int | None
    partial: bool
    refused: list[str]
    payment_answer: str
    payment_reply: str
    # Canonical payment workflow identity, independent from this request's checkpoint thread.
    payment_workflow_id: str
    direct_payment_confirmation: bool
    pending_state: str
    pending_result: str
    # Closed result for crash recovery; may hold only the bounded bank rows returned this turn.
    execution_result: str
    # Validated visualization intent, held until the query artifact exists.
    visual_after_query: bool
    visual_style: Literal["AUTO", "BAR", "LINE"]
    rearm_payment: bool


@dataclass(frozen=True)
class GraphPorts:
    interpreter: TurnOperationsPort
    assessor: InvestigationAssessorPort
    store: DialogueStorePort
    queries: FinancialQueryPort
    summary: AccountSummaryPort
    accounts: AccountDirectoryPort
    contacts: ContactDirectoryPort
    categories: frozenset[str]
    visual_policy: VisualPlanningPolicy | None = None
    count_calls: Callable[[], int] = lambda: 0
    commands: CommandBus | None = None


@dataclass
class GraphContext:
    """Invocation-only data. State, sentence, ports and cache have distinct lifetimes."""

    sentence: str
    today: date
    reporting_timezone: str
    ports: GraphPorts
    caller_scopes: frozenset[str] = frozenset()
    thread_id: str = ""
    request_id: str | None = None
    lease_token: UUID | None = None
    call_offset: int = 0
    # Model calls already represented by the checkpoint when this invocation began. Failed
    # provider attempts are added during cleanup without double-counting completed nodes.
    checkpoint_model_calls: int = 0
    financial_reads: int = 0
    cache: dict[str, Any] = field(default_factory=dict)
    # Port calls since the last graph checkpoint; persisted into `bank_calls` on node return.
    unsaved_calls: int = 0


Ctx = Runtime[GraphContext]
