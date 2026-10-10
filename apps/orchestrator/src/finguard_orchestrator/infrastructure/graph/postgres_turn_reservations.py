"""Durable ownership, retry and replay records for graph turns."""

from typing import Any
from uuid import UUID, uuid4

from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.domain.ports.turn_reservations import (
    AbandonedTurn,
    CommittedTurnResult,
    Reservation,
    Reserved,
    TurnKey,
)


class PostgresTurnReservations:
    """Implements `TurnReservationsPort` using transaction-scoped PostgreSQL locks."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def reserve(self, key: TurnKey, request_id: str, fingerprint: str) -> Reserved:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, key.account_id)
                await _lock(connection, key)
                row = await _find(connection, key, lock=True)
                if row is None:
                    lease_token = uuid4()
                    await connection.execute(
                        """
                        INSERT INTO assistant.conversation_turn
                          (account_id, conversation_id, expected_version, request_id,
                           request_fingerprint, status, lease_until, lease_token)
                        VALUES ($1, $2, $3, $4, $5, 'NEW', now() + interval '5 minutes', $6)
                        """,
                        key.account_id,
                        key.conversation_id,
                        key.expected_version,
                        request_id,
                        fingerprint,
                        lease_token,
                    )
                    return Reserved(Reservation.NEW, lease_token=lease_token)

                held_request = row["request_id"]
                held_fingerprint = row["request_fingerprint"].strip()
                status = Reservation(row["status"])
                if held_request != request_id:
                    if status in (Reservation.RESUME, Reservation.ABANDONED) or (
                        status is Reservation.NEW and row["lease_expired"]
                    ):
                        lease_token = uuid4()
                        await connection.execute(
                            """
                            UPDATE assistant.conversation_turn
                            SET request_id = $4, request_fingerprint = $5, status = 'NEW',
                                answer_schema_version = NULL, answer_payload = NULL,
                                checkpoint_deleted_at = NULL,
                                lease_until = now() + interval '5 minutes',
                                lease_token = $6,
                                updated_at = now()
                            WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                            """,
                            key.account_id,
                            key.conversation_id,
                            key.expected_version,
                            request_id,
                            fingerprint,
                            lease_token,
                        )
                        return Reserved(
                            Reservation.NEW,
                            lease_token=lease_token,
                            previous_request_id=(
                                held_request
                                if status is not Reservation.ABANDONED
                                or row["checkpoint_deleted_at"] is None
                                else None
                            ),
                        )
                    return Reserved(Reservation.TAKEN)
                if held_fingerprint != fingerprint:
                    return Reserved(Reservation.REUSED)
                if status is Reservation.ABANDONED:
                    return Reserved(Reservation.EXPIRED)
                if status is Reservation.DONE:
                    return Reserved(Reservation.DONE, _answer(row))
                if status is Reservation.RESUME:
                    lease_token = uuid4()
                    await connection.execute(
                        """
                        UPDATE assistant.conversation_turn
                        SET status = 'NEW', lease_until = now() + interval '5 minutes',
                            lease_token = $4, updated_at = now()
                        WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                          AND request_id = $5
                        """,
                        key.account_id,
                        key.conversation_id,
                        key.expected_version,
                        lease_token,
                        request_id,
                    )
                    return Reserved(Reservation.RESUME, lease_token=lease_token)
                if status is Reservation.NEW and row["lease_expired"]:
                    lease_token = uuid4()
                    await connection.execute(
                        """
                        UPDATE assistant.conversation_turn
                        SET lease_until = now() + interval '5 minutes', lease_token = $5,
                            updated_at = now()
                        WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                          AND request_id = $4 AND status = 'NEW'
                        """,
                        key.account_id,
                        key.conversation_id,
                        key.expected_version,
                        request_id,
                        lease_token,
                    )
                    return Reserved(Reservation.RESUME, lease_token=lease_token)
                return Reserved(Reservation.TAKEN)

    async def replayed(self, key: TurnKey, request_id: str, fingerprint: str) -> Reserved | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, key.account_id)
                row = await _find(connection, key, lock=False)
        if row is None or row["request_id"] != request_id:
            return None
        if row["request_fingerprint"].strip() != fingerprint:
            return Reserved(Reservation.REUSED)
        if row["committed_result_payload"] is not None:
            return Reserved(
                Reservation.COMMITTED,
                CommittedTurnResult(
                    codec.loads_typed(row["committed_result_payload"]),
                    codec.loads_state(row["committed_state_payload"]),
                    int(row["committed_model_calls"]),
                    (
                        int(row["committed_financial_reads"])
                        if row["committed_financial_reads"] is not None
                        else None
                    ),
                ),
            )
        if Reservation(row["status"]) is Reservation.DONE:
            return Reserved(Reservation.DONE, _answer(row))
        return None

    async def failed(self, key: TurnKey, request_id: str, lease_token: UUID) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, key.account_id)
                await connection.execute(
                    """
                    UPDATE assistant.conversation_turn
                    SET status = 'RESUME', lease_until = NULL, lease_token = NULL,
                        updated_at = now()
                    WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                      AND request_id = $4 AND status = 'NEW' AND lease_token = $5
                    """,
                    key.account_id,
                    key.conversation_id,
                    key.expected_version,
                    request_id,
                    lease_token,
                )

    async def heartbeat(self, key: TurnKey, request_id: str, lease_token: UUID) -> bool:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, key.account_id)
                result = await connection.execute(
                    """
                    UPDATE assistant.conversation_turn
                    SET lease_until = now() + interval '5 minutes', updated_at = now()
                    WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                      AND request_id = $4 AND status = 'NEW'
                      AND lease_until > now() AND lease_token = $5
                    """,
                    key.account_id,
                    key.conversation_id,
                    key.expected_version,
                    request_id,
                    lease_token,
                )
                return result == "UPDATE 1"

    async def abandon_expired_checkpoints(
        self, key: TurnKey, *, retention_seconds: int, limit: int = 20
    ) -> tuple[AbandonedTurn, ...]:
        """Fence old resumable work before its potentially bank-sensitive checkpoint is deleted.

        Runs per conversation under the holder's existing RLS scope. NEW turns need both an
        expired lease and an old update timestamp; RESUME turns need an old failure timestamp.
        ABANDONED rows with no deletion mark are retried after a process interruption.
        """
        if retention_seconds <= 0 or not 1 <= limit <= 100:
            raise ValueError("invalid abandoned checkpoint retention bounds")
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, key.account_id)
                rows = await connection.fetch(
                    """
                    SELECT expected_version, request_id, status
                    FROM assistant.conversation_turn
                    WHERE account_id = $1 AND conversation_id = $2
                      AND (
                        (status = 'RESUME' AND updated_at <= now() - ($4 * interval '1 second'))
                        OR (status = 'NEW' AND lease_until <= now()
                            AND updated_at <= now() - ($4 * interval '1 second'))
                        OR (status = 'ABANDONED' AND checkpoint_deleted_at IS NULL)
                      )
                    ORDER BY (expected_version = $3) DESC, updated_at
                    LIMIT $5
                    FOR UPDATE SKIP LOCKED
                    """,
                    key.account_id,
                    key.conversation_id,
                    key.expected_version,
                    retention_seconds,
                    limit,
                )
                for row in rows:
                    if row["status"] != Reservation.ABANDONED.value:
                        await connection.execute(
                            """
                            UPDATE assistant.conversation_turn
                            SET status = 'ABANDONED', lease_until = NULL, lease_token = NULL,
                                updated_at = now()
                            WHERE account_id = $1 AND conversation_id = $2
                              AND expected_version = $3 AND request_id = $4
                            """,
                            key.account_id,
                            key.conversation_id,
                            row["expected_version"],
                            row["request_id"],
                        )
        return tuple(AbandonedTurn(int(row["expected_version"]), row["request_id"]) for row in rows)

    async def checkpoint_deleted(self, key: TurnKey, request_id: str) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, key.account_id)
                await connection.execute(
                    """
                    UPDATE assistant.conversation_turn
                    SET checkpoint_deleted_at = now()
                    WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                      AND request_id = $4 AND status = 'ABANDONED'
                      AND checkpoint_deleted_at IS NULL
                    """,
                    key.account_id,
                    key.conversation_id,
                    key.expected_version,
                    request_id,
                )

    async def done(
        self, key: TurnKey, request_id: str, lease_token: UUID, answer: EngineAnswer
    ) -> None:
        payload = codec.dumps_answer(answer)
        if len(payload.encode("utf-8")) > 524_288:
            raise ValueError("turn answer exceeds 512 KiB")
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, key.account_id)
                await _lock(connection, key)
                result = await connection.execute(
                    """
                    UPDATE assistant.conversation_turn
                    SET status = 'DONE', answer_schema_version = $5, answer_payload = $6::jsonb,
                        committed_result_payload = NULL, committed_state_payload = NULL,
                        committed_model_calls = NULL, committed_financial_reads = NULL,
                        lease_until = NULL, lease_token = NULL, updated_at = now()
                    WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                      AND request_id = $4 AND status = 'NEW' AND lease_token = $7
                      AND committed_result_payload IS NOT NULL
                    """,
                    key.account_id,
                    key.conversation_id,
                    key.expected_version,
                    request_id,
                    codec.ANSWER_SCHEMA_VERSION,
                    payload,
                    lease_token,
                )
                if result != "UPDATE 1":
                    raise RuntimeError("turn reservation was not owned while closing")


async def _scope(connection: Any, account_id: UUID) -> None:
    await connection.execute("SELECT set_config('app.account_id', $1, true)", str(account_id))


async def _lock(connection: Any, key: TurnKey) -> None:
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1 || ':' || $2 || ':' || $3, 0))",
        str(key.account_id),
        str(key.conversation_id),
        str(key.expected_version),
    )


async def _find(connection: Any, key: TurnKey, *, lock: bool) -> Any:
    suffix = " FOR UPDATE" if lock else ""
    return await connection.fetchrow(
        """
        SELECT request_id, request_fingerprint, status, answer_schema_version,
               answer_payload::text AS answer_payload,
               committed_result_payload, committed_state_payload, committed_model_calls,
               committed_financial_reads,
               lease_token,
               checkpoint_deleted_at,
               (lease_until IS NULL OR lease_until <= now()) AS lease_expired
        FROM assistant.conversation_turn
        WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
        """
        + suffix,
        key.account_id,
        key.conversation_id,
        key.expected_version,
    )


def _answer(row: Any) -> EngineAnswer:
    schema = int(row["answer_schema_version"])
    if schema != codec.ANSWER_SCHEMA_VERSION:
        raise ValueError(f"unsupported turn answer schema version: {schema}")
    return codec.loads_answer(row["answer_payload"])
