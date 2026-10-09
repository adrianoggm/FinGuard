"""The unified conversational turn as a bounded LangGraph StateGraph.

    load_and_reserve → plan → validate_and_apply → execute_read_only → assess_result
                                    │                                    │
                                    ├→ present_and_commit ←──────────────┤ (stop)
                                    └→ END (delegated)        plan ←─────┘ (inspect)

LangGraph owns sequencing, bounded analysis, visualization and payment pause/resume. Nodes adapt
the existing typed interpreter, transition validation, banking ports, guarded payment commands,
presenter and canonical store. The turn reservation and version check remain outside the graph
(`graph_turn`); the persistence nodes still perform compare-and-set.

**What the checkpoint holds** (the graph state, written after every node): the graph and schema
versions, the turn's request id, account and conversation, the version it was made against, the
validated operations, the execution status and counters, and per risen merchant the two totals and
counts. Not the sentence, not a prompt, not a credential and no movement but the one inspected
(its amount and date). The sentence, the ports and the per-invocation cache travel in the runtime
context, which LangGraph never checkpoints.

**Budgets**, deterministic: at most `MAX_BANK_EXECUTIONS` banking port calls and
`MAX_MODEL_CALLS` model calls per turn. Every port call counts, including failures. A payment
confirmation is never inferred: the graph interrupts with the exact bank preview and the resume
node rechecks account, scope, workflow and pending state before invoking the existing guarded
command. Analysis can loop only over the bounded merchants banking returned; unsupported next
steps are refused and any partial answer is marked.
"""

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Final, Literal, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Checkpointer, interrupt

from finguard_orchestrator.application import CommandBus
from finguard_orchestrator.application.commands import payment_input
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    PaymentAnswer,
    PrepareChosenPaymentCommand,
    PrepareOwnTransferCommand,
)
from finguard_orchestrator.application.engine import presenter
from finguard_orchestrator.application.engine.executor import (
    Executed,
    MixedCurrenciesError,
    QueryRun,
    RankingRun,
    execute,
)
from finguard_orchestrator.application.engine.transition_guard import apply_guarded
from finguard_orchestrator.application.graph import codec, investigation
from finguard_orchestrator.application.graph.investigation import Explanation, MerchantChange
from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionResult,
    SpokenPlan,
)
from finguard_orchestrator.domain.engine.grounding import Grounds, ground
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Balance,
    ChangeMeasure,
    Compare,
    Delegate,
    Operation,
    Patch,
    PlanFields,
    PrepareBizum,
    PrepareOwnTransfer,
    Reset,
    SelectOperand,
    Visualize,
)
from finguard_orchestrator.domain.engine.state import (
    ActiveQueryResult,
    ActiveVisual,
    DialogueState,
    DialogueTurn,
    OwnTransferDraftPending,
    PaymentConfirmationPending,
    PaymentDraftPending,
)
from finguard_orchestrator.domain.engine.transitions import Transition, Work
from finguard_orchestrator.domain.enums import (
    GroupBy,
    Metric,
    PaymentOutcome,
    PlanningOutcome,
    Rail,
)
from finguard_orchestrator.domain.errors import DomainError, NotAuthorisedError
from finguard_orchestrator.domain.models.account_summary import AccountSummary
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    Mark,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.models.conversation import Conversation, PaymentConfirmation
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
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
from finguard_orchestrator.domain.services import (
    answer_writer,
    category_words,
    payment_writer,
)
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, FinancialPlan
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

logger = logging.getLogger(__name__)

# Bumped whenever the nodes or their order change: a checkpoint written by another graph is not
# resumed (`graph_turn`), because the node it would resume at may mean something else now.
GRAPH_VERSION: Final = "conversation/9"
# Bumped whenever a key of `TurnGraphState` changes meaning or shape.
STATE_SCHEMA_VERSION: Final = 9
MAX_BANK_EXECUTIONS: Final = 3
MAX_MODEL_CALLS: Final = 3
# A backstop under the budgets: one step per read, and plan, validate and assess per loop.
RECURSION_LIMIT: Final = 4 * (MAX_BANK_EXECUTIONS + 1) + 4


class ReadBudgetExhaustedError(DomainError):
    """The reads the investigation still needs would go past `MAX_BANK_EXECUTIONS`, failed reads
    counted. Answered as such, with nothing saved."""

    code = "read_budget_exhausted"


