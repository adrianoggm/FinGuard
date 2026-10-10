"""Postgres-backed canonical conversation state with account scope and compare-and-set writes."""

from dataclasses import replace
from typing import Any
from uuid import UUID

from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.ports.dialogue_store import StaleDialogueError, TurnCompletion

# Persisted rows start at schema 4. The codec migrates every schema from there through the
# current version, including 10–12; the store must not reject versions the decoder can resume.
_SUPPORTED_CONVERSATION_SCHEMAS = frozenset(range(4, codec.STATE_SCHEMA_VERSION + 1))


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
        if schema not in _SUPPORTED_CONVERSATION_SCHEMAS:
            raise ValueError(f"unsupported conversation schema version: {schema}")
        state = codec.loads_state(row["payload"])
        if state.version != int(row["state_version"]):
            raise ValueError("conversation state version does not match its row version")
        return state

    async def save(
        self,
        state: DialogueState,
        *,
        account_id: UUID,
        expected_version: int,
        completion: TurnCompletion | None = None,
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
                    if completion is not None:
                        await _commit_turn_result(
                            connection,
                            saved,
                            account_id=account_id,
                            expected_version=expected_version,
                            completion=completion,
                        )
                    return saved

                schema = int(row["schema_version"])
                if schema not in _SUPPORTED_CONVERSATION_SCHEMAS:
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
                if completion is not None:
                    await _commit_turn_result(
                        connection,
                        saved,
                        account_id=account_id,
                        expected_version=expected_version,
                        completion=completion,
                    )
                return saved


async def _scope(connection: Any, account_id: UUID) -> None:
    await connection.execute("SELECT set_config('app.account_id', $1, true)", str(account_id))


async def _commit_turn_result(
    connection: Any,
    state: DialogueState,
    *,
    account_id: UUID,
    expected_version: int,
    completion: TurnCompletion,
) -> None:
    """Store a recoverable answer in the same transaction as the state compare-and-set."""
    state_payload = codec.dumps_state(state)
    if (
        not completion.request_id
        or len(completion.result_payload.encode("utf-8")) > 524_288
        or len(state_payload.encode("utf-8")) > 131_072
    ):
        raise ValueError("invalid or oversized committed turn result")
    if completion.model_calls < 0:
        raise ValueError("committed turn model call count cannot be negative")
    if completion.financial_reads < 0:
        raise ValueError("committed financial read count cannot be negative")
    updated = await connection.execute(
        """
        UPDATE assistant.conversation_turn
        SET committed_result_payload = $6, committed_state_payload = $7,
            committed_model_calls = $8, committed_financial_reads = $9, updated_at = now()
        WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
          AND request_id = $4 AND status = 'NEW' AND lease_token = $5
          AND committed_result_payload IS NULL
        """,
        account_id,
        state.conversation_id,
        expected_version,
        completion.request_id,
        completion.lease_token,
        completion.result_payload,
        state_payload,
        completion.model_calls,
        completion.financial_reads,
    )
    if updated != "UPDATE 1":
        raise RuntimeError("turn reservation was not owned while committing canonical state")
