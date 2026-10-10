"""The unified conversational turn as a bounded LangGraph StateGraph.

    load_and_reserve → plan → validate_and_apply → execute_read_only → assess_result
                                    │                                    │
                                    ├→ present_and_commit ←──────────────┤ (stop)
                                    └→ END (delegated)        plan ←─────┘ (inspect)

LangGraph owns sequencing, bounded analysis, visualization and payment confirmation routing. Nodes
adapt the existing typed interpreter, transition validation, banking ports, guarded payment
commands, presenter and canonical store. The turn reservation and version check remain outside the
graph (`graph_turn`); the persistence nodes still perform compare-and-set.

**What the checkpoint holds** (the graph state, written after every node): the graph and schema
versions, the turn's request id, account and conversation, the version it was made against, the
validated operations, execution counters, derived comparison aggregates, and a closed banking
result needed to resume without repeating a successful read. That result can include the bounded
movement rows returned for this answer; it never includes the user's sentence, prompt, credentials,
or an unbounded ledger. Turn checkpoints are deleted after the exact response is committed to the
idempotency reservation. Payment workflow ids are canonical state, independent from request-specific
checkpoint thread ids; each confirmation or cancellation starts in its own thread. The sentence,
the ports and the per-invocation cache travel in runtime context, which LangGraph never checkpoints.

**Budgets**, deterministic: at most `MAX_BANK_EXECUTIONS` banking port calls and
`MAX_MODEL_CALLS` model generation units per turn. A hosted retry or fallback request consumes
one unit; a local provider call consumes one logical unit. Every port call counts, including
failures. A payment
confirmation is never inferred: the graph interrupts with the exact bank preview and the resume
node rechecks account, scope, workflow and pending state before invoking the existing guarded
command. Analysis can loop only over the bounded merchants banking returned; unsupported next
steps are refused and any partial answer is marked.
"""

import dataclasses
import logging
from dataclasses import replace
from typing import Any, Final
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from finguard_orchestrator.application.engine import presenter
from finguard_orchestrator.application.engine.executor import (
    MixedCurrenciesError,
    MovementListRun,
    PairRun,
    QueryRun,
    RankingRun,
)
from finguard_orchestrator.application.engine.transition_guard import apply_guarded
from finguard_orchestrator.application.graph import (
    codec,
    graph_analysis,
    graph_artifacts,
    graph_execution,
    investigation,
)
from finguard_orchestrator.application.graph.budgets import (
    MAX_BANK_EXECUTIONS,
    MAX_MODEL_CALLS,
    ReadBudgetExhaustedError,
)
from finguard_orchestrator.application.graph.graph_grounding import (
    grounds_for_state as _grounds,
)
from finguard_orchestrator.application.graph.graph_payments import (
    _after_payment_reply,
    _remember_result,
    confirm_payment,
    prepare_payment,
    wait_for_payment_reply,
)
from finguard_orchestrator.application.graph.graph_state import (
    Ctx,
    GraphContext,
    TurnGraphState,
)
from finguard_orchestrator.application.graph.graph_store import (
    canonical_state as _canonical,
)
from finguard_orchestrator.application.graph.graph_store import (
    commit_state,
)
from finguard_orchestrator.application.graph.graph_visualization import visualize_and_commit
from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionResult,
    SpokenContact,
)
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    AskCorrection,
    Balance,
    ChangeMeasure,
    Clarify,
    Compare,
    Delegate,
    InvestigateComparison,
    ListCategories,
    ListContacts,
    ListMovements,
    Operation,
    Patch,
    PatchMovementList,
    PlanFields,
    PrepareBizum,
    PrepareOwnTransfer,
    PrepareTransfer,
    Reset,
    RestorePrevious,
    ResumeFocus,
    SelectOperand,
    Visualize,
)
from finguard_orchestrator.domain.engine.state import (
    ArtifactSource,
    ArtifactStatus,
    DialogueTurn,
    FinancialArtifact,
    Missing,
    MovementListFocus,
    PaymentConfirmationPending,
    PaymentDraftPending,
    Pending,
)
from finguard_orchestrator.domain.engine.state import (
    Scope as AccountScope,
)
from finguard_orchestrator.domain.engine.transitions import Transition, Work
from finguard_orchestrator.domain.enums import (
    GroupBy,
    PaymentOutcome,
    PlanningOutcome,
    ResultKind,
    Spoken,
)
from finguard_orchestrator.domain.errors import NotAuthorisedError
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.ports.investigation_assessor import (
    AssessmentFailedError,
    Stop,
)
from finguard_orchestrator.domain.services import (
    answer_writer,
    confirmation,
    explainer,
    payment_router,
    payment_writer,
)
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

logger = logging.getLogger(__name__)

