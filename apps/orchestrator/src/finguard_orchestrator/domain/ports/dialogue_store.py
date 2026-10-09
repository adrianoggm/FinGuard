"""Where the engine keeps each conversation's canonical state, one version at a time.

Saved by compare-and-set: a save names the version it was made against, and is refused when the
store no longer holds that version. A double submit, or two tabs on one conversation, would
otherwise each read version 3 and each write a version 4, and the turn saved second would erase the
first without anybody being told.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.errors import DomainError


class StaleDialogueError(DomainError):
    """A turn was made against a version the store no longer holds.

    Carries the state the store does hold, so the caller can answer with it (a 409 with the
    current conversation) instead of with nothing.
    """

    code = "stale_dialogue"

    def __init__(self, current: DialogueState) -> None:
        super().__init__(f"conversation {current.conversation_id} is at version {current.version}")
        self.current = current


@runtime_checkable
class DialogueStorePort(Protocol):
    async def load(self, conversation_id: UUID) -> DialogueState | None:
        """The state last saved, or None for a conversation never saved."""
        ...

    async def save(self, state: DialogueState, *, expected_version: int) -> DialogueState:
        """`state` saved as version `expected_version + 1`, and returned as saved.

        Raises `StaleDialogueError` with the state held when that is not `expected_version`. A
        conversation never saved is held at version 0.
        """
        ...