Status = Literal[
    "loaded",
    "planned",
    "delegated",
    "visualize",
    "replied",
    "canonical",
    "investigate",
    "inspect",
    "refused",
    "executed",
    "committed",
    "payment",
    "payment_waiting",
    "payment_complete",
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
    # Every banking port call this turn made, failed ones included (R44).
    bank_calls: int
    # The earlier window by merchant, kept between its read and the latest one's: [merchant,
    # total, count] per merchant, its total and its currency. Emptied once the latest is read.
    earlier_groups: list[list[Any]]
    earlier_total: int
    earlier_currency: str
    # Per risen merchant: [merchant, earlier total, earlier count, latest total, latest count,
    # explanation]; and both windows' totals with their currency.
    risen: list[list[Any]]
    totals: list[int]
    currency: str
    # Per inspected merchant: [merchant, largest amount or None, its ISO datetime or None].
    inspected: list[list[Any]]
    # The assessment's choice as a position in `_may_inspect(state)`, or -1 for a merchant the
    # allow-list does not hold: the string the model wrote is never stored (R45).
    next_inspect: int | None
    partial: bool
    refused: list[str]
    payment_answer: str
    payment_reply: str
    pending_state: str
    pending_result: str


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
    """What one invocation runs with and LangGraph never checkpoints. `cache` is rebuilt on a
    resume: everything in it is either re-read (state, grounds) or recomputed (the transition)."""

    sentence: str
    today: date
    reporting_timezone: str
    ports: GraphPorts
    caller_scopes: frozenset[str] = frozenset()
    thread_id: str = ""
    cache: dict[str, Any] = field(default_factory=dict)
    # Banking port calls made since the last checkpoint: a node adds them to `bank_calls` when it
    # returns, and the handler writes them to the checkpoint when it does not (R44).
    unsaved_calls: int = 0


type Ctx = Runtime[GraphContext]


# --- nodes ----------------------------------------------------------------------------------------


async def load_and_reserve(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """The canonical state at the version the turn was reserved against, or stale."""
    await _canonical(state, runtime.context)
    return {"status": "loaded"}


async def plan(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """The interpreter's one structured call, on the first pass only. On a loop the operation is
    the one the assessment asked for, already in the state, and no model is called."""
    if state.get("next_inspect") is not None:
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
    asked = state.get("next_inspect")
    if asked is not None:
        allowed = 0 <= asked < len(_may_inspect(state))
        calls = state.get("bank_calls", 0)
        if not allowed or calls + 1 > MAX_BANK_EXECUTIONS:
            reason = "not_allowed" if not allowed else "bank_budget"
            logger.warning("graph.operation_refused reason=%s", reason)
            return {
                "status": "refused",
                "next_inspect": None,
                "partial": True,
                "refused": [*state.get("refused", []), reason],
            }
        return {"status": "inspect"}
    ops = codec.loads(state["ops"])
    payments = tuple(op for op in ops if isinstance(op, (PrepareBizum, PrepareOwnTransfer)))
    if payments:
        analytics_ops = (Ask, Patch, Compare, ChangeMeasure, SelectOperand, Reset, Balance)
        if len(payments) != 1 or any(
            not isinstance(op, (*analytics_ops, PrepareBizum, PrepareOwnTransfer)) for op in ops
        ):
            return {"status": "payment", "refused": [*state.get("refused", []), "composition"]}
        if Scope.PAYMENTS_PREPARE.value not in runtime.context.caller_scopes:
            raise NotAuthorisedError(
                f"this credential is not allowed {Scope.PAYMENTS_PREPARE.value}"
            )
        if all(isinstance(op, (PrepareBizum, PrepareOwnTransfer)) for op in ops):
            return {"status": "payment"}
    visual = next((op for op in ops if isinstance(op, Visualize)), None)
    if visual is not None:
        return {"status": "visualize"}
    moved = await _transition(state, runtime.context)
    if isinstance(moved.reply, Delegate):
        return {"status": "visualize" if moved.reply.to == "CHART" else "delegated"}
    if moved.work is None:
        return {"status": "replied"}
    investigating = moved.work.kind == "COMPARE" and _by_merchant(codec.loads(state["ops"]))
    return {"status": "investigate" if investigating else "canonical"}


async def execute_read_only(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """One banking read per step, through the executor, and only reads: no operation that reaches
    here can move money, because none of the operations it runs can say so. What was read goes into
    the checkpoint, so a resume after a failed read starts at that read."""
    ctx = runtime.context
    work = (await _transition(state, ctx)).work
    assert work is not None and isinstance(work.plan, ComparisonPlan)
    made = state.get("bank_calls", 0)
    if made + 1 > MAX_BANK_EXECUTIONS:
        if state["status"] == "inspect":
            # The pair is read: the answer stands without the inspection, and says it stopped.
            return {"status": "executed", "next_inspect": None, "partial": True}
        raise ReadBudgetExhaustedError(f"{made} reads made")
    if state["status"] == "inspect":
        return await _inspect(state, work, ctx)
    if "earlier_groups" not in state:
        earlier = await _read_by_merchant(work, work.plan.earlier, state, ctx)
        return {
            "earlier_groups": [[g.key, g.total_minor, g.movement_count] for g in earlier.groups],
            "earlier_total": abs(earlier.total_minor),
            "earlier_currency": earlier.currency,
            "bank_calls": _bank_calls(state, ctx),
        }
    now = await _read_by_merchant(work, work.plan.latest, state, ctx)
    then = QueryAnswer(
        state["earlier_currency"],
        state["earlier_total"],
        sum(group[2] for group in state["earlier_groups"]),
        groups=tuple(Group(*group) for group in state["earlier_groups"]),
    )
    if then.currency != now.currency and not then.is_empty and not now.is_empty:
        raise MixedCurrenciesError(f"{then.currency} and {now.currency}")
    changes = investigation.risen(then, now)
    return {
        "status": "executed",
        "bank_calls": _bank_calls(state, ctx),
        "earlier_groups": [],
        "risen": [
            [
                c.merchant,
                c.earlier_total,
                c.earlier_count,
                c.latest_total,
                c.latest_count,
                c.explanation.value,
            ]
            for c in changes
        ],
        "totals": [state["earlier_total"], abs(now.total_minor)],
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
    if (
        state.get("model_calls", 0) >= MAX_MODEL_CALLS
        or state.get("bank_calls", 0) >= MAX_BANK_EXECUTIONS
    ):
        # Nothing the assessment could ask for could be read, or it could not be asked.
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
    elif not isinstance(decided, Stop):
        chosen = decided.merchant
        update["next_inspect"] = candidates.index(chosen) if chosen in candidates else -1
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
        # An ordinary turn reads and answers in this one step, as the engine does: there is no
        # earlier read to keep. Its port calls are counted, not capped (the measured engine's work).
        run = await _execute(moved.work, state, ctx)
        ctx.cache["run"] = run
        result = presenter.answered(
            run,
            scope=moved.work.account_ids,
            account_names=names,
            reporting_timezone=ctx.reporting_timezone,
        )
        in_words = result.plan_in_words or ""
    canonical = moved.state
    # Keep the bounded aggregate answer available for later graph visualization operations.
    # Movement evidence remains in the response and is never copied into dialogue state.
    final_run = ctx.cache.get("run")
    if moved.work is not None:
        # Do not let a chart request after a different kind of answer accidentally visualize an
        # older query. A completed query below installs its new bounded result.
        canonical = replace(canonical, active_result=None, active_visual=None)
    if isinstance(final_run, QueryRun):
        groups = final_run.answer.groups[:100]
        active_result = (
            ActiveQueryResult(
                plan=final_run.plan,
                currency=final_run.answer.currency,
                total_minor=final_run.answer.total_minor,
                movement_count=final_run.answer.movement_count,
                groups=groups,
                period_start=final_run.answer.period_start,
                period_end=final_run.answer.period_end,
                result_version=state["expected_version"] + 1,
            )
            if all(len(group.key) <= 256 for group in groups)
            else None
        )
        canonical = replace(canonical, active_result=active_result)
    remembered = canonical.remembered(DialogueTurn(said=ctx.sentence, answered=in_words))
    if any(isinstance(op, (PrepareBizum, PrepareOwnTransfer)) for op in codec.loads(state["ops"])):
        # A mixed analytics/payment turn has one canonical commit. Keep the bounded typed result in
        # the graph checkpoint while payment preparation runs so a retry never repeats the query.
        ctx.cache["result"] = result
        return {
            "status": "committed",
            "pending_state": codec.dumps_state(remembered),
            "pending_result": codec.dumps_typed(result),
            "bank_calls": _bank_calls(state, ctx),
        }
    saved = await ctx.ports.store.save(
        remembered,
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    ctx.cache["result"], ctx.cache["saved"] = result, saved
    return {
        "status": "committed",
        "expected_version": saved.version,
        "bank_calls": _bank_calls(state, ctx),
    }


async def prepare_payment(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """Ground the model's typed Bizum against the sentence and holder contacts before asking the
    existing guarded command handler for a bank-authored preview. No command here can confirm."""
    ctx = runtime.context
    account_id = UUID(state["account_id"])
    conversation_id = UUID(state["conversation_id"])
    current = (
        codec.loads_state(state["pending_state"])
        if state.get("pending_state")
        else await ctx.ports.store.load(conversation_id, account_id=account_id)
        or DialogueState(conversation_id)
    )
    base_result = (
        codec.loads_typed(state["pending_result"]) if state.get("pending_result") else None
    )
    if current is None or current.version != state["expected_version"]:
        raise StaleDialogueError(current or DialogueState(conversation_id))
    if base_result is not None and not isinstance(base_result, AskFinancialQuestionResult):
        raise RuntimeError("pending graph result is not an answer")
    if ctx.ports.commands is None:
        return await _finish_payment_without_command(
            state, ctx, payment_writer.cannot_pay(), current, base_result
        )
    if "composition" in state.get("refused", []):
        return await _finish_payment_without_command(
            state,
            ctx,
            "No puedo combinar ese Bizum con las otras operaciones de este turno.",
            current,
            base_result,
        )
    operations = codec.loads(state["ops"])
    own_transfer = next((item for item in operations if isinstance(item, PrepareOwnTransfer)), None)
    if own_transfer is not None:
        if isinstance(current.payment, PaymentDraftPending):
            return await _finish_own_transfer_refusal(
                state,
                ctx,
                current,
                base_result,
                "Termina o cancela el Bizum pendiente antes de iniciar otra operación de pago.",
            )
        return await _prepare_own_transfer(state, ctx, current, base_result, own_transfer)
    op = next((item for item in operations if isinstance(item, PrepareBizum)), None)
    if op is None:
        raise RuntimeError("payment node reached without a PrepareBizum operation")
    if current.own_transfer is not None:
        return await _finish_payment_without_command(
            state,
            ctx,
            "Termina o cancela la transferencia pendiente antes de iniciar otro pago.",
            current,
            base_result,
        )
    contacts = await ctx.ports.contacts.all_of(account_id)
    previous_payment = current.payment if isinstance(current.payment, PaymentDraftPending) else None
    resolved = payment_input.resolve_bizum_draft(
        ctx.sentence,
        op,
        contacts,
        previous=previous_payment.draft if previous_payment is not None else None,
        candidate_contact_ids=(
            previous_payment.candidate_contact_ids if previous_payment is not None else ()
        ),
    )
    if resolved.rejected:
        return await _finish_payment_without_command(
            state,
            ctx,
            "No he podido verificar el importe y el destinatario en tu mensaje y tus contactos. "
            "Indica un Bizum con un importe y un contacto reconocible.",
            current,
            base_result,
        )
    if resolved.candidate_contact_ids or resolved.draft.missing:
        if resolved.candidate_contact_ids:
            names = tuple(
                contact.display_name
                for contact in contacts
                if contact.contact_id in resolved.candidate_contact_ids
            )
            question = payment_writer.several_candidates(names)
        else:
            question = payment_writer.still_missing(resolved.draft)
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
            text=question,
        )
        result = _combine_payment_result(base_result, result)
        candidate = _remember_result(
            replace(
                current,
                payment=PaymentDraftPending(resolved.draft, resolved.candidate_contact_ids),
            ),
            ctx.sentence,
            result.text,
        )
        saved = await ctx.ports.store.save(
            candidate,
            account_id=account_id,
            expected_version=state["expected_version"],
        )
        ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = (
            saved,
            result,
            saved,
        )
        return {"status": "payment_complete", "expected_version": saved.version}
    recipient = next(
        (
            contact
            for contact in contacts
            if contact.contact_id == resolved.draft.recipient_contact_id
        ),
        None,
    )
    if recipient is None or resolved.draft.amount_minor is None:
        raise RuntimeError("complete Bizum draft lacks a grounded recipient or amount")
    command = PrepareChosenPaymentCommand(
        account_id=account_id,
        recipient_name=recipient.display_name,
        recipient_contact_id=recipient.contact_id,
        amount_minor=resolved.draft.amount_minor,
        currency=resolved.draft.currency,
        idempotency_key=f"conversation-bizum:{state['turn_id']}",
        rail=Rail.BIZUM,
    )
    answer = await ctx.ports.commands.dispatch(command)
    if not isinstance(answer, PaymentAnswer):
        raise TypeError("payment preparation returned an unexpected answer")
    pending = (
        PaymentConfirmationPending(answer.preview, ctx.thread_id)
        if answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
        and answer.preview is not None
        else None
    )
    payment_result = _payment_result(answer, replace(current, payment=pending), pending is not None)
    result = _combine_payment_result(base_result, payment_result)
    candidate = replace(current, payment=pending)
    if candidate.turns and candidate.turns[-1].said == ctx.sentence:
        last = candidate.turns[-1]
        candidate = replace(
            candidate, turns=(*candidate.turns[:-1], replace(last, answered=result.text))
        )
    else:
        candidate = candidate.remembered(DialogueTurn(ctx.sentence, result.text))
    saved = await ctx.ports.store.save(
        candidate,
        account_id=account_id,
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    ctx.cache["result"] = _combine_payment_result(
        base_result, _payment_result(answer, saved, pending is not None)
    )
    ctx.cache["saved"] = saved
    if pending is not None:
        return {
            "status": "payment_waiting",
            "payment_answer": codec.dumps_typed(answer),
            "expected_version": saved.version,
        }
    return {"status": "payment_complete", "expected_version": saved.version}


async def _prepare_own_transfer(
    state: TurnGraphState,
    ctx: GraphContext,
    current: DialogueState,
    previous: AskFinancialQuestionResult | None,
    operation: PrepareOwnTransfer,
) -> TurnGraphState:
    """Resolve the transfer only from the caller's words and bank account catalogue.

    The model selects the typed transfer operation. Account and amount are independently grounded;
    only the existing command can ask banking for a preview, and this node never confirms it.
    """
    grounds = await _grounds(state, ctx)
    accounts = grounds.accounts
    prior = current.own_transfer
    destination_candidates = payment_input.destination_candidates(ctx.sentence, accounts, prior)
    destination: Any | None = None
    if operation.destination_account is not None:
        grounded = ground(PlanFields(accounts=(operation.destination_account,)), grounds)
        if isinstance(grounded, PlanFields) and grounded.accounts:
            destination = next(
                (holding for holding in accounts if holding.display_name == grounded.accounts[0]),
                None,
            )
    if destination_candidates:
        if len(destination_candidates) == 1:
            lexical = destination_candidates[0]
            if destination is not None and destination.account_id != lexical.account_id:
                return await _finish_own_transfer_refusal(
                    state,
                    ctx,
                    current,
                    previous,
                    "No he podido verificar la cuenta de destino que nombraste.",
                )
            destination = lexical
        else:
            destination = None
    elif destination is not None and prior is not None:
        if prior.to_account_id != destination.account_id:
            return await _finish_own_transfer_refusal(
                state,
                ctx,
                current,
                previous,
                "No he podido verificar la cuenta de destino que nombraste.",
            )
    elif destination is not None:
        return await _finish_own_transfer_refusal(
            state,
            ctx,
            current,
            previous,
            "No he podido verificar la cuenta de destino que nombraste.",
        )

    if destination is None and prior is not None and prior.to_account_id is not None:
        destination = next(
            (holding for holding in accounts if holding.account_id == prior.to_account_id), None
        )

    amount_spoken = payment_input.amount(ctx.sentence)
    if payment_input.mentions_amount(ctx.sentence) and amount_spoken is None:
        return await _finish_own_transfer_refusal(
            state,
            ctx,
            current,
            previous,
            "El importe indicado queda fuera de los límites permitidos. Indica otro importe.",
            retain=True,
        )
    amount = amount_spoken if amount_spoken is not None else prior.amount_minor if prior else None

    candidate_ids = tuple(holding.account_id for holding in destination_candidates)
    if destination is None and prior is not None and prior.candidate_account_ids:
        candidate_ids = prior.candidate_account_ids
    if destination is None or amount is None:
        draft = OwnTransferDraftPending(
            to_account_id=destination.account_id if destination is not None else None,
            amount_minor=amount,
            candidate_account_ids=candidate_ids if destination is None else (),
            workflow_id=prior.workflow_id if prior is not None else uuid4().hex,
        )
        question = (
            answer_writer.which_account(
                [
                    holding
                    for holding in accounts
                    if not candidate_ids or holding.account_id in candidate_ids
                ]
            )
            if destination is None
            else answer_writer.how_much_to_move(destination)
        )
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
            text=question,
        )
        result = _combine_payment_result(previous, result)
        saved = await _save_own_transfer_state(current, draft, result, ctx, state)
        ctx.cache["result"], ctx.cache["saved"] = result, saved
        return {"status": "payment_complete", "expected_version": saved.version}

    workflow_id = prior.workflow_id if prior is not None else uuid4().hex
    command = PrepareOwnTransferCommand(
        account_id=UUID(state["account_id"]),
        to_account_id=destination.account_id,
        amount_minor=amount,
        currency=destination.currency,
        idempotency_key=f"conversation-own-transfer:{workflow_id}",
    )
    if ctx.ports.commands is None:
        return await _finish_own_transfer_refusal(
            state, ctx, current, previous, payment_writer.cannot_pay()
        )
    answer = await ctx.ports.commands.dispatch(command)
    if not isinstance(answer, PaymentAnswer):
        raise TypeError("own transfer preparation returned an unexpected answer")
    waiting = (
        answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value and answer.preview is not None
    )
    uncertain = answer.outcome == PaymentOutcome.BANKING_UNAVAILABLE.value
    pending = None
    if waiting:
        if answer.preview is None:
            raise RuntimeError("a transfer awaiting confirmation has no bank preview")
        pending = PaymentConfirmationPending(answer.preview, ctx.thread_id)
    retained = (
        OwnTransferDraftPending(destination.account_id, amount, workflow_id=workflow_id)
        if uncertain
        else None
    )
    payment = _payment_result(answer, replace(current, payment=pending), waiting)
    result = _combine_payment_result(previous, payment)
    candidate = _remember_result(
        replace(current, own_transfer=retained, payment=pending), ctx.sentence, result.text
    )
    saved = await ctx.ports.store.save(
        candidate,
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    ctx.cache["result"] = _combine_payment_result(previous, _payment_result(answer, saved, waiting))
    ctx.cache["saved"] = saved
    if waiting:
        return {
            "status": "payment_waiting",
            "payment_answer": codec.dumps_typed(answer),
            "expected_version": saved.version,
        }
    return {"status": "payment_complete", "expected_version": saved.version}


async def _finish_own_transfer_refusal(
    graph_state: TurnGraphState,
    ctx: GraphContext,
    current: DialogueState,
    previous: AskFinancialQuestionResult | None,
    text: str,
    *,
    retain: bool = False,
) -> TurnGraphState:
    prior = current.own_transfer
    result = _combine_payment_result(
        previous,
        AskFinancialQuestionResult(outcome=PlanningOutcome.INVALID_DOMAIN.value, text=text),
    )
    candidate = _remember_result(
        replace(current, own_transfer=prior if retain else None), ctx.sentence, result.text
    )
    saved = await ctx.ports.store.save(
        candidate,
        account_id=UUID(graph_state["account_id"]),
        expected_version=graph_state["expected_version"],
    )
    ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = saved, result, saved
    return {"status": "payment_complete", "expected_version": saved.version}


async def _save_own_transfer_state(
    current: DialogueState,
    draft: OwnTransferDraftPending,
    result: AskFinancialQuestionResult,
    ctx: GraphContext,
    state: TurnGraphState,
) -> DialogueState:
    candidate = _remember_result(replace(current, own_transfer=draft), ctx.sentence, result.text)
    saved = await ctx.ports.store.save(
        candidate,
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    return saved


def _remember_result(state: DialogueState, sentence: str, answer: str) -> DialogueState:
    if state.turns and state.turns[-1].said == sentence:
        last = state.turns[-1]
        return replace(state, turns=(*state.turns[:-1], replace(last, answered=answer)))
    return state.remembered(DialogueTurn(sentence, answer))


async def _finish_payment_without_command(
    state: TurnGraphState,
    ctx: GraphContext,
    text: str,
    current: DialogueState | None = None,
    base_result: AskFinancialQuestionResult | None = None,
) -> TurnGraphState:
    account_id = UUID(state["account_id"])
    current = current or await ctx.ports.store.load(
        UUID(state["conversation_id"]), account_id=account_id
    )
    if current is None or current.version != state["expected_version"]:
        raise StaleDialogueError(current or DialogueState(UUID(state["conversation_id"])))
    payment_result = AskFinancialQuestionResult(
        outcome=PlanningOutcome.INVALID_DOMAIN.value,
        text=text,
    )
    result = _combine_payment_result(base_result, payment_result)
    candidate = current
    if candidate.turns and candidate.turns[-1].said == ctx.sentence:
        last = candidate.turns[-1]
        candidate = replace(
            candidate, turns=(*candidate.turns[:-1], replace(last, answered=result.text))
        )
    else:
        candidate = candidate.remembered(DialogueTurn(ctx.sentence, result.text))
    saved = await ctx.ports.store.save(
        candidate,
        account_id=account_id,
        expected_version=state["expected_version"],
    )
    ctx.cache["result"], ctx.cache["saved"] = result, saved
    return {"status": "payment_complete", "expected_version": saved.version}


async def wait_for_payment_reply(state: TurnGraphState) -> TurnGraphState:
    answer = codec.loads_typed(state["payment_answer"])
    if not isinstance(answer, PaymentAnswer) or answer.preview is None:
        raise RuntimeError("payment confirmation checkpoint has no bank preview")
    preview = answer.preview
    reply = interrupt(
        {
            "intent_id": str(preview.intent_id),
            "recipient": preview.recipient,
            "amount_minor": preview.amount_minor,
            "currency": preview.currency,
            "balance_after_minor": preview.balance_after_minor,
            "expires_at": preview.expires_at.isoformat(),
        }
    )
    if not isinstance(reply, str):
        raise ValueError("payment confirmation reply must be text")
    return {"payment_reply": reply}


async def confirm_payment(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    ctx = runtime.context
    if Scope.PAYMENTS_CONFIRM.value not in ctx.caller_scopes:
        raise NotAuthorisedError(f"this credential is not allowed {Scope.PAYMENTS_CONFIRM.value}")
    if ctx.ports.commands is None:
        raise RuntimeError("payment command bus is unavailable")
    account_id = UUID(state["account_id"])
    conversation_id = UUID(state["conversation_id"])
    current = await ctx.ports.store.load(conversation_id, account_id=account_id)
    pending = current.payment if current is not None else None
    if (
        current is None
        or not isinstance(pending, PaymentConfirmationPending)
        or pending.workflow_id != ctx.thread_id
    ):
        raise StaleDialogueError(current or DialogueState(conversation_id))
    command = ConfirmPaymentCommand(
        reply=state["payment_reply"],
        account_id=account_id,
        intent_id=pending.preview.intent_id,
        preview=pending.preview,
    )
    answer = await ctx.ports.commands.dispatch(command)
    if not isinstance(answer, PaymentAnswer):
        raise TypeError("payment confirmation returned an unexpected answer")
    answer = replace(answer, preview=pending.preview)
    remains_pending = answer.outcome in {
        PaymentOutcome.AWAITING_CONFIRMATION.value,
        PaymentOutcome.BANKING_UNAVAILABLE.value,
    }
    saved = await ctx.ports.store.save(
        replace(current, payment=pending if remains_pending else None).remembered(
            DialogueTurn(ctx.sentence, answer.text)
        ),
        account_id=account_id,
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    ctx.cache["result"] = _payment_result(answer, saved, remains_pending)
    ctx.cache["saved"] = saved
    return {
        "status": "payment_waiting" if remains_pending else "payment_complete",
        "payment_answer": codec.dumps_typed(answer),
        "expected_version": saved.version,
    }


def _payment_result(
    answer: PaymentAnswer, state: DialogueState, waiting: bool
) -> AskFinancialQuestionResult:
    pending = state.payment
    preview = (
        answer.preview
        if answer.preview is not None
        else pending.preview
        if isinstance(pending, PaymentConfirmationPending)
        else None
    )
    conversation = Conversation(
        conversation_id=state.conversation_id,
        version=state.version,
        active=(
            PaymentConfirmation(
                intent_id=preview.intent_id,
                recipient=preview.recipient,
                amount_minor=preview.amount_minor,
                currency=preview.currency,
                rail=preview.rail,
                recipient_iban=preview.recipient_iban,
            )
            if waiting and preview is not None
            else None
        ),
    )
    return AskFinancialQuestionResult(
        outcome=answer.outcome,
        text=answer.text,
        conversation=conversation,
    )


def _combine_payment_result(
    previous: AskFinancialQuestionResult | None, payment: AskFinancialQuestionResult
) -> AskFinancialQuestionResult:
    if previous is None:
        return payment
    return replace(
        previous,
        text=f"{previous.text}\n\n{payment.text}",
        conversation=payment.conversation,
    )


async def visualize_and_commit(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """Render the active aggregate through the native graph path; no banking or legacy dispatch."""
    ctx = runtime.context
    current = await _canonical(state, ctx)
    artifact = current.active_result
    operations = codec.loads(state["ops"])
    visual_ops = tuple(op for op in operations if isinstance(op, Visualize))
    visual_op = visual_ops[0] if len(visual_ops) == 1 and len(operations) == 1 else None
    if visual_ops and visual_op is None:
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text="La visualización debe solicitarse como una operación independiente.",
        )
        saved = await ctx.ports.store.save(
            current.remembered(DialogueTurn(ctx.sentence, result.text)),
            account_id=UUID(state["account_id"]),
            expected_version=state["expected_version"],
        )
        ctx.cache["result"], ctx.cache["saved"] = result, saved
        return {"status": "committed"}
    if visual_op is None:
        visual_op = Visualize(
            "ACTIVE_CHART" if current.active_visual is not None else "ACTIVE_RESULT", "AUTO"
        )
    if visual_op.target == "ACTIVE_CHART" and current.active_visual is None:
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text="Todavía no hay un gráfico activo que pueda cambiar. Primero crea uno.",
        )
    elif artifact is None:
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text="Primero necesito una consulta con resultados para poder crear el gráfico.",
        )
    else:
        plan = artifact.plan
        groups = artifact.groups
        rows = tuple(
            VisualRow(
                (
                    category_words.name_of(group.key)
                    if plan.group_by is GroupBy.CATEGORY
                    else group.key
                ),
                group.movement_count
                if plan.metric in {Metric.COUNT, Metric.FREQUENCY}
                else group.total_minor,
            )
            for group in groups
        )
        if not rows:
            rows = (VisualRow("total", artifact.total_minor),)
        window = (
            plan.date_range
            if plan.date_range is not None
            else AbsoluteRange(artifact.period_start, artifact.period_end)
            if artifact.period_start is not None and artifact.period_end is not None
            else None
        )
        dataset = VisualDataset(
            rows=rows,
            metric=plan.metric.value,
            title=answer_writer.describe(plan),
            window=window,
        )
        profile = visual_profile_of(
            dataset,
            total_minor=artifact.total_minor,
            group_by=plan.group_by.value,
            plan_limited=plan.group_limit is not None,
            plan_metric=plan.metric.value,
        )
        if ctx.ports.visual_policy is None:
            raise RuntimeError("the graph visual policy is not configured")
        before = ctx.ports.count_calls()
        decision = await ctx.ports.visual_policy.decide(ctx.sentence, profile, DEFAULT_PRESENTATION)
        ctx.cache["visual_model_calls"] = max(0, ctx.ports.count_calls() - before)
        if decision is not None and visual_op.style != "AUTO":
            mark: Mark = "line" if visual_op.style == "LINE" else "bar"
            if profile.metric == "RATIO_SHARE" and mark == "line":
                decision = None
            else:
                styled = replace(decision.spec, mark=mark, stack=None)
                if styled.valid_for(profile.field_names()):
                    decision = replace(
                        decision,
                        spec=styled,
                        source="patch",
                        fallback_reason=None,
                        accepted=True,
                        proposed=True,
                        valid=True,
                        proposed_spec=styled,
                    )
                else:
                    decision = None
        spoken_plan = SpokenPlan(
            direction=plan.direction.value,
            period=plan.period.period.value if plan.period is not None else None,
            n=plan.period.n if plan.period is not None else None,
            start=window.start.isoformat() if window is not None else None,
            end=window.end.isoformat() if window is not None else None,
            categories=list(plan.categories),
            merchants=list(plan.merchants),
            merchant_terms=list(plan.merchant_terms),
            group_by=plan.group_by.value,
            metric=plan.metric.value,
            group_limit=plan.group_limit,
        )
        if decision is None:
            result = AskFinancialQuestionResult(
                outcome=PlanningOutcome.PLANNED.value,
                text="La consulta no tiene suficientes grupos para dibujar un gráfico útil.",
                plan_in_words=answer_writer.plan_in_words(plan),
                plan_context=spoken_plan,
                currency=artifact.currency,
                total_minor=artifact.total_minor,
                movement_count=artifact.movement_count,
                explicit_visualization=True,
                visual_dataset=dataset,
                metric_definition=plan,
            )
        else:
            result = AskFinancialQuestionResult(
                outcome=PlanningOutcome.PLANNED.value,
                text=f"Aquí tienes el desglose de {answer_writer.plan_in_words(plan)}.",
                plan_in_words=answer_writer.plan_in_words(plan),
                plan_context=spoken_plan,
                currency=artifact.currency,
                total_minor=artifact.total_minor,
                movement_count=artifact.movement_count,
                explicit_visualization=True,
                visual_dataset=dataset,
                visual_decision=decision,
                metric_definition=plan,
            )
    canonical = current
    if (
        result.visual_decision is not None
        and result.visual_dataset is not None
        and artifact is not None
    ):
        previous = current.active_visual
        visual_version = (
            previous.version + 1
            if previous is not None and previous.result_version == artifact.result_version
            else 1
        )
        canonical = replace(
            current,
            active_visual=ActiveVisual(
                artifact.result_version,
                visual_version,
                result.visual_decision.spec,
                result.visual_decision.profile,
            ),
        )
    else:
        canonical = replace(
            current,
            active_visual=(current.active_visual if visual_op.target == "ACTIVE_CHART" else None),
        )
    saved = await ctx.ports.store.save(
        canonical.remembered(DialogueTurn(ctx.sentence, result.plan_in_words or result.text)),
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["result"], ctx.cache["saved"] = result, saved
    return {
        "status": "committed",
        "model_calls": state.get("model_calls", 0) + ctx.cache.get("visual_model_calls", 0),
    }


# --- routing --------------------------------------------------------------------------------------


def _after_validation(state: TurnGraphState) -> str:
    match state["status"]:
        case "delegated":
            return END
        case "payment":
            return "prepare_payment"
        case "visualize":
            return "visualize_and_commit"
        case "replied" | "refused" | "canonical":
            return "present_and_commit"
        case _:
            return "execute_read_only"


def _after_presentation(state: TurnGraphState) -> str:
    return (
        "prepare_payment"
        if any(
            isinstance(op, (PrepareBizum, PrepareOwnTransfer)) for op in codec.loads(state["ops"])
        )
        else END
    )


def _after_payment_prepare(state: TurnGraphState) -> str:
    return "wait_for_payment_reply" if state["status"] == "payment_waiting" else END


def _after_payment_confirmation(state: TurnGraphState) -> str:
    return "wait_for_payment_reply" if state["status"] == "payment_waiting" else END


def _after_execution(state: TurnGraphState) -> str:
    """The latest window is the next step's read until it is read."""
    return "execute_read_only" if state["status"] == "investigate" else "assess_result"


def _after_assessment(state: TurnGraphState) -> str:
    return "plan" if state.get("next_inspect") is not None else "present_and_commit"


def build(checkpointer: Checkpointer) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(TurnGraphState, context_schema=GraphContext)
    graph.add_node("load_and_reserve", load_and_reserve)
    graph.add_node("plan", plan)
    graph.add_node("validate_and_apply", validate_and_apply)
    graph.add_node("execute_read_only", execute_read_only)
    graph.add_node("assess_result", assess_result)
    graph.add_node("present_and_commit", present_and_commit)
    graph.add_node("visualize_and_commit", visualize_and_commit)
    graph.add_node("prepare_payment", prepare_payment)
    graph.add_node("wait_for_payment_reply", wait_for_payment_reply)
    graph.add_node("confirm_payment", confirm_payment)
    graph.add_edge(START, "load_and_reserve")
    graph.add_edge("load_and_reserve", "plan")
    graph.add_edge("plan", "validate_and_apply")
    graph.add_conditional_edges(
        "validate_and_apply",
        _after_validation,
        [
            "execute_read_only",
            "present_and_commit",
            "visualize_and_commit",
            "prepare_payment",
            END,
        ],
    )
    graph.add_conditional_edges(
        "execute_read_only", _after_execution, ["execute_read_only", "assess_result"]
    )
    graph.add_conditional_edges("assess_result", _after_assessment, ["plan", "present_and_commit"])
    graph.add_conditional_edges("present_and_commit", _after_presentation)
    graph.add_conditional_edges("prepare_payment", _after_payment_prepare)
    graph.add_edge("wait_for_payment_reply", "confirm_payment")
    graph.add_conditional_edges("confirm_payment", _after_payment_confirmation)
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
        ops = tuple(
            _without_merchant_grouping(op)
            for op in codec.loads(state["ops"])
            if not isinstance(op, (PrepareBizum, PrepareOwnTransfer))
        )
        current = await _canonical(state, ctx)
        ctx.cache["transition"] = apply_guarded(current, ops, ctx.today, await _grounds(state, ctx))
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


async def _read_by_merchant(
    work: Work, side: FinancialPlan, state: TurnGraphState, ctx: GraphContext
) -> QueryAnswer:
    run = await _execute(_by_merchant_work(work, side), state, ctx)
    assert isinstance(run, QueryRun)
    return run.answer


def _bank_calls(state: TurnGraphState, ctx: GraphContext) -> int:
    """`bank_calls` with this step's calls added, which are then no longer unsaved."""
    calls, ctx.unsaved_calls = state.get("bank_calls", 0) + ctx.unsaved_calls, 0
    return calls


class _CountedQueries:
    """The query port, counting each call before it is made, so a failed one counts too."""

    def __init__(self, inner: FinancialQueryPort, ctx: GraphContext) -> None:
        self._inner, self._ctx = inner, ctx

    async def run(self, plan: FinancialPlan, **kwargs: Any) -> QueryAnswer:
        self._ctx.unsaved_calls += 1
        return await self._inner.run(plan, **kwargs)

    async def ranked(self, ranking: TopMovements, **kwargs: Any) -> Movement | None:
        self._ctx.unsaved_calls += 1
        return await self._inner.ranked(ranking, **kwargs)


class _CountedSummary:
    def __init__(self, inner: AccountSummaryPort, ctx: GraphContext) -> None:
        self._inner, self._ctx = inner, ctx

    async def of(self, account_id: UUID, **kwargs: Any) -> AccountSummary:
        self._ctx.unsaved_calls += 1
        return await self._inner.of(account_id, **kwargs)


async def _execute(work: Work, state: TurnGraphState, ctx: GraphContext) -> Executed:
    return await execute(
        work,
        queries=_CountedQueries(ctx.ports.queries, ctx),
        summary=_CountedSummary(ctx.ports.summary, ctx),
        account_id=UUID(state["account_id"]),
        today=ctx.today,
        reporting_timezone=ctx.reporting_timezone,
    )


async def _inspect(state: TurnGraphState, work: Work, ctx: GraphContext) -> TurnGraphState:
    """The largest purchase at the merchant asked about, in the latest window and the same scope
    and filters the validated comparison carries: a ranking banking answers."""
    position = state["next_inspect"]
    assert position is not None and isinstance(work.plan, ComparisonPlan)
    merchant = _may_inspect(state)[position]
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
        "bank_calls": _bank_calls(state, ctx),
        "inspected": [*state.get("inspected", []), found],
        "next_inspect": None,
    }


# --- the investigation's state, read back ---------------------------------------------------------


def _changes(state: TurnGraphState) -> tuple[MerchantChange, ...]:
    return tuple(
        MerchantChange(name, e_total, e_count, l_total, l_count, Explanation(cause))
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
            MerchantSummary(c.merchant, c.explanation, c.earlier_count, c.latest_count)
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
    text = investigation.answer_text(
        _changes(state),
        _inspections(state),
        earlier=earlier,
        latest=latest,
        earlier_total=then,
        latest_total=now,
        currency=currency,
        partial=state.get("partial", False),
    )
    text = "\n".join((text, presenter.scope_note(work.account_ids, names)))
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.PLANNED.value,
        text=text,
        plan_in_words=(
            f"{answer_writer.plan_in_words(earlier)} frente a "
            f"{answer_writer.plan_in_words(latest)}, por comercio"
        ),
        currency=currency,
    )
