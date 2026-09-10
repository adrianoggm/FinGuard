from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AssistantReply:
    text: str
    conversation: object
    answer: dict[str, object]
    payment_confirmation_required: bool = False
    # What a voice client should say instead of `text` -- set only on a VOICE turn that drew a
    # chart (Task 3, Wave 4.4). `None` on every older orchestrator and on any turn without one.
    spoken: str | None = None


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
