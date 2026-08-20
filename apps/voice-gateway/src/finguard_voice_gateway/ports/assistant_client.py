from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AssistantReply:
    text: str
    conversation: object
    answer: dict[str, object]
    payment_confirmation_required: bool = False


AssistantStageCallback = Callable[[str, str | None], Awaitable[None]]


class AssistantClientError(RuntimeError):
    """Sanitized assistant failure safe to cross the gateway boundary."""

    def __init__(self, message: str, code: str = "ORCHESTRATOR_ERROR") -> None:
        super().__init__(message)
        self.code = code


class AssistantClient(Protocol):
    async def ask(
        self,
        text: str,
        conversation: object,
        *,
        credential: str,
        idempotency_key: str,
        on_stage: AssistantStageCallback | None = None,
    ) -> AssistantReply: ...