# Bumped whenever nodes, routing, or turn-resume semantics change: a checkpoint written by another
# graph is not resumed (`graph_turn`), because its next step or per-turn state may mean something
# else now.
GRAPH_VERSION: Final = "conversation/39"
# Bumped whenever a key of `TurnGraphState` changes meaning or shape.
STATE_SCHEMA_VERSION: Final = 22
# A backstop under the budgets: one step per read, and plan, validate and assess per loop.
RECURSION_LIMIT: Final = 4 * (MAX_BANK_EXECUTIONS + 1) + 4


# --- nodes ----------------------------------------------------------------------------------------


async def load_and_reserve(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """The canonical state at the version the turn was reserved against, or stale."""
    await _canonical(state, runtime.context)
    return {"status": "payment_waiting" if state.get("direct_payment_confirmation") else "loaded"}


async def plan(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """The interpreter's one structured call, on the first pass only. On a loop the operation is
    the one the assessment asked for, already in the state, and no model is called."""
    if state.get("next_inspect") is not None:
        return {}
    ctx = runtime.context
    current = await _canonical(state, ctx)
    if (
        isinstance(current.payment, PaymentDraftPending)
        and not current.payment.suspended
        and confirmation.is_refusal(ctx.sentence)
    ):
        # An unresolved recipient/amount choice is a real user-visible payment draft too. Close
        # that draft on an unambiguous refusal before asking the model to interpret the same «no»
        # as another recipient. This clears draft state only; it never reaches the bank.
        logger.info("graph.payment_draft.cancelled")
        return {"ops": codec.dumps(()), "status": "cancel_payment_draft"}
    grounds = await _grounds(state, ctx)
    payment = current.payment
    if (
        isinstance(payment, PaymentDraftPending)
        and payment.suspended
        and not payment_router.resumes(ctx.sentence)
        and not payment_router.is_payment(ctx.sentence)
        and payment_router.continues(ctx.sentence, payment.draft, grounds.contacts)
    ):
        # The user's bare slot value is ambiguous after another subject took the floor. Resolve
        # this deterministic guard before asking the model, so a planner cannot revive the payment.
        logger.info("graph.payment.paused_reply")
        return {
            "ops": codec.dumps(()),
            "status": "paused_payment",
        }
    if state.get("model_calls", 0) >= MAX_MODEL_CALLS:
        logger.warning("graph.model_budget_exhausted")
        return {
            "ops": codec.dumps(()),
            "status": "refused",
            "refused": [*state.get("refused", []), "model_budget"],
        }
    names = {holding.account_id: holding.display_name for holding in grounds.accounts}
    before = ctx.ports.count_calls()
    ops = await ctx.ports.interpreter.interpret(
        ctx.sentence, current, today=ctx.today, account_names=names
    )
    logger.info(
        "graph.plan.completed operation_count=%d operation_types=%s has_focus=%s "
        "open_question=%s focus_history_count=%d",
        len(ops),
        ",".join(type(operation).__name__ for operation in ops),
        current.focus is not None,
        current.pending.missing.value if current.pending is not None else "none",
        len(current.focus_history),
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
    if state.get("status") in {"paused_payment", "cancel_payment_draft"}:
        return {"status": state["status"]}
    if "model_budget" in state.get("refused", []):
        return {"status": "refused"}
    asked = state.get("next_inspect")
    if asked is not None:
        allowed = 0 <= asked < len(graph_analysis.may_inspect(state))
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
    if any(isinstance(op, AskCorrection) for op in ops):
        if len(ops) != 1:
            return {"status": "refused", "refused": [*state.get("refused", []), "composition"]}
        return {"status": "replied"}
    if any(isinstance(op, (ListContacts, ListCategories)) for op in ops):
        if len(ops) != 1:
            return {"status": "refused", "refused": [*state.get("refused", []), "composition"]}
        if state.get("bank_calls", 0) >= MAX_BANK_EXECUTIONS:
            return {"status": "refused", "refused": [*state.get("refused", []), "bank_budget"]}
        return {"status": "replied"}
    contact_payments = (PrepareBizum, PrepareTransfer)
    payments = tuple(op for op in ops if isinstance(op, (*contact_payments, PrepareOwnTransfer)))
    if payments:
        if Scope.PAYMENTS_PREPARE.value not in runtime.context.caller_scopes:
            raise NotAuthorisedError(
                f"this credential is not allowed {Scope.PAYMENTS_PREPARE.value}"
            )
        # The interpreter can redundantly emit Clarify beside a payment when a slot is missing.
        # The payment command already owns the safe, canonical slot question; retain that typed
        # operation and let it ask for the recipient or amount. Never let this turn execute a
        # second unrelated financial operation alongside a payment.
        if len(payments) == 1 and all(
            isinstance(op, (*contact_payments, PrepareOwnTransfer, Clarify)) for op in ops
        ):
            payment_ops = tuple(
                op for op in ops if isinstance(op, (*contact_payments, PrepareOwnTransfer))
            )
            if len(payment_ops) == 1:
                return {
                    "status": "payment",
                    "ops": codec.dumps(payment_ops),
                }
        analytics_ops = (
            Ask,
            Patch,
            Compare,
            InvestigateComparison,
            ChangeMeasure,
            SelectOperand,
            Reset,
            Balance,
        )
        if len(payments) != 1 or any(
            not isinstance(op, (*analytics_ops, *contact_payments, PrepareOwnTransfer))
            for op in ops
        ):
            return {"status": "payment", "refused": [*state.get("refused", []), "composition"]}
        if all(isinstance(op, (*contact_payments, PrepareOwnTransfer)) for op in ops):
            return {"status": "payment"}
    visuals = tuple(op for op in ops if isinstance(op, Visualize))
    if visuals:
        if len(visuals) != 1:
            return {"status": "refused", "refused": [*state.get("refused", []), "composition"]}
        if visuals[0].target == "ACTIVE_CHART":
            current = await _canonical(state, runtime.context)
            if current.active_visual is None:
                return {
                    # Let the visual node produce its typed no-chart response. In a mixed query
                    # turn, dropping the query here also guarantees no unintended banking read.
                    "status": "visualize",
                    "ops": codec.dumps((visuals[0],)),
                }
        if len(ops) == 1:
            return {"status": "visualize"}
        query_ops = tuple(op for op in ops if not isinstance(op, Visualize))
        query_types = (
            Ask,
            Patch,
            Compare,
            InvestigateComparison,
            ChangeMeasure,
            SelectOperand,
            RestorePrevious,
            ResumeFocus,
            ListMovements,
            PatchMovementList,
        )
        if len(query_ops) != 1 or not isinstance(query_ops[0], query_types):
            return {"status": "refused", "refused": [*state.get("refused", []), "composition"]}
        moved = await _transition({**state, "ops": codec.dumps(query_ops)}, runtime.context)
        if moved.work is None:
            return {"status": "replied", "ops": codec.dumps(query_ops)}
        investigating = moved.work.kind == "COMPARE" and _by_merchant(query_ops)
        return {
            "status": "investigate"
            if investigating
            else "compare"
            if moved.work.kind == "COMPARE"
            else "canonical",
            "ops": codec.dumps(query_ops),
            "visual_after_query": True,
            "visual_style": visuals[0].style,
        }
    moved = await _transition(state, runtime.context)
    if isinstance(moved.reply, Delegate):
        return {"status": "visualize" if moved.reply.to == "CHART" else "delegated"}
    if moved.work is None:
        return {"status": "replied"}
    investigating = moved.work.kind == "COMPARE" and _by_merchant(codec.loads(state["ops"]))
    return {
        "status": "investigate"
        if investigating
        else "compare"
        if moved.work.kind == "COMPARE"
        else "canonical"
    }


async def execute_read_only(state: TurnGraphState, runtime: Ctx) -> TurnGraphState:
    """One banking read per step, through the executor, and only reads: no operation that reaches
    here can move money, because none of the operations it runs can say so. What was read goes into
    the checkpoint, so a resume after a failed read starts at that read."""
    ctx = runtime.context
    if state["status"] == "canonical":
        # Ordinary reads used to run inside `present_and_commit`, coupling bank execution,
        # response composition and persistence in one node. Execute and checkpoint them here so
        # the presenter can only consume a bank-authored result. `_transition` is deterministic and
        # the same canonical transition is used again at commit to update conversational focus.
        work = (await _transition(state, ctx)).work
        if work is None:
            return {"status": "refused", "refused": [*state.get("refused", []), "missing_work"]}
        if work.kind != "NO_MORE_MOVEMENTS" and state.get("bank_calls", 0) >= MAX_BANK_EXECUTIONS:
            raise ReadBudgetExhaustedError(f"{state.get('bank_calls', 0)} reads made")
        executed = await graph_execution.execute_work(work, state, ctx)
        return {
            "status": "executed",
            "execution_result": codec.dumps_typed(executed),
            "bank_calls": graph_execution.record_bank_calls(state, ctx),
        }
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
    if not _by_merchant(codec.loads(state["ops"])):
        if state["status"] != "compare_latest":
            earlier = await graph_execution.execute_work(
                Work("QUERY", work.plan.earlier, work.account_ids), state, ctx
            )
            assert isinstance(earlier, QueryRun)
            return {
                "status": "compare_latest",
                "earlier_groups": [
                    [group.key, group.total_minor, group.movement_count]
                    for group in earlier.answer.groups
                ],
                "earlier_total": abs(earlier.answer.total_minor),
                "earlier_currency": earlier.answer.currency,
                "earlier_movement_count": earlier.answer.movement_count,
                "bank_calls": graph_execution.record_bank_calls(state, ctx),
            }
        latest = await graph_execution.execute_work(
            Work("QUERY", work.plan.latest, work.account_ids), state, ctx
        )
        assert isinstance(latest, QueryRun)
        earlier_answer = QueryAnswer(
            state["earlier_currency"],
            state["earlier_total"],
            state["earlier_movement_count"],
            groups=tuple(Group(*group) for group in state.get("earlier_groups", [])),
        )
        if (
            earlier_answer.currency != latest.answer.currency
            and not earlier_answer.is_empty
            and not latest.answer.is_empty
        ):
            raise MixedCurrenciesError(f"{earlier_answer.currency} and {latest.answer.currency}")
        return {
            "status": "executed",
            "bank_calls": graph_execution.record_bank_calls(state, ctx),
            "earlier_groups": [],
            "comparison_groups": [
                state.get("earlier_groups", []),
                [
                    [group.key, group.total_minor, group.movement_count]
                    for group in latest.answer.groups
                ],
            ],
            "comparison_counts": [state["earlier_movement_count"], latest.answer.movement_count],
            "totals": [state["earlier_total"], abs(latest.answer.total_minor)],
            "currency": latest.answer.currency,
        }
    if "earlier_groups" not in state:
        merchant_earlier = await graph_execution.read_by_merchant(
            work, work.plan.earlier, state, ctx
        )
        return {
            "earlier_groups": [
                [g.key, g.total_minor, g.movement_count] for g in merchant_earlier.groups
            ],
            "earlier_total": abs(merchant_earlier.total_minor),
            "earlier_currency": merchant_earlier.currency,
            "bank_calls": graph_execution.record_bank_calls(state, ctx),
        }
    now = await graph_execution.read_by_merchant(work, work.plan.latest, state, ctx)
    then = QueryAnswer(
        state["earlier_currency"],
        state["earlier_total"],
        sum(group[2] for group in state["earlier_groups"]),
        groups=tuple(Group(*group) for group in state["earlier_groups"]),
    )
    if then.currency != now.currency and not then.is_empty and not now.is_empty:
        raise MixedCurrenciesError(f"{then.currency} and {now.currency}")
    changes = investigation.changed(then, now)
    return {
        "status": "executed",
        "bank_calls": graph_execution.record_bank_calls(state, ctx),
        "earlier_groups": [],
        "comparison_groups": [
            state["earlier_groups"],
            [[g.key, g.total_minor, g.movement_count] for g in now.groups],
        ],
        "changes": [
            [
                c.merchant,
                c.earlier_total,
                c.earlier_count,
                c.latest_total,
                c.latest_count,
                c.explanation.value,
                c.decreased,
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
    if "changes" not in state:
        return {}
    candidates = graph_analysis.may_inspect(state)
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
        decided = await ctx.ports.assessor.assess(graph_analysis.summary(state, candidates))
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
    if "model_budget" in state.get("refused", []):
        current = await _canonical(state, ctx)
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.BANKING_UNAVAILABLE.value,
            text=(
                "No he podido interpretar esta pregunta dentro del límite de intentos. "
                "Puedes volver a formularla."
            ),
        )
        saved = await commit_state(
            ctx,
            current.remembered(DialogueTurn(ctx.sentence, result.text)),
            result,
            account_id=UUID(state["account_id"]),
            expected_version=state["expected_version"],
        )
        ctx.cache["result"], ctx.cache["saved"] = result, saved
        return {"status": "committed", "expected_version": saved.version}
    if state.get("status") == "cancel_payment_draft":
        current = await _canonical(state, ctx)
        if not isinstance(current.payment, PaymentDraftPending):
            raise StaleDialogueError(current)
        result = AskFinancialQuestionResult(
            outcome=PaymentOutcome.CANCELLED.value,
            text="Vale, he cancelado el borrador. No se ha enviado nada.",
        )
        saved = await commit_state(
            ctx,
            replace(current, payment=None).remembered(DialogueTurn(ctx.sentence, result.text)),
            result,
            account_id=UUID(state["account_id"]),
            expected_version=state["expected_version"],
        )
        ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = (
            saved,
            result,
            saved,
        )
        return {"status": "committed", "expected_version": saved.version}
    if state.get("status") == "paused_payment":
        current = await _canonical(state, ctx)
        payment = current.payment
        if not isinstance(payment, PaymentDraftPending) or not payment.suspended:
            raise RuntimeError("paused-payment reply has no suspended draft")
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
            text=payment_writer.paused(payment.draft),
        )
        saved = await commit_state(
            ctx,
            current.remembered(DialogueTurn(ctx.sentence, result.text)),
            result,
            account_id=UUID(state["account_id"]),
            expected_version=state["expected_version"],
        )
        ctx.cache["result"], ctx.cache["saved"] = result, saved
        return {"status": "committed", "expected_version": saved.version}
    if any(isinstance(op, AskCorrection) for op in codec.loads(state["ops"])):
        return await _ask_correction_and_commit(state, ctx)
    if any(isinstance(op, ListCategories) for op in codec.loads(state["ops"])):
        return await _list_categories_and_commit(state, ctx)
    if any(isinstance(op, ListContacts) for op in codec.loads(state["ops"])):
        return await _list_contacts_and_commit(state, ctx)
    moved = await _transition(state, ctx)
    grounds = await _grounds(state, ctx)
    names = {holding.account_id: holding.display_name for holding in grounds.accounts}
    if moved.work is None:
        assert not isinstance(moved.reply, Delegate)
        result = presenter.replied(
            moved.reply,
            moved.state,
            names,
            today=ctx.today,
            payments_available=(
                ctx.ports.commands is not None and Scope.PAYMENTS_PREPARE.value in ctx.caller_scopes
            ),
        )
        in_words = result.text
    elif "changes" in state:
        result = graph_analysis.investigated(state, moved.work, names, moved.work.account_ids)
        in_words = result.plan_in_words or ""
    elif isinstance(moved.work.plan, ComparisonPlan) and "comparison_counts" in state:
        pair_run = graph_artifacts.pair_run_from_state(state, moved.work.plan)
        ctx.cache["run"] = pair_run
        result = presenter.answered(
            pair_run,
            scope=moved.work.account_ids,
            account_names=names,
            reporting_timezone=ctx.reporting_timezone,
        )
        in_words = result.plan_in_words or ""
    else:
        # The execution node checkpointed the closed banking result before this presentation node.
        # This node must not call a bank port: replay uses the same result and cannot double-read.
        ordinary_run = codec.loads_typed(state["execution_result"])
        ctx.cache["run"] = ordinary_run
        result = presenter.answered(
            ordinary_run,
            scope=moved.work.account_ids,
            account_names=names,
            reporting_timezone=ctx.reporting_timezone,
        )
        in_words = result.plan_in_words or ""
    canonical = moved.state
    if isinstance(canonical.payment, PaymentDraftPending) and not any(
        isinstance(op, (PrepareBizum, PrepareTransfer, PrepareOwnTransfer))
        for op in codec.loads(state["ops"])
    ):
        # A different subject takes the floor, so a bare slot answer on a later turn cannot
        # complete this payment by accident. Listing contacts is handled above and does not pause
        # it.
        canonical = replace(
            canonical,
            payment=replace(canonical.payment, suspended=True),
        )
    # Keep a bounded aggregate or chart-ready movement-page summary for later visualization.
    # Raw movement evidence remains in the response and is never copied into dialogue state.
    final_run = ctx.cache.get("run")
    query_id, artifact_id = (
        graph_artifacts.artifact_ids(state) if moved.work is not None else ("", "")
    )
    if moved.work is not None and moved.work.kind not in {
        "NO_MORE_MOVEMENTS",
        "BALANCE",
    }:
        # Do not let a chart request after a different kind of answer accidentally visualize an
        # older query. A completed query below installs its new bounded result.
        canonical = replace(
            canonical,
            active_artifact=None,
            active_movement_artifact=None,
            active_visual=None,
        )
    if isinstance(final_run, MovementListRun):
        canonical = replace(
            canonical,
            focus=MovementListFocus(replace(final_run.plan, cursor=final_run.answer.next_cursor)),
            active_movement_artifact=graph_artifacts.movement_list_artifact(
                final_run,
                result_version=state["expected_version"] + 1,
                query_id=query_id,
                artifact_id=artifact_id,
            ),
        )
    if isinstance(final_run, QueryRun):
        groups = final_run.answer.groups[:100]
        active_artifact = (
            FinancialArtifact(
                plan=final_run.plan,
                currency=final_run.answer.currency,
                total_minor=final_run.answer.total_minor,
                movement_count=final_run.answer.movement_count,
                groups=groups,
                query_id=query_id,
                artifact_id=artifact_id,
                period_start=final_run.answer.period_start,
                period_end=final_run.answer.period_end,
                result_version=state["expected_version"] + 1,
                result_kind=graph_artifacts.artifact_result_kind(final_run.plan),
                status=(
                    ArtifactStatus.EMPTY
                    if final_run.answer.is_empty
                    else ArtifactStatus.PARTIAL
                    if len(final_run.answer.groups) > len(groups)
                    else ArtifactStatus.VERIFIED
                ),
                source=ArtifactSource.BANKING_API,
                provenance=(query_id,),
            )
            if all(len(group.key) <= 256 for group in groups)
            else None
        )
        canonical = replace(canonical, active_artifact=active_artifact)
    elif moved.work is not None and "changes" in state:
        assert isinstance(moved.work.plan, ComparisonPlan)
        earlier_groups, latest_groups = state.get("comparison_groups", [[], []])
        latest_plan = moved.work.plan.latest
        prior = tuple(Group(*group) for group in earlier_groups[:100])
        latest = tuple(Group(*group) for group in latest_groups[:100])
        comparison = graph_artifacts.pair_run_from_state(state, moved.work.plan)
        active_artifact = FinancialArtifact(
            plan=latest_plan,
            currency=state["currency"],
            total_minor=state["totals"][1],
            movement_count=state["comparison_counts"][1],
            groups=latest,
            query_id=query_id,
            artifact_id=artifact_id,
            result_version=state["expected_version"] + 1,
            comparison=moved.work.plan,
            comparison_groups=(prior, latest),
            comparison_values=(
                answer_writer.comparison_value(comparison.earlier, moved.work.plan.earlier),
                answer_writer.comparison_value(comparison.latest, moved.work.plan.latest),
            ),
            result_kind=ResultKind.COMPARISON,
            status=(
                ArtifactStatus.EMPTY
                if not any(state["comparison_counts"])
                else ArtifactStatus.PARTIAL
                if len(earlier_groups) > len(prior) or len(latest_groups) > len(latest)
                else ArtifactStatus.VERIFIED
            ),
            source=ArtifactSource.BANKING_API,
            provenance=(query_id,),
        )
        canonical = replace(canonical, active_artifact=active_artifact)
    elif isinstance(final_run, PairRun):
        groups = final_run.latest.groups[:100]
        prior = final_run.earlier.groups[:100]
        if all(len(group.key) <= 256 for group in (*prior, *groups)):
            canonical = replace(
                canonical,
                active_artifact=FinancialArtifact(
                    plan=final_run.pair.latest,
                    currency=final_run.latest.currency,
                    total_minor=abs(final_run.latest.total_minor),
                    movement_count=final_run.latest.movement_count,
                    groups=groups,
                    query_id=query_id,
                    artifact_id=artifact_id,
                    result_version=state["expected_version"] + 1,
                    comparison=final_run.pair,
                    comparison_groups=(prior, groups),
                    comparison_values=(
                        answer_writer.comparison_value(final_run.earlier, final_run.pair.earlier),
                        answer_writer.comparison_value(final_run.latest, final_run.pair.latest),
                    ),
                    result_kind=ResultKind.COMPARISON,
                    status=(
                        ArtifactStatus.EMPTY
                        if final_run.earlier.is_empty and final_run.latest.is_empty
                        else ArtifactStatus.PARTIAL
                        if len(final_run.earlier.groups) > len(prior)
                        or len(final_run.latest.groups) > len(groups)
                        else ArtifactStatus.VERIFIED
                    ),
                    source=ArtifactSource.BANKING_API,
                    provenance=(query_id,),
                ),
            )
    context_plan = graph_artifacts.context_plan(final_run)
    if context_plan is not None:
        plan_context = graph_artifacts.spoken_plan(context_plan)
        if canonical.active_artifact is not None:
            plan_context = replace(
                plan_context,
                query_id=canonical.active_artifact.query_id or None,
                artifact_id=canonical.active_artifact.artifact_id or None,
            )
        result = replace(result, plan_context=plan_context)
    remembered = canonical.remembered(DialogueTurn(said=ctx.sentence, answered=in_words))
    if any(
        isinstance(op, (PrepareBizum, PrepareTransfer, PrepareOwnTransfer))
        for op in codec.loads(state["ops"])
    ):
        # A mixed analytics/payment turn has one canonical commit. Keep the bounded typed result in
        # the graph checkpoint while payment preparation runs so a retry never repeats the query.
        ctx.cache["result"] = result
        return {
            "status": "committed",
            "pending_state": codec.dumps_state(remembered),
            "pending_result": codec.dumps_typed(result),
            "bank_calls": graph_execution.record_bank_calls(state, ctx),
        }
    if state.get("visual_after_query"):
        # The following graph node combines this bank-backed answer with its view and commits both
        # together. Persisting here would advance the conversation version twice in one turn.
        ctx.cache["result"] = result
        return {
            "status": "needs_visualization",
            "pending_state": codec.dumps_state(remembered),
            "pending_result": codec.dumps_typed(result),
            "bank_calls": graph_execution.record_bank_calls(state, ctx),
        }
    saved = await commit_state(
        ctx,
        remembered,
        result,
        account_id=UUID(state["account_id"]),
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"] = saved
    ctx.cache["result"], ctx.cache["saved"] = result, saved
    return {
        "status": (
            "payment_waiting"
            if isinstance(saved.payment, PaymentConfirmationPending)
            else "committed"
        ),
        "expected_version": saved.version,
        "payment_workflow_id": (
            saved.payment.workflow_id
            if isinstance(saved.payment, PaymentConfirmationPending)
            else ""
        ),
        "bank_calls": graph_execution.record_bank_calls(state, ctx),
        "rearm_payment": isinstance(saved.payment, PaymentConfirmationPending),
    }


async def _list_contacts_and_commit(state: TurnGraphState, ctx: GraphContext) -> TurnGraphState:
    """Read payees while retaining the canonical payment draft, as a normal conversation turn."""
    account_id = UUID(state["account_id"])
    current = await _canonical(state, ctx)
    if "composition" in state.get("refused", []):
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text="No puedo combinar la consulta de contactos con otra operación en el mismo turno.",
        )
    elif "bank_budget" in state.get("refused", []):
        result = AskFinancialQuestionResult(
            outcome="BANKING_UNAVAILABLE",
            text="He alcanzado el límite de consultas de este turno. Puedes volver a preguntarlo.",
        )
    else:
        ctx.unsaved_calls += 1
        contacts = await ctx.ports.contacts.all_of(account_id)
        result = AskFinancialQuestionResult(
            outcome=Spoken.CONTACTS.value,
            text=answer_writer.contacts(contacts),
            contacts=[
                SpokenContact(str(item.contact_id), item.display_name, item.phone)
                for item in contacts
            ],
        )
    remembered = current.remembered(DialogueTurn(ctx.sentence, result.text))
    saved = await commit_state(
        ctx,
        remembered,
        result,
        account_id=account_id,
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = saved, result, saved
    return {
        "status": (
            "payment_waiting"
            if isinstance(saved.payment, PaymentConfirmationPending)
            else "committed"
        ),
        "expected_version": saved.version,
        "payment_workflow_id": (
            saved.payment.workflow_id
            if isinstance(saved.payment, PaymentConfirmationPending)
            else ""
        ),
        "bank_calls": graph_execution.record_bank_calls(state, ctx),
        "rearm_payment": isinstance(saved.payment, PaymentConfirmationPending),
    }


async def _list_categories_and_commit(state: TurnGraphState, ctx: GraphContext) -> TurnGraphState:
    """Answer from the product vocabulary without touching the bank or focus."""
    account_id = UUID(state["account_id"])
    current = await _canonical(state, ctx)
    if "composition" in state.get("refused", []):
        result = AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text="La lista de categorías debe pedirse por separado.",
        )
    else:
        result = AskFinancialQuestionResult(
            outcome=Spoken.CATEGORIES.value,
            text=explainer.category_catalogue(),
        )
    saved = await commit_state(
        ctx,
        current.remembered(DialogueTurn(ctx.sentence, result.text)),
        result,
        account_id=account_id,
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = saved, result, saved
    return {
        "status": (
            "payment_waiting"
            if isinstance(saved.payment, PaymentConfirmationPending)
            else "committed"
        ),
        "expected_version": saved.version,
        "payment_workflow_id": (
            saved.payment.workflow_id
            if isinstance(saved.payment, PaymentConfirmationPending)
            else ""
        ),
        "rearm_payment": isinstance(saved.payment, PaymentConfirmationPending),
    }


async def _ask_correction_and_commit(state: TurnGraphState, ctx: GraphContext) -> TurnGraphState:
    """Open a typed correction question without discarding the query or payment state."""
    account_id = UUID(state["account_id"])
    current = await _canonical(state, ctx)
    if current.focus is None:
        pending = Pending(Missing.SUBJECT, PlanFields(), AccountScope())
        text = "¿Qué consulta quieres hacer o corregir?"
    else:
        pending = Pending(Missing.CORRECTION, PlanFields(), current.scope)
        text = "¿Qué parte de la consulta anterior querías corregir?"
    result = AskFinancialQuestionResult(
        outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
        text=text,
    )
    saved = await commit_state(
        ctx,
        replace(_remember_result(current, ctx.sentence, result.text), pending=pending),
        result,
        account_id=account_id,
        expected_version=state["expected_version"],
    )
    ctx.cache["dialogue"], ctx.cache["result"], ctx.cache["saved"] = saved, result, saved
    return {
        "status": (
            "payment_waiting"
            if isinstance(saved.payment, PaymentConfirmationPending)
            else "committed"
        ),
        "expected_version": saved.version,
        "payment_workflow_id": (
            saved.payment.workflow_id
            if isinstance(saved.payment, PaymentConfirmationPending)
            else ""
        ),
        "rearm_payment": isinstance(saved.payment, PaymentConfirmationPending),
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
        case "compare":
            return "execute_read_only"
        case "replied" | "refused" | "paused_payment" | "cancel_payment_draft":
            return "present_and_commit"
        case "canonical":
            return "execute_read_only"
        case _:
            return "execute_read_only"


def _after_load(state: TurnGraphState) -> str:
    return "confirm_payment" if state.get("direct_payment_confirmation") else "plan"


def _after_presentation(state: TurnGraphState) -> str:
    if any(
        isinstance(op, (PrepareBizum, PrepareTransfer, PrepareOwnTransfer))
        for op in codec.loads(state["ops"])
    ):
        return "prepare_payment"
    if state.get("visual_after_query"):
        return "visualize_and_commit"
    return "wait_for_payment_reply" if state.get("rearm_payment") else END


def _after_visualization(state: TurnGraphState) -> str:
    return "wait_for_payment_reply" if state.get("rearm_payment") else END


def _after_payment_prepare(state: TurnGraphState) -> str:
    return (
        "wait_for_payment_reply"
        if state["status"] == "payment_waiting" or state.get("rearm_payment")
        else END
    )


def _after_payment_confirmation(state: TurnGraphState) -> str:
    return "wait_for_payment_reply" if state["status"] == "payment_waiting" else END


def _after_execution(state: TurnGraphState) -> str:
    """The latest window is the next step's read until it is read."""
    return (
        "execute_read_only"
        if state["status"] in {"investigate", "compare_latest"}
        else "assess_result"
    )


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
    graph.add_conditional_edges("load_and_reserve", _after_load, ["confirm_payment", "plan"])
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
    graph.add_conditional_edges(
        "visualize_and_commit", _after_visualization, ["wait_for_payment_reply", END]
    )
    graph.add_conditional_edges("prepare_payment", _after_payment_prepare)
    graph.add_conditional_edges(
        "wait_for_payment_reply",
        _after_payment_reply,
        ["plan", "confirm_payment"],
    )
    graph.add_conditional_edges("confirm_payment", _after_payment_confirmation)
    return graph.compile(checkpointer=checkpointer)


# --- adapters over the engine's pieces ------------------------------------------------------------


async def _transition(state: TurnGraphState, ctx: GraphContext) -> Transition:
    """`transitions.apply` over the planned operations, with the comparison's merchant breakdown
    set aside: a pair is one figure per side, so the canonical transition refuses a grouping on it,
    and the graph is what reads each side by merchant instead. Pure, so recomputing it on a resume
    gives the same transition."""
    if "transition" not in ctx.cache:
        planned = codec.loads(state["ops"])
        if state.get("visual_after_query"):
            # The query and chart edit are one graph turn, but the domain transition must see
            # only the financial operation. Visualization is a graph concern, not a Delegate
            # for another conversational coordinator.
            planned = tuple(op for op in planned if not isinstance(op, Visualize))
        ops = tuple(
            _without_merchant_grouping(op)
            for op in planned
            if not isinstance(op, (PrepareBizum, PrepareTransfer, PrepareOwnTransfer))
        )
        current = await _canonical(state, ctx)
        ctx.cache["transition"] = apply_guarded(current, ops, ctx.today, await _grounds(state, ctx))
    transition: Transition = ctx.cache["transition"]
    return transition


def _by_merchant(ops: tuple[Operation, ...]) -> bool:
    """Whether the turn compares two windows by merchant: the capability's trigger, read off the
    interpreter's typed operations and never off the sentence."""
    return any(
        isinstance(op, InvestigateComparison)
        or (
            isinstance(op, Compare)
            and GroupBy.MERCHANT in (op.shared.group_by, op.earlier.group_by, op.latest.group_by)
        )
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


async def _inspect(state: TurnGraphState, work: Work, ctx: GraphContext) -> TurnGraphState:
    """The largest purchase at the merchant asked about, in the latest window and the same scope
    and filters the validated comparison carries: a ranking banking answers."""
    position = state["next_inspect"]
    assert position is not None and isinstance(work.plan, ComparisonPlan)
    merchant = graph_analysis.may_inspect(state)[position]
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
    run = await graph_execution.execute_work(Work("QUERY", ranking, work.account_ids), state, ctx)
    assert isinstance(run, RankingRun)
    largest = run.movements[0] if run.movements else None
    found = [
        merchant,
        None if largest is None else largest.amount_minor,
        None if largest is None else largest.occurred_at.isoformat(),
    ]
    return {
        "status": "executed",
        "bank_calls": graph_execution.record_bank_calls(state, ctx),
        "inspected": [*state.get("inspected", []), found],
        "next_inspect": None,
    }
