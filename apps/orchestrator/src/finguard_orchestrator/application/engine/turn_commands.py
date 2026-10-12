"""A conversational turn through the buses, like every other use case.

A turn changes the conversation's canonical state, so answering a sentence and running a form's
action are commands; whether the graph owns a workflow a client echoed is a query. The routes
dispatch them and never hold the graph.

The graph is built when the application starts (it needs the PostgreSQL pool), after the buses are,
so each handler is given a way to reach it rather than the graph itself, and says the runtime is
unavailable until it exists.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from finguard_orchestrator.application.bus.command_bus import Command
from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.application.engine.turn_handler import ConversationTurnHandler
from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.domain.errors import GraphUnavailableError
from finguard_orchestrator.domain.models.conversation import Workflow

GraphProvider = Callable[[], ConversationTurnHandler | None]


@dataclass(frozen=True)
class Turn:
    """Who is asking, about which conversation, on which day, under which request id."""

    conversation_id: UUID | None
    version: int | None
    account_id: UUID
    today: date
    reporting_timezone: str
    request_id: str | None = None
    caller_scopes: frozenset[str] = field(default_factory=frozenset)
    # Said rather than typed. A spoken yes never confirms a payment: a misheard word must not move
    # money, so the confirmation is the button on screen (ADR-010 §5).
    spoken: bool = False


@dataclass(frozen=True)
class AnswerTurnCommand(Command[EngineAnswer]):
    sentence: str
    turn: Turn


@dataclass(frozen=True)
class OwnsWorkflowQuery(Query[bool]):
    conversation_id: UUID
    account_id: UUID
    workflow: Workflow


def _graph(provider: GraphProvider) -> ConversationTurnHandler:
    graph = provider()
    if graph is None:
        raise GraphUnavailableError("the conversation graph is not available")
    return graph


class AnswerTurnHandler:
    def __init__(self, graph: GraphProvider) -> None:
        self._graph = graph

    async def handle(self, command: AnswerTurnCommand) -> EngineAnswer:
        turn = command.turn
        return await _graph(self._graph).answer(
            command.sentence,
            conversation_id=turn.conversation_id,
            version=turn.version,
            account_id=turn.account_id,
            today=turn.today,
            reporting_timezone=turn.reporting_timezone,
            request_id=turn.request_id,
            caller_scopes=turn.caller_scopes,
            spoken=turn.spoken,
        )


class OwnsWorkflowHandler:
    def __init__(self, graph: GraphProvider) -> None:
        self._graph = graph

    async def handle(self, query: OwnsWorkflowQuery) -> bool:
        return await _graph(self._graph).owns_workflow(
            query.conversation_id, account_id=query.account_id, workflow=query.workflow
        )
