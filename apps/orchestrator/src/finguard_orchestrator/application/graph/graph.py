"""The bounded investigation as a LangGraph StateGraph (Task 8).

    load_and_reserve → plan → validate_and_apply → execute_read_only → assess_result
                                    │                                    │
                                    ├→ present_and_commit ←──────────────┤ (stop)
                                    └→ END (delegated)        plan ←─────┘ (inspect)

LangGraph owns the sequencing and the loop, and nothing else. Every node is a thin adapter over the
engine's own pieces: the interpreter (`TurnOperationsPort`), `transitions.apply` for grounding and
validation, `executor.execute` over the banking ports, the presenter and the canonical store's
compare-and-set. The turn's reservation and its version check happen before the graph runs
(`graph_turn`); the node named for them re-checks the version against the store.

**What the checkpoint holds** (the graph state, written after every node): the graph and schema
versions, the turn's request id, account and conversation, the version it was made against, the
validated operations, the execution status and counters, and per risen merchant the two totals and
counts. Not the sentence, not a prompt, not a credential and no movement but the one inspected
(its amount and date). The sentence, the ports and the per-invocation cache travel in the runtime
context, which LangGraph never checkpoints.

**Budgets**, deterministic: at most `MAX_BANK_EXECUTIONS` banking port calls by the investigation
and `MAX_MODEL_CALLS` model calls per turn. The model may ask for the next operation only from the
allow-list `investigation.worth_inspecting` computed from banking's answer; anything else is
refused and the turn answers with what it has, marked partial.
"""

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Final, Literal, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Checkpointer

from finguard_orchestrator.application.engine import presenter
from finguard_orchestrator.application.engine.answer_turn import _applied as applied
from finguard_orchestrator.application.engine.executor import (
    Executed,
    MixedCurrenciesError,
    QueryRun,
    RankingRun,
    execute,
)
from finguard_orchestrator.application.graph import codec, investigation
from finguard_orchestrator.application.graph.investigation import Cause, MerchantChange
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.engine.grounding import Grounds
from finguard_orchestrator.domain.engine.operations import Compare, Delegate, Operation
from finguard_orchestrator.domain.engine.state import DialogueState, DialogueTurn
from finguard_orchestrator.domain.engine.transitions import Transition, Work
from finguard_orchestrator.domain.enums import GroupBy, Metric, PlanningOutcome
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.ports.account_directory import AccountDirectoryPort
from finguard_orchestrator.domain.ports.account_summary import AccountSummaryPort
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.domain.ports.dialogue_store import DialogueStorePort, StaleDialogueError
from finguard_orchestrator.domain.ports.financial_query import FinancialQueryPort
from finguard_orchestrator.domain.ports.investigation_assessor import (
    AssessmentFailedError,
    InspectedSummary,
    InvestigationAssessorPort,
    InvestigationSummary,
    MerchantSummary,
    Stop,
)
from finguard_orchestrator.domain.ports.turn_operations import TurnOperationsPort
from finguard_orchestrator.domain.services import answer_writer
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

logger = logging.getLogger(__name__)

# Bumped whenever the nodes or their order change: a checkpoint written by another graph is not
# resumed (`graph_turn`), because the node it would resume at may mean something else now.
GRAPH_VERSION: Final = "investigation/1"
# Bumped whenever a key of `TurnGraphState` changes meaning or shape.
STATE_SCHEMA_VERSION: Final = 1
MAX_BANK_EXECUTIONS: Final = 3
MAX_MODEL_CALLS: Final = 3
# A backstop under the budgets: each loop is plan, validate, execute, assess.
RECURSION_LIMIT: Final = 4 * (MAX_BANK_EXECUTIONS + 1) + 4

Status = Literal[
    "loaded",
    "planned",
    "delegated",
    "replied",
    "canonical",
    "investigate",
    "inspect",
    "refused",
    "executed",
    "committed",
]


