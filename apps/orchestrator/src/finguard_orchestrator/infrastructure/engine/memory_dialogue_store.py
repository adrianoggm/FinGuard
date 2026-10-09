"""The prototype's dialogue store: a dictionary in this process.

**Single instance only.** Two replicas would each hold their own conversations, and a restart
forgets every one of them; a client that comes back after a restart is told its version is stale
and handed the empty state. A Postgres adapter in the orchestrator's `assistant` schema is the next
step (spec: "Canonical state").
"""

import asyncio
from dataclasses import replace
from uuid import UUID

from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError


class MemoryDialogueStore:
    """Implements DialogueStorePort."""

    def __init__(self) -> None:
        self._states: dict[UUID, DialogueState] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def load(self, conversation_id: UUID) -> DialogueState | None:
        return self._states.get(conversation_id)

    async def save(self, state: DialogueState, *, expected_version: int) -> DialogueState:
        # One lock per conversation, so the check and the write are one step. Nothing between them
        # awaits today, which already makes them atomic on one event loop; the lock is what keeps
        # them so when something there does.
        lock = self._locks.setdefault(state.conversation_id, asyncio.Lock())
        async with lock:
            held = self._states.get(state.conversation_id, DialogueState(state.conversation_id))
            if held.version != expected_version:
                raise StaleDialogueError(held)
            saved = replace(state, version=expected_version + 1)
            self._states[state.conversation_id] = saved
            return saved
