"""One turn through the unified conversation graph, with the guarantees it does not own.

What stays here and out of LangGraph: the version check, the turn reservation (idempotency), and
the decision to resume, replay or refuse. The store's compare-and-set stays in the graph's commit
nodes. Bizum, saved-contact transfers and transfers between the holder's own accounts run through
the graph and existing guarded banking commands; transfers to typed bank details and bookings
remain explicitly unavailable.

- **New request** against the held version: reserved, then run from the start under a thread id
  made of the account, conversation, version and request id.
- **Same request again** after it failed before the canonical compare-and-set: resumed from its
  last checkpoint, so the model is not asked again for what it already answered and nothing is
  saved twice. The canonical CAS and a replayable result/state snapshot are committed together in
  PostgreSQL. If the process exits before the reservation is finalized as `DONE`, the same request
  replays that committed result and state without model or bank work; it cannot repeat a financial
  action. Normal completion upgrades the recoverable reservation to the full `EngineAnswer`.
- **Another request** against a version a turn is in flight on or already done with: stale, as
  the canonical engine answers a version that moved.
- **A new payment reply** gets its own request checkpoint thread and starts a fresh turn budget and
  query state; the stable payment workflow id and bank-authored preview remain in canonical state.
  A retry with the same request id keeps its checkpointed counters and evidence.
- **A checkpoint from another graph or schema version** is not resumed: it is dropped and the
  turn runs again from the start with same-request model and bank budgets carried forward. Read
  work may repeat; a pending payment confirmation is rebuilt from the canonical bank preview and
  still goes through the guarded confirmation command.

The checkpointer is injected by the composition root. Production graph mode uses LangGraph's
PostgreSQL saver; in-memory saver instances are for tests only. Canonical dialogue state and turn
reservations are stored separately with their own schema versions, account scope and CAS rules.
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from finguard_orchestrator.application.engine import form_actions, presenter
from finguard_orchestrator.application.engine.budgets import ReadBudgetExhaustedError
from finguard_orchestrator.application.engine.executor import MixedCurrenciesError
from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.engine.operations import Delegate, Operation
from finguard_orchestrator.domain.engine.state import (
    DialogueState,
    DialogueTurn,
    OwnTransferDraftPending,
    PaymentConfirmationPending,
    PaymentDraftPending,
)
from finguard_orchestrator.domain.enums import PlanningOutcome, Spoken
from finguard_orchestrator.domain.errors import (
    BankingUnavailableError,
    CheckpointCleanupUnavailableError,
    DomainError,
    InvalidValueError,
    QueryRejectedError,
    TurnCheckpointExpiredError,
)
from finguard_orchestrator.domain.models.conversation import Workflow
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.ports.turn_operations import InterpretationFailedError
from finguard_orchestrator.domain.ports.turn_reservations import (
    CommittedTurnResult,
    Reservation,
    TurnKey,
    TurnReservationsPort,
)
from finguard_orchestrator.domain.services import confirmation
from finguard_orchestrator.infrastructure.graph import codec
from finguard_orchestrator.infrastructure.graph import graph as investigation_graph
from finguard_orchestrator.infrastructure.graph.graph import (
    GRAPH_VERSION,
    RECURSION_LIMIT,
    STATE_SCHEMA_VERSION,
)
from finguard_orchestrator.infrastructure.graph.graph_state import (
    GraphContext,
    GraphPorts,
    TurnGraphState,
)
from finguard_orchestrator.infrastructure.graph.graph_store import commit_state

logger = logging.getLogger(__name__)
_RESERVATION_HEARTBEAT_SECONDS = 30
_TURN_CHECKPOINT_RETENTION_SECONDS = 7 * 24 * 60 * 60


class RequestReusedError(DomainError):
    """A request id sent again with another sentence: it is not the same turn, so it is neither
    resumed nor replayed. The HTTP routes use the caller's `Idempotency-Key` as this request id."""

    code = "request_reused"


