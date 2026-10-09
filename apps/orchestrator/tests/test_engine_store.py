"""The engine's dialogue store: one versioned state per conversation, saved by compare-and-set.

A save names the version it was made against. The store refuses it when that is no longer the
version it holds, and hands back the one it does hold, so a double submit can never overwrite the
turn that won.
"""

import asyncio
from dataclasses import replace
from uuid import UUID

import pytest

from finguard_orchestrator.domain.engine.state import DialogueState, DialogueTurn
from finguard_orchestrator.domain.ports.dialogue_store import DialogueStorePort, StaleDialogueError
from finguard_orchestrator.infrastructure.engine.memory_dialogue_store import MemoryDialogueStore

ID = UUID(int=1)
FRESH = DialogueState(ID)


def test_the_memory_store_is_a_dialogue_store() -> None:
    assert isinstance(MemoryDialogueStore(), DialogueStorePort)


async def test_a_conversation_never_saved_loads_as_nothing() -> None:
    assert await MemoryDialogueStore().load(ID) is None


async def test_a_save_returns_the_next_version_and_is_what_load_reads() -> None:
    store = MemoryDialogueStore()
    said = FRESH.remembered(DialogueTurn("hola", "un saludo"))

    saved = await store.save(said, expected_version=0)

    assert saved == replace(said, version=1)
    assert await store.load(ID) == saved
    again = await store.save(replace(saved, turns=()), expected_version=1)
    assert again.version == 2
    assert await store.load(ID) == again


async def test_a_save_against_a_version_no_longer_held_is_refused_and_changes_nothing() -> None:
    store = MemoryDialogueStore()
    held = await store.save(FRESH, expected_version=0)

    with pytest.raises(StaleDialogueError) as refused:
        await store.save(FRESH.remembered(DialogueTurn("otra", "otra")), expected_version=0)

    assert refused.value.current == held
    assert await store.load(ID) == held


async def test_a_first_save_against_a_version_never_held_is_refused_with_the_empty_state() -> None:
    store = MemoryDialogueStore()

    with pytest.raises(StaleDialogueError) as refused:
        await store.save(FRESH, expected_version=3)

    assert refused.value.current == FRESH
    assert await store.load(ID) is None


async def test_two_saves_against_one_version_one_wins() -> None:
    store = MemoryDialogueStore()
    await store.save(FRESH, expected_version=0)
    first = FRESH.remembered(DialogueTurn("primera", "a"))
    second = FRESH.remembered(DialogueTurn("segunda", "b"))

    outcomes = await asyncio.gather(
        store.save(first, expected_version=1),
        store.save(second, expected_version=1),
        return_exceptions=True,
    )

    refused = [o for o in outcomes if isinstance(o, StaleDialogueError)]
    won = [o for o in outcomes if isinstance(o, DialogueState)]
    assert len(refused) == 1 and len(won) == 1
    assert won[0].version == 2
    assert refused[0].current == won[0] == await store.load(ID)


async def test_conversations_do_not_share_versions() -> None:
    store = MemoryDialogueStore()
    other = DialogueState(UUID(int=2))

    await store.save(FRESH, expected_version=0)
    saved = await store.save(other, expected_version=0)

    assert saved.version == 1