class TurnGraphState(TypedDict, total=False):
    """The checkpointed state: plain data only (see the module docstring for what and why)."""

    graph_version: str
    schema_version: int
    turn_id: str
    account_id: str
    conversation_id: str
    expected_version: int
    ops: str  # codec.dumps of the interpreter's operations, once planned
    status: Status
    model_calls: int
    bank_executions: int
    # Per risen merchant: [merchant, earlier total, earlier count, latest total, latest count,
    # cause]; and both windows' totals with their currency.
    risen: list[list[Any]]
    totals: list[int]
    currency: str
    # Per inspected merchant: [merchant, largest amount or None, its ISO datetime or None].
    inspected: list[list[Any]]
    next_merchant: str | None
    narrative: str
    partial: bool
    refused: list[str]


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
    count_calls: Callable[[], int] = lambda: 0


@dataclass
class GraphContext:
    """What one invocation runs with and LangGraph never checkpoints. `cache` is rebuilt on a
    resume: everything in it is either re-read (state, grounds) or recomputed (the transition)."""

    sentence: str
    today: date
    reporting_timezone: str
    ports: GraphPorts
    cache: dict[str, Any] = field(default_factory=dict)


type Ctx = Runtime[GraphContext]


# --- nodes ----------------------------------------------------------------------------------------