# What a turn can run into that is answered honestly and saves nothing, as in `answer_turn`.
_UNANSWERED = (
    BankingUnavailableError,
    QueryRejectedError,
    MixedCurrenciesError,
    InvalidValueError,
    InterpretationFailedError,
    ReadBudgetExhaustedError,
)


@dataclass(frozen=True)
class _Turn:
    key: TurnKey
    request_id: str
    lease_token: UUID
    held: DialogueState


class GraphTurnHandler:
    """Answers a conversational turn through the unified LangGraph flow."""

    def __init__(
        self,
        ports: GraphPorts,
        reservations: TurnReservationsPort,
        checkpointer: BaseCheckpointSaver[Any],
    ) -> None:
        self._ports = ports
        self._reservations = reservations
        self._checkpointer = checkpointer
        self._graph = investigation_graph.build(checkpointer)

    async def owns_workflow(
        self, conversation_id: UUID, *, account_id: UUID, workflow: Workflow
    ) -> bool:
        """Whether a wire workflow belongs to this graph's account-scoped canonical state.

        Legacy sessions can also arrive with payment fields. Graph mode may resume a confirmation
        only when the same account and conversation have the corresponding durable state; a wire
        label alone never proves ownership. The version is checked by ``answer`` so a stale but
        graph-owned workflow receives the normal 409 response.
        """
        held = await self._ports.store.load(conversation_id, account_id=account_id)
        if held is None:
            return False
        match workflow:
            case Workflow.PAYMENT_CONFIRMATION:
                return isinstance(held.payment, PaymentConfirmationPending)
            case Workflow.PAYMENT_IN_PROGRESS:
                return isinstance(held.payment, PaymentDraftPending)
            case Workflow.TRANSFER_IN_PROGRESS:
                return isinstance(held.own_transfer, OwnTransferDraftPending)
            case _:
                return False

    async def act(
        self,
        action: form_actions.FormAction,
        *,
        conversation_id: UUID | None,
        version: int | None,
        account_id: UUID,
        today: date,
        reporting_timezone: str,
        request_id: str | None = None,
        caller_scopes: frozenset[str] = frozenset(),
    ) -> EngineAnswer:
        """A form's action as a turn of this conversation: the same preview, state and confirmation
        a chat payment gets, without a model call."""
        turn: dict[str, Any] = {
            "conversation_id": conversation_id,
            "version": version,
            "account_id": account_id,
            "today": today,
            "reporting_timezone": reporting_timezone,
            "request_id": request_id,
            "caller_scopes": caller_scopes,
        }
        if isinstance(action, form_actions.AnswerPreview):
            # Only a preview this conversation holds can be answered; anything else is stale, and
            # a yes must never reach the interpreter as a new sentence. A resend of an answer that
            # already ran is replayed by `answer`, which is why it is looked up first.
            reply = form_actions.reply_to(action)
            held = (
                await self._ports.store.load(conversation_id, account_id=account_id)
                if conversation_id is not None
                else None
            )
            answered = (
                held is not None
                and version is not None
                and request_id is not None
                and await self._reservations.replayed(
                    TurnKey(account_id, held.conversation_id, version),
                    request_id,
                    _fingerprint(reply),
                )
                is not None
            )
            if not answered and (
                held is None or not isinstance(held.payment, PaymentConfirmationPending)
            ):
                raise StaleDialogueError(held or DialogueState(conversation_id or uuid4()))
            return await self.answer(reply, **turn)
        if isinstance(action, form_actions.MoveBetweenAccounts):
            # Named as the holder's own directory names it, so the graph grounds it the way it
            # grounds «a la Hucha»; an id that is not one of theirs never becomes a name.
            destination = next(
                (
                    holding.display_name
                    for holding in await self._ports.accounts.all_of(account_id)
                    if holding.account_id == action.to_account_id
                ),
                None,
            )
            if destination is None:
                raise InvalidValueError("the destination is not one of your accounts")
            sentence, operations = form_actions.own_transfer_turn(destination, action.amount_minor)
        else:
            sentence, operations = form_actions.as_turn(action)
        return await self.answer(sentence, operations=operations, **turn)

    async def answer(
        self,
        sentence: str,
        *,
        conversation_id: UUID | None,
        version: int | None,
        account_id: UUID,
        today: date,
        reporting_timezone: str,
        request_id: str | None = None,
        caller_scopes: frozenset[str] = frozenset(),
        operations: tuple[Operation, ...] | None = None,
    ) -> EngineAnswer:
        """`request_id` is the turn's idempotency key; without one every call is a new request.
        `operations`, when given, replace the interpreter's reading of `sentence` (a form)."""
        calls_before = self._ports.count_calls()
        started = time.perf_counter()
        turn = await self._reserve(
            conversation_id,
            version,
            account_id,
            request_id or uuid4().hex,
            _fingerprint(sentence),
        )
        if isinstance(turn, EngineAnswer):
            return turn  # replayed
        config: Any = {
            "configurable": {"thread_id": _thread(turn)},
            "recursion_limit": RECURSION_LIMIT,
        }
        context = GraphContext(
            sentence,
            today,
            reporting_timezone,
            self._ports,
            caller_scopes=caller_scopes,
            operations=operations,
            thread_id=config["configurable"]["thread_id"],
            request_id=turn.request_id,
            lease_token=turn.lease_token,
            call_offset=calls_before,
        )

        def unsaved(
            result: AskFinancialQuestionResult, *, financial_reads: int | None = None
        ) -> EngineAnswer:
            calls = self._ports.count_calls() - calls_before
            return EngineAnswer(
                result,
                turn.held,
                None,
                calls,
                _ms(started),
                financial_reads=financial_reads,
            )

        answer: EngineAnswer | None = None
        closed = False
        lease_lost = False
        heartbeat = asyncio.create_task(self._heartbeat(turn))

        async def lease_bound(work: Any) -> Any:
            nonlocal lease_lost
            task = asyncio.create_task(work)
            try:
                completed, _ = await asyncio.wait(
                    (task, heartbeat), return_when=asyncio.FIRST_COMPLETED
                )
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            if heartbeat in completed:
                lease_lost = True
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                # Propagate the heartbeat's ownership-loss error to the request boundary.
                await heartbeat
                raise StaleDialogueError(turn.held)
            return await task

        try:
            try:
                initial = await lease_bound(self._input(turn, config, sentence, context))
                final = await lease_bound(self._graph.ainvoke(initial, config, context=context))
            except _UNANSWERED as unanswered:
                # Resumable by the same request: its checkpoint stays.
                logger.warning("graph.turn_unanswered error=%s", type(unanswered).__name__)
                checkpoint = await self._graph.aget_state(config)
                checkpoint_reads = (
                    checkpoint.values.get("bank_calls", 0) if checkpoint.values else 0
                )
                # The checkpoint carries earlier completed steps; the context carries reads made
                # by the step that failed before it could return its updated graph state.
                financial_reads = max(
                    context.financial_reads,
                    checkpoint_reads + context.unsaved_calls,
                )
                return unsaved(_failed(unanswered), financial_reads=financial_reads)
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            if final.get("status") == "delegated":
                ops = codec.loads(final["ops"])
                delegate = next(op for op in ops if isinstance(op, Delegate))
                payments_available = (
                    delegate.to == "PAYMENT"
                    and self._ports.commands is not None
                    and "payments:prepare" in caller_scopes
                )
                result = _unavailable(delegate.to, payments_available=payments_available)
                saved = await commit_state(
                    context,
                    turn.held.remembered(DialogueTurn(sentence, result.text)),
                    result,
                    account_id=turn.key.account_id,
                    expected_version=turn.key.expected_version,
                )
                answer = EngineAnswer(
                    result,
                    saved,
                    None,
                    self._ports.count_calls() - calls_before,
                    _ms(started),
                    financial_reads=final.get("bank_calls", 0),
                )
                await self._reservations.done(turn.key, turn.request_id, turn.lease_token, answer)
                closed = True
                await self._discard_completed_checkpoint(config)
                return answer
            answer = EngineAnswer(
                context.cache["result"],
                context.cache["saved"],
                None,
                self._ports.count_calls() - calls_before,
                _ms(started),
                financial_reads=final.get("bank_calls", 0),
            )
            await self._reservations.done(turn.key, turn.request_id, turn.lease_token, answer)
            closed = True
            await self._discard_completed_checkpoint(config)
            return answer
        finally:
            if not heartbeat.done():
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
            if not closed and not lease_lost:
                await self._close(turn, config, context, answer)

    async def _discard_completed_checkpoint(self, config: Any) -> None:
        """Remove turn-local bank evidence once its replayable result is committed.

        Each request has its own graph thread. A pending payment's stable workflow id lives in
        canonical dialogue state and is independent from that checkpoint thread, so deleting this
        request's checkpoint cannot race a later confirmation request.
        """
        try:
            await self._checkpointer.adelete_thread(config["configurable"]["thread_id"])
        except Exception as failed:
            # Cleanup must not turn an already committed financial answer into an HTTP failure.
            logger.warning("graph.checkpoint_cleanup_failed error=%s", type(failed).__name__)

    async def _heartbeat(self, turn: _Turn) -> None:
        """Keep a live turn reserved; a crashed worker's lease expires for safe recovery."""
        while True:
            await asyncio.sleep(_RESERVATION_HEARTBEAT_SECONDS)
            try:
                renewed = await self._reservations.heartbeat(
                    turn.key, turn.request_id, turn.lease_token
                )
                if not renewed:
                    raise StaleDialogueError(turn.held)
            except asyncio.CancelledError:
                raise
            except StaleDialogueError:
                raise
            except Exception as failed:
                # The canonical version/CAS remains the final concurrency guard if Postgres is
                # briefly unavailable. Keep retrying while this worker is still making progress.
                logger.warning("graph.turn_heartbeat_failed error=%s", type(failed).__name__)

    async def _close(
        self, turn: _Turn, config: Any, context: GraphContext, answer: EngineAnswer | None
    ) -> None:
        """The reservation of a turn that did not close normally, whatever ended it (R43).

        **Saved** (the compare-and-set went through, `done()` did not return): the turn is closed
        as done, never marked resumable, because resuming it would run a saved turn again. If that
        fails too, the reservation stays in flight and a duplicate is stale, which is still true.

        **Not saved**: the calls the failed step made go into the checkpoint (R44), and release is
        attempted in a `finally`, so neither a persist error nor cancellation skips the attempt.
        Persistence/release errors are logged, not raised: they must not mask the failure that
        ended the turn, nor an answer already given for it. Each step is shielded so its cleanup
        continues if the caller is cancelled again; if Postgres is unavailable, lease expiry is
        the recovery path.
        """
        if answer is not None:
            try:
                await asyncio.shield(
                    self._reservations.done(turn.key, turn.request_id, turn.lease_token, answer)
                )
                await asyncio.shield(self._discard_completed_checkpoint(config))
            except Exception as failed:
                logger.error("graph.saved_turn_not_closed error=%s", type(failed).__name__)
            return
        try:
            await asyncio.shield(self._persist_progress(config, context))
        except Exception as failed:
            logger.error("graph.calls_not_persisted error=%s", type(failed).__name__)
        finally:
            try:
                await asyncio.shield(
                    self._reservations.failed(turn.key, turn.request_id, turn.lease_token)
                )
            except Exception as failed:
                logger.error("graph.turn_release_failed error=%s", type(failed).__name__)

    async def _persist_progress(self, config: Any, context: GraphContext) -> None:
        """Checkpoint failed banking and model attempts before releasing this turn's lease."""
        found = await self._graph.aget_state(config)
        if found.values:
            updates: dict[str, int] = {}
            if context.unsaved_calls:
                updates["bank_calls"] = found.values.get("bank_calls", 0) + context.unsaved_calls
            attempted_model_units = max(0, context.ports.count_calls() - context.call_offset)
            checkpointed_model_units = max(
                0,
                found.values.get("model_calls", 0) - context.checkpoint_model_calls,
            )
            uncheckpointed_model_units = max(0, attempted_model_units - checkpointed_model_units)
            if uncheckpointed_model_units:
                updates["model_calls"] = (
                    found.values.get("model_calls", 0) + uncheckpointed_model_units
                )
            if updates:
                await self._graph.aupdate_state(config, updates)

    async def _reserve(
        self,
        conversation_id: UUID | None,
        version: int | None,
        account_id: UUID,
        request_id: str,
        fingerprint: str,
    ) -> "_Turn | EngineAnswer":
        # A new conversation's id is derived from the request, so the same request resumed or
        # replayed lands on the same conversation, and was sent against its version 0.
        derived = uuid5(NAMESPACE_URL, f"finguard:{account_id}:{request_id}")
        if conversation_id is None:
            conversation_id, version = derived, 0
        held = await self._ports.store.load(conversation_id, account_id=account_id)
        if held is not None and version is not None:
            sent = TurnKey(account_id, held.conversation_id, version)
            found = await self._reservations.replayed(sent, request_id, fingerprint)
            if found is not None and found.outcome is Reservation.REUSED:
                raise RequestReusedError(request_id)
            if found is not None:
                if found.outcome is Reservation.COMMITTED:
                    committed = found.answer
                    if (
                        not isinstance(committed, CommittedTurnResult)
                        or not isinstance(committed.result, AskFinancialQuestionResult)
                        or committed.state.conversation_id != conversation_id
                        or committed.state.version != version + 1
                    ):
                        raise RuntimeError(
                            "committed turn result does not match canonical conversation state"
                        )
                    return EngineAnswer(
                        committed.result,
                        committed.state,
                        None,
                        committed.model_calls,
                        {"recovered": 0},
                        financial_reads=committed.financial_reads,
                    )
                done: EngineAnswer = found.answer
                return done
        # An explicit conversation id is part of the canonical identity. If its row is missing,
        # never silently fork it under the request-derived id; reconstruct the empty version-zero
        # state with the requested id and let the same version check reject stale client state.
        held = held or DialogueState(conversation_id)
        if version is not None and version != held.version:
            raise StaleDialogueError(held)
        key = TurnKey(account_id, held.conversation_id, held.version)
        # Failed turns keep their checkpoint so an immediate same-key retry can resume without
        # repeating model or banking work. After seven days, fence that request before deleting
        # its temporary bank evidence. The canonical dialogue and any pending bank preview remain
        # independently durable; a new idempotency key can continue from that state.
        abandoned = await self._reservations.abandon_expired_checkpoints(
            key, retention_seconds=_TURN_CHECKPOINT_RETENTION_SECONDS
        )
        for expired in abandoned:
            expired_key = TurnKey(account_id, held.conversation_id, expired.expected_version)
            try:
                await self._checkpointer.adelete_thread(_thread_id(expired_key, expired.request_id))
            except Exception as failed:
                logger.warning(
                    "graph.expired_checkpoint_cleanup_failed error=%s",
                    type(failed).__name__,
                )
                # Keep the reservation ABANDONED and do not let a same-key retry resume the
                # checkpoint. The next request can retry deletion before taking ownership.
                raise CheckpointCleanupUnavailableError() from failed
            else:
                await self._reservations.checkpoint_deleted(expired_key, expired.request_id)
        reserved = await self._reservations.reserve(key, request_id, fingerprint)
        if reserved.outcome is Reservation.REUSED:
            raise RequestReusedError(request_id)
        if reserved.outcome is Reservation.EXPIRED:
            raise TurnCheckpointExpiredError()
        if reserved.outcome is Reservation.TAKEN:
            raise StaleDialogueError(held)
        if reserved.outcome is Reservation.DONE:
            replayed: EngineAnswer = reserved.answer
            return replayed
        if reserved.previous_request_id is not None:
            # A replacement request has claimed the lease for this conversation version. Its
            # predecessor can no longer resume, so discard that turn-local checkpoint before
            # starting the new thread. Cleanup failure must not strand the newly acquired lease.
            try:
                await self._checkpointer.adelete_thread(
                    _thread_id(key, reserved.previous_request_id)
                )
            except Exception as failed:
                logger.warning(
                    "graph.superseded_checkpoint_cleanup_failed error=%s",
                    type(failed).__name__,
                )
        if reserved.lease_token is None:
            raise RuntimeError("new or resumed turn reservation has no fencing token")
        return _Turn(key, request_id, reserved.lease_token, held)

    async def _input(
        self, turn: _Turn, config: Any, sentence: str, context: GraphContext
    ) -> TurnGraphState | Command[Any] | None:
        """None to resume the turn's own checkpoint; a fresh state to start (or start again)."""
        found = await self._graph.aget_state(config)
        values = found.values
        same_turn = bool(values and values.get("turn_id") == turn.request_id)
        carried_model_calls = int(values.get("model_calls", 0)) if same_turn else 0
        carried_bank_calls = int(values.get("bank_calls", 0)) if same_turn else 0
        context.checkpoint_model_calls = carried_model_calls
        context.financial_reads = carried_bank_calls
        if values and found.next:
            same = (
                values.get("graph_version") == GRAPH_VERSION
                and values.get("schema_version") == STATE_SCHEMA_VERSION
            )
            if same:
                logger.info("graph.resumed at=%s", ",".join(found.next))
                pending = turn.held.payment
                if isinstance(pending, PaymentConfirmationPending):
                    if (
                        values.get("payment_workflow_id") != pending.workflow_id
                        or values.get("status") != "payment_waiting"
                    ):
                        raise StaleDialogueError(turn.held)
                    update: dict[str, Any] = {
                        "turn_id": turn.request_id,
                        "expected_version": turn.key.expected_version,
                    }
                    if not same_turn:
                        update.update(_new_payment_turn_fields())
                        context.checkpoint_model_calls = 0
                        context.financial_reads = 0
                    return Command(
                        # Checkpoints hold only the decision, never the user's free-form reply.
                        resume=confirmation.canonical_reply(sentence),
                        update=update,
                    )
                return None
            logger.warning("graph.checkpoint_version_mismatch")
        if values:
            await self._checkpointer.adelete_thread(_thread(turn))
        fresh: TurnGraphState = {
            "graph_version": GRAPH_VERSION,
            "schema_version": STATE_SCHEMA_VERSION,
            "turn_id": turn.request_id,
            "account_id": str(turn.key.account_id),
            "conversation_id": str(turn.key.conversation_id),
            "expected_version": turn.key.expected_version,
            # A graph-version restart of the same request is still the same turn: carry its
            # already-spent budgets forward. A pending payment resumed with a new request id is a
            # new turn and starts with fresh budgets.
            "model_calls": carried_model_calls,
            "bank_calls": carried_bank_calls,
            "partial": False,
            "refused": [],
            "next_inspect": None,
        }
        pending = turn.held.payment
        if isinstance(pending, PaymentConfirmationPending):
            # Older releases also used the payment workflow id as the checkpoint thread id. Clean
            # up that legacy thread; this request has its own thread id now.
            await self._checkpointer.adelete_thread(pending.workflow_id)
            fresh["payment_workflow_id"] = pending.workflow_id
            reply = confirmation.canonical_reply(sentence)
            if reply != "respuesta ambigua":
                # A versioned checkpoint can be discarded during a graph upgrade, but a bank
                # preview is still live in canonical state. Route an explicit yes/no through the
                # guarded confirmation node even when its old interrupt checkpoint is unusable.
                fresh["direct_payment_confirmation"] = True
                fresh["payment_reply"] = reply
        return fresh


