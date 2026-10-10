"""Application boundary for one conversational turn.

The HTTP routes depend on this contract rather than either concrete coordinator. That keeps the
legacy engine replaceable while LangGraph becomes the sole conversational orchestrator.
"""

from datetime import date
from typing import Protocol
from uuid import UUID

from finguard_orchestrator.application.engine.turn_result import EngineAnswer


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
    ) -> EngineAnswer: ...
