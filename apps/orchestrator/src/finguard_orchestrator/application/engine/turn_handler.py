"""Application boundary for one conversational turn.

The HTTP routes depend on this contract, which the LangGraph runtime implements, so the routes
can be exercised against a scripted turn handler.
"""

from datetime import date
from typing import Protocol
from uuid import UUID

from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.domain.models.conversation import Workflow


class ConversationTurnHandler(Protocol):
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
    ) -> EngineAnswer: ...

    async def owns_workflow(
        self, conversation_id: UUID, *, account_id: UUID, workflow: Workflow
    ) -> bool: ...
