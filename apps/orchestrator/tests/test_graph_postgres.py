"""PostgreSQL contracts for graph state and durable request reservations."""

import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest

from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError
from finguard_orchestrator.domain.ports.turn_reservations import Reservation, TurnKey
from finguard_orchestrator.infrastructure.engine.postgres_dialogue_store import (
    PostgresDialogueStore,
)
from finguard_orchestrator.infrastructure.graph.postgres_turn_reservations import (
    PostgresTurnReservations,
)


@pytest.mark.integration
async def test_postgres_state_cas_and_turn_resume_replay() -> None:
    url = os.getenv("ORCHESTRATOR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ORCHESTRATOR_TEST_DATABASE_URL is not configured")
    url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    pool = await asyncpg.create_pool(url)
    account, other_account, conversation_id = uuid4(), uuid4(), uuid4()
    store = PostgresDialogueStore(pool)
    reservations = PostgresTurnReservations(pool)
    try:
        saved = await store.save(
            DialogueState(conversation_id), account_id=account, expected_version=0
        )
        assert saved.version == 1
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.account_id', $1, true)", str(account)
                )
                await connection.execute(
                    "UPDATE assistant.conversation_state SET schema_version = 5 "
                    "WHERE account_id = $1 AND conversation_id = $2",
                    account,
                    conversation_id,
                )
        assert await store.load(conversation_id, account_id=account) == saved
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.account_id', $1, true)", str(account)
                )
                await connection.execute(
                    "UPDATE assistant.conversation_state SET schema_version = 6 "
                    "WHERE account_id = $1 AND conversation_id = $2",
                    account,
                    conversation_id,
                )
        assert await store.load(conversation_id, account_id=account) == saved
        assert await store.load(conversation_id, account_id=other_account) is None

        async def save_race() -> str:
            try:
                result = await store.save(
                    DialogueState(conversation_id), account_id=account, expected_version=1
                )
                return f"saved:{result.version}"
            except StaleDialogueError:
                return "stale"

        assert sorted(await asyncio.gather(save_race(), save_race())) == ["saved:2", "stale"]
        current = await store.load(conversation_id, account_id=account)
        assert current is not None and current.version == 2
        key = TurnKey(account, conversation_id, current.version)

        reserved = await reservations.reserve(key, "retry-1", "a" * 64)
        assert reserved.outcome is Reservation.NEW and reserved.lease_token is not None
        await reservations.failed(key, "retry-1", reserved.lease_token)
        resumed = await reservations.reserve(key, "retry-1", "a" * 64)
        assert resumed.outcome is Reservation.RESUME and resumed.lease_token is not None
        answer = EngineAnswer(None, current, "UNSUPPORTED", 1, {"graph": 5})
        await reservations.done(key, "retry-1", resumed.lease_token, answer)
        replay = await reservations.reserve(key, "retry-1", "a" * 64)
        assert replay.outcome is Reservation.DONE and replay.answer == answer
        assert (await reservations.reserve(key, "retry-1", "b" * 64)).outcome is Reservation.REUSED
    finally:
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.account_id', $1, true)", str(account)
                )
                await connection.execute(
                    "DELETE FROM assistant.conversation_turn "
                    "WHERE account_id = $1 AND conversation_id = $2",
                    account,
                    conversation_id,
                )
                await connection.execute(
                    "DELETE FROM assistant.conversation_state "
                    "WHERE account_id = $1 AND conversation_id = $2",
                    account,
                    conversation_id,
                )
        await pool.close()