def _fingerprint(sentence: str) -> str:
    """What binds a request id to its sentence: a hash, compared and never read (R45)."""
    return hashlib.sha256(sentence.encode("utf-8")).hexdigest()


def _new_payment_turn_fields() -> dict[str, Any]:
    """Clear request-local data while retaining the canonical bank preview and workflow id.

    Request-specific threads make this compatibility path unnecessary for ordinary turns, but it
    remains useful if a checkpoint is recovered from an older workflow-scoped deployment.
    """
    return {
        "ops": codec.dumps(()),
        "model_calls": 0,
        "bank_calls": 0,
        "earlier_groups": [],
        "comparison_groups": [],
        "comparison_counts": [],
        "earlier_movement_count": 0,
        "earlier_total": 0,
        "earlier_currency": "",
        "changes": [],
        "totals": [],
        "currency": "",
        "inspected": [],
        "next_inspect": None,
        "partial": False,
        "refused": [],
        "payment_reply": "respuesta ambigua",
        "direct_payment_confirmation": False,
        "pending_state": "",
        "pending_result": "",
        "execution_result": "",
        "visual_after_query": False,
        "visual_style": "AUTO",
        "rearm_payment": False,
    }


def _failed(error: DomainError) -> AskFinancialQuestionResult:
    if isinstance(error, ReadBudgetExhaustedError):
        return AskFinancialQuestionResult(
            outcome="BANKING_UNAVAILABLE",
            text=(
                "No he podido completar las consultas al banco dentro del límite de esta "
                "pregunta. Vuelve a preguntármelo."
            ),
        )
    return presenter.failed(error)


