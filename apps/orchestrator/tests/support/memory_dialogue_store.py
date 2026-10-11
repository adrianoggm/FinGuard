"""The prototype's dialogue store: a dictionary in this process.

**Single instance only.** Two replicas would each hold their own conversations, and a restart
forgets every one of them; a client that comes back after a restart is told its version is stale
and handed an empty state. A Postgres adapter in the orchestrator's `assistant` schema is the next
step (spec: "Canonical state").

Keyed by account and conversation (ruling R20): the same id under another account is another
entry, which is how someone else's conversation stays unknown.
"""

import asyncio
from dataclasses import replace
from uuid import UUID

from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.ports.dialogue_store import (
    DialogueStorePort,
    StaleDialogueError,
    TurnCompletion,
)

type _Key = tuple[UUID, UUID]


class MemoryDialogueStore(DialogueStorePort):
    """Implements DialogueStorePort."""

    def __init__(self) -> None:
        self._states: dict[_Key, DialogueState] = {}
        self._locks: dict[_Key, asyncio.Lock] = {}

    async def load(self, conversation_id: UUID, *, account_id: UUID) -> DialogueState | None:
        return self._states.get((account_id, conversation_id))

    async def save(
        self,
        state: DialogueState,
        *,
        account_id: UUID,
        expected_version: int,
        completion: TurnCompletion | None = None,
    ) -> DialogueState:
        # This adapter is only for explicitly non-durable local/test runs. Production graph mode
        # uses PostgresDialogueStore, which atomically commits `completion` with the state CAS.
        key = (account_id, state.conversation_id)
        # One lock per conversation, so the check and the write are one step. Nothing between them
        # awaits today, which already makes them atomic on one event loop; the lock is what keeps
        # them so when something there does.
        async with self._locks.setdefault(key, asyncio.Lock()):
            held = self._states.get(key, DialogueState(state.conversation_id))
            if held.version != expected_version:
                raise StaleDialogueError(held)
            saved = replace(state, version=expected_version + 1)
            self._states[key] = saved
            return saved
