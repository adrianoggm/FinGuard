"""Where the engine keeps each conversation's canonical state, one version at a time.

Saved by compare-and-set: a save names the version it was made against, and is refused when the
store no longer holds that version. A double submit, or two tabs on one conversation, would
otherwise each read version 3 and each write a version 4, and the turn saved second would erase the
first without anybody being told.

**A conversation belongs to the account it was saved under** (ruling R20). Its state holds what the
holder said and which of their accounts they looked at, so under any other account it does not
exist: `load` finds nothing, and a save is compared with nothing of the holder's. Its id is a
reference the client echoes, never a credential.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.errors import DomainError


class StaleDialogueError(DomainError):
    """A turn was made against a version the store no longer holds.

    Carries the state the store does hold for that account, so the caller can answer with it (a
    409 with the current conversation) instead of with nothing.
    """

    code = "stale_dialogue"

    def __init__(self, current: DialogueState) -> None:
        super().__init__(f"conversation {current.conversation_id} is at version {current.version}")
        self.current = current


@dataclass(frozen=True)
class TurnCompletion:
    """Response payload to commit atomically with its canonical state version.

    The payload is encoded by the graph's closed checkpoint codec and stored in the turn
    reservation, never in canonical dialogue state. This keeps ledger rows out of conversation
    checkpoints while making a post-CAS retry replayable.
    """

    request_id: str
    lease_token: UUID
    result_payload: str
    model_calls: int
    financial_reads: int


@runtime_checkable
class DialogueStorePort(Protocol):
    async def load(self, conversation_id: UUID, *, account_id: UUID) -> DialogueState | None:
        """The state last saved under `account_id`, or None for a conversation it never saved."""
        ...

    async def save(
        self,
        state: DialogueState,
        *,
        account_id: UUID,
        expected_version: int,
        completion: TurnCompletion | None = None,
    ) -> DialogueState:
        """`state` saved under `account_id` as version `expected_version + 1`, and returned.

        When `completion` is supplied, its replayable response and state snapshot must be committed
        atomically with this compare-and-set; production graph turns rely on this for crash replay.
        Raises `StaleDialogueError` with the state held under `account_id` when that is not
        `expected_version`. A conversation never saved under it is held at version 0.
        """
        ...