def _unavailable(
    capability: str, *, payments_available: bool = False
) -> AskFinancialQuestionResult:
    if capability == "PAYMENT" and payments_available:
        return AskFinancialQuestionResult(
            outcome=Spoken.GUIDANCE.value,
            text=(
                "Sí. Puedo preparar un Bizum para uno de tus contactos guardados y enseñarte "
                "la vista previa. No se envía hasta que confirmes. Dime a quién y cuánto."
            ),
        )
    if capability == "UNSUPPORTED":
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.UNSUPPORTED.value,
            text="Todav\u00eda no puedo resolver esa operaci\u00f3n desde esta conversaci\u00f3n.",
        )
    if capability == "TRANSFER":
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.INVALID_DOMAIN.value,
            text=(
                "Solo puedo preparar transferencias a contactos bancarios guardados en este chat; "
                "no puedo usar datos de cuenta nuevos."
            ),
        )
    explanations = {
        "CHART": "Todavía no puedo crear gráficos desde esta conversación.",
        "PAYMENT": "Los pagos aún no están disponibles en este flujo conversacional.",
        "TRANSFER": "Las transferencias aún no están disponibles en este flujo conversacional.",
        "BOOKING": "Las reservas aún no están disponibles en este flujo conversacional.",
        "UNSUPPORTED": "Todavía no puedo resolver esa operación desde esta conversación.",
    }
    return AskFinancialQuestionResult(
        outcome=PlanningOutcome.INVALID_DOMAIN.value,
        text=explanations.get(capability, explanations["UNSUPPORTED"]),
    )


def _thread(turn: _Turn) -> str:
    return _thread_id(turn.key, turn.request_id)


def _thread_id(key: TurnKey, request_id: str) -> str:
    return f"{key.account_id}:{key.conversation_id}:{key.expected_version}:{request_id}"


def _ms(started: float) -> dict[str, int]:
    return {"graph": round((time.perf_counter() - started) * 1000)}
