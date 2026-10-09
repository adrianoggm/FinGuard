"""One turn through the investigation graph, with the guarantees the graph does not own.

What stays here and out of LangGraph (Task 8 brief): the version check, the turn's reservation
(idempotency) and the decision to resume, replay or refuse. The store's compare-and-set stays in
the presenter node, and money flows leave before any of it (a `Delegate` ends the graph, saved
nothing, and the dispatch hands the turn to the rules route that guards it).

- **New request** against the held version: reserved, then run from the start under a thread id
  made of the account, conversation, version and request id.
- **Same request again** after it failed (a bank or provider outage): resumed from its last
  checkpoint, so the model is not asked again for what it already answered and nothing is saved
  twice. After it succeeded: the same answer, replayed, with no model or bank work.
- **Another request** against a version a turn is in flight on or already done with: stale, as
  the canonical engine answers a version that moved.
- **A checkpoint from another graph or schema version** is not resumed: it is dropped and the
  turn runs again from the start, which is safe because everything before the save is a read.

The checkpointer is whatever the composition root passes. In this experiment it is LangGraph's
in-memory saver: a restart loses every checkpoint, so **durability is unproven** and no crash
recovery is claimed (see the Task 8 report).
"""

import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver

from finguard_orchestrator.application.engine import presenter
from finguard_orchestrator.application.engine.answer_turn import EngineAnswer
from finguard_orchestrator.application.engine.executor import MixedCurrenciesError
from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.application.graph import graph as investigation_graph
from finguard_orchestrator.application.graph.graph import (
    GRAPH_VERSION,
    RECURSION_LIMIT,
    STATE_SCHEMA_VERSION,
    GraphContext,
    GraphPorts,
    TurnGraphState,
)
from finguard_orchestrator.domain.engine.operations import Delegate
from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.errors import (
    BankingUnavailableError,
    InvalidValueError,
    QueryRejectedError,
)
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.ports.turn_operations import InterpretationFailedError
from finguard_orchestrator.domain.ports.turn_reservations import (
    Reservation,
    TurnKey,
    TurnReservationsPort,
)

logger = logging.getLogger(__name__)

# What a turn can run into that is answered honestly and saves nothing, as in `answer_turn`.
_UNANSWERED = (
    BankingUnavailableError,
    QueryRejectedError,
    MixedCurrenciesError,
    InvalidValueError,
    InterpretationFailedError,
)


@dataclass(frozen=True)
class _Turn:
    key: TurnKey
    request_id: str
    held: DialogueState


class GraphTurnHandler:
    """Answers a turn as `AnswerTurnHandler.answer` does, through the investigation graph."""

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
    ) -> EngineAnswer:
        """`request_id` is the turn's idempotency key; without one every call is a new request."""
        calls_before = self._ports.count_calls()
        started = time.perf_counter()
        turn = await self._reserve(conversation_id, version, account_id, request_id or uuid4().hex)
        if isinstance(turn, EngineAnswer):
            return turn  # replayed
        config: Any = {
            "configurable": {"thread_id": _thread(turn)},
            "recursion_limit": RECURSION_LIMIT,
        }
        context = GraphContext(sentence, today, reporting_timezone, self._ports)

        def unsaved(result: Any, delegate_to: str | None = None) -> EngineAnswer:
            calls = self._ports.count_calls() - calls_before
            return EngineAnswer(result, turn.held, delegate_to, calls, _ms(started))

        try:
            final = await self._graph.ainvoke(
                await self._input(turn, config), config, context=context
            )
        except StaleDialogueError:
            await self._reservations.failed(turn.key, turn.request_id)
            raise
        except _UNANSWERED as unanswered:
            # Resumable by the same request: its checkpoint stays.
            logger.warning("graph.turn_unanswered error=%s", type(unanswered).__name__)
            await self._reservations.failed(turn.key, turn.request_id)
            return unsaved(presenter.failed(unanswered))
        if final.get("status") == "delegated":
            await self._reservations.failed(turn.key, turn.request_id)
            ops = codec.loads(final["ops"])
            delegate = next(op for op in ops if isinstance(op, Delegate))
            return unsaved(None, delegate.to)
        answer = EngineAnswer(
            context.cache["result"],
            context.cache["saved"],
            None,
            self._ports.count_calls() - calls_before,
            _ms(started),
        )
        await self._reservations.done(turn.key, turn.request_id, answer)
        return answer

    async def _reserve(
        self, conversation_id: UUID | None, version: int | None, account_id: UUID, request_id: str
    ) -> "_Turn | EngineAnswer":
        held = None
        if conversation_id is not None:
            held = await self._ports.store.load(conversation_id, account_id=account_id)
        if held is not None and version is not None:
            sent = TurnKey(account_id, held.conversation_id, version)
            replayed: EngineAnswer | None = await self._reservations.replayed(sent, request_id)
            if replayed is not None:
                return replayed
        if held is not None and version is not None and version != held.version:
            raise StaleDialogueError(held)
        # A new conversation's id is derived from the request, so the same request resumed or
        # replayed lands on the same conversation.
        held = held or DialogueState(uuid5(NAMESPACE_URL, f"finguard:{account_id}:{request_id}"))
        key = TurnKey(account_id, held.conversation_id, held.version)
        reserved = await self._reservations.reserve(key, request_id)
        if reserved.outcome is Reservation.TAKEN:
            raise StaleDialogueError(held)
        if reserved.outcome is Reservation.DONE:
            done: EngineAnswer = reserved.answer
            return done
        return _Turn(key, request_id, held)

    async def _input(self, turn: _Turn, config: Any) -> TurnGraphState | None:
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
                return None
            logger.warning("graph.checkpoint_version_mismatch")
        if values:
            await self._checkpointer.adelete_thread(_thread(turn))
        return {
            "graph_version": GRAPH_VERSION,
            "schema_version": STATE_SCHEMA_VERSION,
            "turn_id": turn.request_id,
            "account_id": str(turn.key.account_id),
            "conversation_id": str(turn.key.conversation_id),
            "expected_version": turn.key.expected_version,
            "model_calls": 0,
            "bank_executions": 0,
            "partial": False,
            "refused": [],
            "next_merchant": None,
        }


def _thread(turn: _Turn) -> str:
    key = turn.key
    return f"{key.account_id}:{key.conversation_id}:{key.expected_version}:{turn.request_id}"


def _ms(started: float) -> dict[str, int]:
    return {"graph": round((time.perf_counter() - started) * 1000)}
