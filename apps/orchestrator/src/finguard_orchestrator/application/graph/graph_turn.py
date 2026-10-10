"""One turn through the unified conversation graph, with the guarantees it does not own.

What stays here and out of LangGraph: the version check, the turn reservation (idempotency), and
the decision to resume, replay or refuse. The store's compare-and-set stays in the graph's commit
nodes. Bizum, saved-contact transfers and transfers between the holder's own accounts run through
the graph and existing guarded banking commands; transfers to typed bank details and bookings
remain explicitly unavailable.

- **New request** against the held version: reserved, then run from the start under a thread id
  made of the account, conversation, version and request id.
- **Same request again** after it failed (a bank or provider outage): resumed from its last
  checkpoint, so the model is not asked again for what it already answered and nothing is saved
  twice. After it succeeded: the same answer, replayed, with no model or bank work.
- **Another request** against a version a turn is in flight on or already done with: stale, as
  the canonical engine answers a version that moved.
- **A checkpoint from another graph or schema version** is not resumed: it is dropped and the
  turn runs again from the start, which is safe because everything before the save is a read.

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

from finguard_orchestrator.application.engine import presenter
from finguard_orchestrator.application.engine.executor import MixedCurrenciesError
from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.application.graph import graph as investigation_graph
from finguard_orchestrator.application.graph.graph import (
    GRAPH_VERSION,
    RECURSION_LIMIT,
    STATE_SCHEMA_VERSION,
    GraphContext,
    GraphPorts,
    ReadBudgetExhaustedError,
    TurnGraphState,
)
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.domain.engine.operations import Delegate
from finguard_orchestrator.domain.engine.state import (
    DialogueState,
    DialogueTurn,
    OwnTransferDraftPending,
    PaymentConfirmationPending,
    PaymentDraftPending,
)
from finguard_orchestrator.domain.enums import PlanningOutcome
from finguard_orchestrator.domain.errors import (
    BankingUnavailableError,
    DomainError,
    InvalidValueError,
    QueryRejectedError,
)
from finguard_orchestrator.domain.models.conversation import Workflow
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.ports.turn_operations import InterpretationFailedError
from finguard_orchestrator.domain.ports.turn_reservations import (
    Reservation,
    TurnKey,
    TurnReservationsPort,
)
from finguard_orchestrator.domain.services import confirmation

logger = logging.getLogger(__name__)


class RequestReusedError(DomainError):
    """A request id sent again with another sentence: it is not the same turn, so it is neither
    resumed nor replayed. Only reachable by a caller that sends ids; the HTTP routes send none."""

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
    held: DialogueState


class GraphTurnHandler:
    """Answers a conversational turn through the unified LangGraph flow."""

    supports_idempotency = True
    supports_caller_scopes = True
    authoritative = True

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
    ) -> EngineAnswer:
        """`request_id` is the turn's idempotency key; without one every call is a new request."""
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
            thread_id=config["configurable"]["thread_id"],
        )

        def unsaved(result: AskFinancialQuestionResult) -> EngineAnswer:
            calls = self._ports.count_calls() - calls_before
            return EngineAnswer(result, turn.held, None, calls, _ms(started))

        answer: EngineAnswer | None = None
        closed = False
        try:
            try:
                final = await self._graph.ainvoke(
                    await self._input(turn, config, sentence), config, context=context
                )
            except _UNANSWERED as unanswered:
                # Resumable by the same request: its checkpoint stays.
                logger.warning("graph.turn_unanswered error=%s", type(unanswered).__name__)
                return unsaved(_failed(unanswered))
            if final.get("status") == "delegated":
                ops = codec.loads(final["ops"])
                delegate = next(op for op in ops if isinstance(op, Delegate))
                result = _unavailable(delegate.to)
                saved = await self._ports.store.save(
                    turn.held.remembered(DialogueTurn(sentence, result.text)),
                    account_id=turn.key.account_id,
                    expected_version=turn.key.expected_version,
                )
                answer = EngineAnswer(
                    result,
                    saved,
                    None,
                    self._ports.count_calls() - calls_before,
                    _ms(started),
                )
                await self._reservations.done(turn.key, turn.request_id, answer)
                closed = True
                return answer
            answer = EngineAnswer(
                context.cache["result"],
                context.cache["saved"],
                None,
                self._ports.count_calls() - calls_before,
                _ms(started),
            )
            await self._reservations.done(turn.key, turn.request_id, answer)
            closed = True
            return answer
        finally:
            if not closed:
                await self._close(turn, config, context, answer)

    async def _close(
        self, turn: _Turn, config: Any, context: GraphContext, answer: EngineAnswer | None
    ) -> None:
        """The reservation of a turn that did not close normally, whatever ended it (R43).

        **Saved** (the compare-and-set went through, `done()` did not return): the turn is closed
        as done, never marked resumable, because resuming it would run a saved turn again. If that
        fails too, the reservation stays in flight and a duplicate is stale, which is still true.

        **Not saved**: the calls the failed step made go into the checkpoint (R44), and the
        reservation is released in a `finally`, so neither a persist that raises nor a cancellation
        during it can leave the turn held. A persist error is logged, not raised: it must not mask
        the failure that ended the turn, nor the answer already given for it. Each step is
        shielded, so a second cancellation cannot interrupt it half done.
        """
        if answer is not None:
            try:
                await asyncio.shield(self._reservations.done(turn.key, turn.request_id, answer))
            except Exception as failed:
                logger.error("graph.saved_turn_not_closed error=%s", type(failed).__name__)
            return
        try:
            if context.unsaved_calls:
                await asyncio.shield(self._persist_calls(config, context.unsaved_calls))
        except Exception as failed:
            logger.error("graph.calls_not_persisted error=%s", type(failed).__name__)
        finally:
            await asyncio.shield(self._reservations.failed(turn.key, turn.request_id))

    async def _persist_calls(self, config: Any, calls: int) -> None:
        found = await self._graph.aget_state(config)
        if found.values:
            stored = found.values.get("bank_calls", 0)
            await self._graph.aupdate_state(config, {"bank_calls": stored + calls})

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
                done: EngineAnswer = found.answer
                return done
        if held is not None and version is not None and version != held.version:
            raise StaleDialogueError(held)
        held = held or DialogueState(derived)
        key = TurnKey(account_id, held.conversation_id, held.version)
        reserved = await self._reservations.reserve(key, request_id, fingerprint)
        if reserved.outcome is Reservation.REUSED:
            raise RequestReusedError(request_id)
        if reserved.outcome is Reservation.TAKEN:
            raise StaleDialogueError(held)
        if reserved.outcome is Reservation.DONE:
            replayed: EngineAnswer = reserved.answer
            return replayed
        return _Turn(key, request_id, held)

    async def _input(
        self, turn: _Turn, config: Any, sentence: str
    ) -> TurnGraphState | Command[Any] | None:
        """None to resume the turn's own checkpoint; a fresh state to start (or start again)."""
        found = await self._graph.aget_state(config)
        values = found.values
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
                        pending.workflow_id != config["configurable"]["thread_id"]
                        or values.get("status") != "payment_waiting"
                    ):
                        raise StaleDialogueError(turn.held)
                    return Command(
                        # Checkpoints hold only the decision, never the user's free-form reply.
                        resume=confirmation.canonical_reply(sentence),
                        update={
                            "turn_id": turn.request_id,
                            "expected_version": turn.key.expected_version,
                        },
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
            "model_calls": 0,
            "bank_calls": 0,
            "partial": False,
            "refused": [],
            "next_inspect": None,
        }
        pending = turn.held.payment
        if (
            isinstance(pending, PaymentConfirmationPending)
            and pending.workflow_id == config["configurable"]["thread_id"]
        ):
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


def _unavailable(capability: str) -> AskFinancialQuestionResult:
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
    pending = turn.held.payment
    if isinstance(pending, PaymentConfirmationPending) and pending.workflow_id:
        return pending.workflow_id
    key = turn.key
    return f"{key.account_id}:{key.conversation_id}:{key.expected_version}:{turn.request_id}"


def _ms(started: float) -> dict[str, int]:
    return {"graph": round((time.perf_counter() - started) * 1000)}
