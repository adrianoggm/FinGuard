"""Postgres-backed canonical conversation state with account scope and compare-and-set writes."""

from dataclasses import replace
from typing import Any
from uuid import UUID

from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError


class PostgresDialogueStore:
    """Implements `DialogueStorePort` using a shared asyncpg pool.

    The account is part of the key and enforced again by row-level security. Saves acquire a
    transaction advisory lock before reading, including the first insert where `FOR UPDATE` has
    no row to lock. The version check and write therefore form one compare-and-set across workers.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def load(self, conversation_id: UUID, *, account_id: UUID) -> DialogueState | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, account_id)
                row = await connection.fetchrow(
                    """
                    SELECT state_version, schema_version, payload::text AS payload
                    FROM assistant.conversation_state
                    WHERE account_id = $1 AND conversation_id = $2
                    """,
                    account_id,
                    conversation_id,
                )
        if row is None:
            return None
        schema = int(row["schema_version"])
        if schema not in {4, 5, 6, codec.STATE_SCHEMA_VERSION}:
            raise ValueError(f"unsupported conversation schema version: {schema}")
        state = codec.loads_state(row["payload"])
        if state.version != int(row["state_version"]):
            raise ValueError("conversation state version does not match its row version")
        return state

    async def save(
        self, state: DialogueState, *, account_id: UUID, expected_version: int
    ) -> DialogueState:
        payload = codec.dumps_state(state)
        if len(payload.encode("utf-8")) > 131_072:
            raise ValueError("conversation state exceeds 128 KiB")
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, account_id)
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1 || ':' || $2, 0))",
                    str(account_id),
                    str(state.conversation_id),
                )
                row = await connection.fetchrow(
                    """
                    SELECT state_version, schema_version, payload::text AS payload
                    FROM assistant.conversation_state
                    WHERE account_id = $1 AND conversation_id = $2
                    FOR UPDATE
                    """,
                    account_id,
                    state.conversation_id,
                )
                if row is None:
                    if expected_version != 0:
                        raise StaleDialogueError(DialogueState(state.conversation_id))
                    saved = replace(state, version=1)
                    await connection.execute(
                        """
                        INSERT INTO assistant.conversation_state
                          (account_id, conversation_id, state_version, schema_version, payload)
                        VALUES ($1, $2, 1, $3, $4::jsonb)
                        """,
                        account_id,
                        state.conversation_id,
                        codec.STATE_SCHEMA_VERSION,
                        codec.dumps_state(saved),
                    )
                    return saved

                schema = int(row["schema_version"])
                if schema not in {4, 5, 6, codec.STATE_SCHEMA_VERSION}:
                    raise ValueError(f"unsupported conversation schema version: {schema}")
                held = codec.loads_state(row["payload"])
                if held.version != int(row["state_version"]):
                    raise ValueError("conversation state version does not match its row version")
                if held.version != expected_version:
                    raise StaleDialogueError(held)
                saved = replace(state, version=expected_version + 1)
                await connection.execute(
                    """
                    UPDATE assistant.conversation_state
                    SET state_version = $3, schema_version = $4, payload = $5::jsonb,
                        updated_at = now()
                    WHERE account_id = $1 AND conversation_id = $2
                    """,
                    account_id,
                    state.conversation_id,
                    saved.version,
                    codec.STATE_SCHEMA_VERSION,
                    codec.dumps_state(saved),
                )
                return saved


async def _scope(connection: Any, account_id: UUID) -> None:
    await connection.execute("SELECT set_config('app.account_id', $1, true)", str(account_id))