async def load_and_reserve(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """The canonical state at the version the turn was reserved against, or stale."""
    await _canonical(state, runtime.context)
    return {"status": "loaded"}


async def plan(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """The interpreter's one structured call, on the first pass only. On a loop the operation is
    the one the assessment asked for, already in the state, and no model is called."""
    if state.get("next_merchant"):
        return {}
    ctx = runtime.context
    current = await _canonical(state, ctx)
    grounds = await _grounds(state, ctx)
    names = {holding.account_id: holding.display_name for holding in grounds.accounts}
    before = ctx.ports.count_calls()
    ops = await ctx.ports.interpreter.interpret(
        ctx.sentence, current, today=ctx.today, account_names=names
    )
    calls = max(1, ctx.ports.count_calls() - before)
    return {
        "ops": codec.dumps(ops),
        "status": "planned",
        "model_calls": state.get("model_calls", 0) + calls,
    }


async def validate_and_apply(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """The canonical transition for the interpreter's operations; the allow-list and the budget for
    the assessment's. Nothing here is decided by a model."""
    asked = state.get("next_merchant")
    if asked:
        allowed = set(_may_inspect(state))
        executions = state.get("bank_executions", 0)
        if asked not in allowed or executions + 1 > MAX_BANK_EXECUTIONS:
            reason = "not_allowed" if asked not in allowed else "bank_budget"
            logger.warning("graph.operation_refused reason=%s", reason)
            return {
                "status": "refused",
                "next_merchant": None,
                "partial": True,
                "refused": [*state.get("refused", []), reason],
            }
        return {"status": "inspect"}
    moved = await _transition(state, runtime.context)
    if isinstance(moved.reply, Delegate):
        return {"status": "delegated"}
    if moved.work is None:
        return {"status": "replied"}
    investigating = moved.work.kind == "COMPARE" and _by_merchant(codec.loads(state["ops"]))
    return {"status": "investigate" if investigating else "canonical"}


async def execute_read_only(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """Banking reads through the executor, and only reads: no operation that reaches here can move
    money, because none of the operations it runs can say so."""
    ctx = runtime.context
    moved = await _transition(state, ctx)
    work = moved.work
    assert work is not None
    if state["status"] == "canonical":
        ctx.cache["run"] = await _execute(work, state, ctx)
        return {"status": "executed", "bank_executions": state.get("bank_executions", 0) + 1}
    if state["status"] == "inspect":
        return await _inspect(state, work, ctx)
    assert isinstance(work.plan, ComparisonPlan)
    earlier_run = await _execute(_by_merchant_work(work, work.plan.earlier), state, ctx)
    latest_run = await _execute(_by_merchant_work(work, work.plan.latest), state, ctx)
    assert isinstance(earlier_run, QueryRun) and isinstance(latest_run, QueryRun)
    then, now = earlier_run.answer, latest_run.answer
    if then.currency != now.currency and not then.is_empty and not now.is_empty:
        raise MixedCurrenciesError(f"{then.currency} and {now.currency}")
    changes = investigation.risen(then, now)
    return {
        "status": "executed",
        "bank_executions": state.get("bank_executions", 0) + 2,
        "risen": [
            [
                c.merchant,
                c.earlier_total,
                c.earlier_count,
                c.latest_total,
                c.latest_count,
                c.cause.value,
            ]
            for c in changes
        ],
        "totals": [abs(then.total_minor), abs(now.total_minor)],
        "currency": now.currency,
        "inspected": [],
    }


async def assess_result(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """The assessment's separate structured call, only when banking's answer leaves a cause a
    single purchase could explain, and only within the model budget."""
    if "risen" not in state:
        return {}
    candidates = _may_inspect(state)
    if not candidates and not state.get("inspected"):
        return {}  # nothing to investigate and nothing to narrate
    if state.get("model_calls", 0) >= MAX_MODEL_CALLS:
        return {"partial": state.get("partial", False) or bool(candidates)}
    ctx = runtime.context
    before = ctx.ports.count_calls()
    try:
        decided = await ctx.ports.assessor.assess(_summary(state, candidates))
    except AssessmentFailedError:
        logger.warning("graph.assessment_failed")
        decided = None
    update: TurnGraphState = {
        "model_calls": state.get("model_calls", 0) + max(1, ctx.ports.count_calls() - before)
    }
    if decided is None:
        update["partial"] = state.get("partial", False) or bool(candidates)
    elif isinstance(decided, Stop):
        update["narrative"] = decided.why
    else:
        update["next_merchant"] = decided.merchant
    return update


async def present_and_commit(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """The presenter's answer, then the canonical compare-and-set. The answer and the saved state
    go to the invocation's cache: they are this turn's output, not something to resume."""
    ctx = runtime.context
    moved = await _transition(state, ctx)
    grounds = await _grounds(state, ctx)
    names = {holding.account_id: holding.display_name for holding in grounds.accounts}
    if moved.work is None:
        assert not isinstance(moved.reply, Delegate)
        result = presenter.replied(moved.reply, moved.state, names, today=ctx.today)
        in_words = result.text
    elif "risen" in state:
        result = _investigated(state, moved.work, names)
        in_words = result.plan_in_words or ""
    else:
        # A resume after the read and before this node has no run in its cache: reading again is
        # safe because it is only a read, and nothing was saved for the first one.
        run = ctx.cache.get("run") or await _execute(moved.work, state, ctx)
        result = presenter.answered(
            run,
            scope=moved.work.account_ids,
            account_names=names,
            reporting_timezone=ctx.reporting_timezone,
        )
        in_words = result.plan_in_words or ""
    saved = await ctx.ports.store.save(
        moved.state.remembered(DialogueTurn(said=ctx.sentence, answered=in_words)),
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["result"], ctx.cache["saved"] = result, saved
    return {"status": "committed"}


# --- routing --------------------------------------------------------------------------------------


def _after_validation(state: TurnGraphState) -> str:
    match state["status"]:
        case "delegated":
            return END
        case "replied" | "refused":
            return "present_and_commit"
        case _:
            return "execute_read_only"


def _after_assessment(state: TurnGraphState) -> str:
    return "plan" if state.get("next_merchant") else "present_and_commit"


def build(checkpointer: Checkpointer) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(TurnGraphState, context_schema=GraphContext)
    graph.add_node("load_and_reserve", load_and_reserve)
    graph.add_node("plan", plan)
    graph.add_node("validate_and_apply", validate_and_apply)
    graph.add_node("execute_read_only", execute_read_only)
    graph.add_node("assess_result", assess_result)
    graph.add_node("present_and_commit", present_and_commit)
    graph.add_edge(START, "load_and_reserve")
    graph.add_edge("load_and_reserve", "plan")
    graph.add_edge("plan", "validate_and_apply")
    graph.add_conditional_edges(
        "validate_and_apply", _after_validation, ["execute_read_only", "present_and_commit", END]
    )
    graph.add_edge("execute_read_only", "assess_result")
    graph.add_conditional_edges("assess_result", _after_assessment, ["plan", "present_and_commit"])
    graph.add_edge("present_and_commit", END)
    return graph.compile(checkpointer=checkpointer)


# --- adapters over the engine's pieces ------------------------------------------------------------


async def _canonical(state: TurnGraphState, ctx: GraphContext) -> DialogueState:
    """The store's state for this turn, re-read once per invocation; stale when it moved."""
    if "dialogue" not in ctx.cache:
        conversation_id = UUID(state["conversation_id"])
        held = await ctx.ports.store.load(conversation_id, account_id=UUID(state["account_id"]))
        current = held or DialogueState(conversation_id)
        if current.version != state["expected_version"]:
            raise StaleDialogueError(current)
        ctx.cache["dialogue"] = current
    dialogue: DialogueState = ctx.cache["dialogue"]
    return dialogue


async def _grounds(state: TurnGraphState, ctx: GraphContext) -> Grounds:
    if "grounds" not in ctx.cache:
        account_id = UUID(state["account_id"])
        ctx.cache["grounds"] = Grounds(
            ctx.ports.categories,
            await ctx.ports.accounts.all_of(account_id),
            await ctx.ports.contacts.all_of(account_id),
        )
    grounds: Grounds = ctx.cache["grounds"]
    return grounds


async def _transition(state: TurnGraphState, ctx: GraphContext) -> Transition:
    """`transitions.apply` over the planned operations, with the comparison's merchant breakdown
    set aside: a pair is one figure per side, so the canonical transition refuses a grouping on it,
    and the graph is what reads each side by merchant instead. Pure, so recomputing it on a resume
    gives the same transition."""
    if "transition" not in ctx.cache:
        ops = tuple(_without_merchant_grouping(op) for op in codec.loads(state["ops"]))
        current = await _canonical(state, ctx)
        ctx.cache["transition"] = applied(current, ops, ctx.today, await _grounds(state, ctx))
    transition: Transition = ctx.cache["transition"]
    return transition


def _by_merchant(ops: tuple[Operation, ...]) -> bool:
    """Whether the turn compares two windows by merchant: the capability's trigger, read off the
    interpreter's typed operations and never off the sentence."""
    return any(
        isinstance(op, Compare)
        and GroupBy.MERCHANT in (op.shared.group_by, op.earlier.group_by, op.latest.group_by)
        for op in ops
    )


def _without_merchant_grouping(op: Operation) -> Operation:
    if not isinstance(op, Compare) or not _by_merchant((op,)):
        return op
    return Compare(
        *(
            dataclasses.replace(side, group_by=None) if side.group_by is GroupBy.MERCHANT else side
            for side in (op.shared, op.earlier, op.latest)
        )
    )


def _by_merchant_work(work: Work, side: FinancialPlan) -> Work:
    """One side of the validated pair, read by merchant, in the scope the transition set."""
    by_merchant = replace(side, group_by=GroupBy.MERCHANT, metric=Metric.SUM, group_limit=None)
    return Work("QUERY", by_merchant, work.account_ids)


async def _execute(work: Work, state: TurnGraphState, ctx: GraphContext) -> Executed:
    return await execute(
        work,
        queries=ctx.ports.queries,
        summary=ctx.ports.summary,
        account_id=UUID(state["account_id"]),
        today=ctx.today,
        reporting_timezone=ctx.reporting_timezone,
    )


async def _inspect(state: TurnGraphState, work: Work, ctx: GraphContext) -> TurnGraphState:
    """The largest purchase at the merchant asked about, in the latest window and the same scope
    and filters the validated comparison carries: a ranking banking answers."""
    merchant = state["next_merchant"]
    assert merchant is not None and isinstance(work.plan, ComparisonPlan)
    latest = work.plan.latest
    ranking = TopMovements(
        direction=latest.direction,
        period=latest.period,
        date_range=latest.date_range,
        categories=latest.categories,
        excluded_categories=latest.excluded_categories,
        merchants=(merchant,),
        rank=1,
    )
    run = await _execute(Work("QUERY", ranking, work.account_ids), state, ctx)
    assert isinstance(run, RankingRun)
    largest = run.movements[0] if run.movements else None
    found = [
        merchant,
        None if largest is None else largest.amount_minor,
        None if largest is None else largest.occurred_at.isoformat(),
    ]
    return {
        "status": "executed",
        "bank_executions": state.get("bank_executions", 0) + 1,
        "inspected": [*state.get("inspected", []), found],
        "next_merchant": None,
    }


# --- the investigation's state, read back ---------------------------------------------------------


def _changes(state: TurnGraphState) -> tuple[MerchantChange, ...]:
    return tuple(
        MerchantChange(name, e_total, e_count, l_total, l_count, Cause(cause))
        for name, e_total, e_count, l_total, l_count, cause in state.get("risen", [])
    )


def _inspections(state: TurnGraphState) -> tuple[investigation.Inspection, ...]:
    by_name = {change.merchant: change for change in _changes(state)}
    found = []
    for merchant, amount, occurred_at in state.get("inspected", []):
        largest = None
        if amount is not None:
            largest = Movement(datetime.fromisoformat(occurred_at), "", amount, "EXPENSE")
        found.append(investigation.inspected(by_name[merchant], largest))
    return tuple(found)


def _may_inspect(state: TurnGraphState) -> tuple[str, ...]:
    done = {merchant for merchant, *_ in state.get("inspected", [])}
    return tuple(m for m in investigation.worth_inspecting(_changes(state)) if m not in done)


def _summary(state: TurnGraphState, candidates: tuple[str, ...]) -> InvestigationSummary:
    currency = state.get("currency", "EUR")
    inspected = []
    for found in _inspections(state):
        largest = "ninguna"
        if found.largest is not None:
            amount = answer_writer.money(abs(found.largest.amount_minor), currency)
            largest = f"{amount} el {found.largest.occurred_at.date().isoformat()}"
        inspected.append(InspectedSummary(found.merchant, found.finding or "NOT_FOUND", largest))
    return InvestigationSummary(
        risen=tuple(
            MerchantSummary(c.merchant, c.cause, c.earlier_count, c.latest_count)
            for c in _changes(state)
        ),
        inspected=tuple(inspected),
        may_inspect=candidates,
    )


def _investigated(
    state: TurnGraphState, work: Work, names: dict[UUID, str]
) -> AskFinancialQuestionResult:
    assert isinstance(work.plan, ComparisonPlan)
    earlier, latest = work.plan.earlier, work.plan.latest
    currency = state["currency"]
    then, now = state["totals"]
    grounded = investigation.answer_text(
        _changes(state),
        _inspections(state),
        earlier=earlier,
        latest=latest,
        earlier_answer=QueryAnswer(currency, then, 0),
        latest_answer=QueryAnswer(currency, now, 0),
        partial=state.get("partial", False),
    )
    text = grounded
    narrative = state.get("narrative", "")
    if narrative:
        checked = investigation.checked_narrative(narrative, grounded)
        if checked is None:
            logger.warning("graph.narrative_refused")
        else:
            text = f"{grounded}\n{checked}"
    text = f"{text}\n{presenter._scope_note(work.account_ids, names)}"
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.PLANNED.value,
        text=text,
        plan_in_words=(
            f"{answer_writer.plan_in_words(earlier)} frente a "
            f"{answer_writer.plan_in_words(latest)}, por comercio"
        ),
        currency=currency,
    )
