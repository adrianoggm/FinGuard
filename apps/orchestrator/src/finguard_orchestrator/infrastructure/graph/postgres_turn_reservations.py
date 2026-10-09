"""Durable ownership, retry and replay records for graph turns."""

from typing import Any
from uuid import UUID

from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.application.graph import codec
from finguard_orchestrator.domain.ports.turn_reservations import (
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
                    await connection.execute(
                        """
                        INSERT INTO assistant.conversation_turn
                          (account_id, conversation_id, expected_version, request_id,
                           request_fingerprint, status)
                        VALUES ($1, $2, $3, $4, $5, 'NEW')
                        """,
                        key.account_id,
                        key.conversation_id,
                        key.expected_version,
                        request_id,
                        fingerprint,
                    )
                    return Reserved(Reservation.NEW)

                held_request = row["request_id"]
                held_fingerprint = row["request_fingerprint"].strip()
                status = Reservation(row["status"])
                if held_request != request_id:
                    if status is Reservation.RESUME:
                        await connection.execute(
                            """
                            UPDATE assistant.conversation_turn
                            SET request_id = $4, request_fingerprint = $5, status = 'NEW',
                                answer_schema_version = NULL, answer_payload = NULL,
                                updated_at = now()
                            WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                            """,
                            key.account_id,
                            key.conversation_id,
                            key.expected_version,
                            request_id,
                            fingerprint,
                        )
                        return Reserved(Reservation.NEW)
                    return Reserved(Reservation.TAKEN)
                if held_fingerprint != fingerprint:
                    return Reserved(Reservation.REUSED)
                if status is Reservation.DONE:
                    return Reserved(Reservation.DONE, _answer(row))
                if status is Reservation.RESUME:
                    await connection.execute(
                        """
                        UPDATE assistant.conversation_turn SET status = 'NEW', updated_at = now()
                        WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                        """,
                        key.account_id,
                        key.conversation_id,
                        key.expected_version,
                    )
                    return Reserved(Reservation.RESUME)
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
        if Reservation(row["status"]) is Reservation.DONE:
            return Reserved(Reservation.DONE, _answer(row))
        return None

    async def failed(self, key: TurnKey, request_id: str) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await _scope(connection, key.account_id)
                await connection.execute(
                    """
                    UPDATE assistant.conversation_turn
                    SET status = 'RESUME', updated_at = now()
                    WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                      AND request_id = $4 AND status = 'NEW'
                    """,
                    key.account_id,
                    key.conversation_id,
                    key.expected_version,
                    request_id,
                )

    async def done(self, key: TurnKey, request_id: str, answer: EngineAnswer) -> None:
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
                        updated_at = now()
                    WHERE account_id = $1 AND conversation_id = $2 AND expected_version = $3
                      AND request_id = $4 AND status = 'NEW'
                    """,
                    key.account_id,
                    key.conversation_id,
                    key.expected_version,
                    request_id,
                    codec.ANSWER_SCHEMA_VERSION,
                    payload,
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
               answer_payload::text AS answer_payload
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
