"""PostgreSQL adapter for assistant.saved_metric.

Every operation sets the holder inside its transaction; callers never pass a holder id obtained
from a request body.  The advisory lock serialises the position/limit decisions per holder.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg

from finguard_orchestrator.domain.models.saved_metric import SavedMetric, SavedMetricDefinition
from finguard_orchestrator.domain.ports.saved_metric_repository import (
    IdempotencyConflictError,
    SavedMetricRepository,
    VersionConflictError,
)
from finguard_orchestrator.infrastructure.metrics.codec import (
    decode_definition,
    encode_definition,
)


def _columns(definition: SavedMetricDefinition) -> tuple[str, str]:
    """The two JSON columns a definition becomes: `plan_json` and `encoding_json`.

    `chart` is *absent* from the encoding when the metric is a figure rather than a chart, so it is
    read with `.get`: subscripting it raised `KeyError` and turned every save of a lone figure into
    a 500. `json.dumps(None)` is `"null"` -- a JSON null, which the column accepts, and not the SQL
    null it forbids.

    A function rather than two copies of the same two lines, so the encoding has one place to be
    wrong and one place to be tested without a database.
    """
    encoded = encode_definition(definition)
    return (
        json.dumps(encoded["plan"], separators=(",", ":")),
        json.dumps(encoded.get("chart"), separators=(",", ":")),
    )


class PostgresSavedMetricRepository(SavedMetricRepository):
    def __init__(self, pool: asyncpg.Pool, maximum: int = 50) -> None:
        self.pool = pool
        self.maximum = maximum

    async def _holder(self, connection: asyncpg.Connection[Any], holder_id: str) -> None:
        await connection.execute("SELECT set_config('app.holder_id', $1, true)", holder_id)

    async def list(self, holder_id: str) -> list[SavedMetric]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await self._holder(connection, holder_id)
                rows = await connection.fetch(
                    "SELECT * FROM assistant.saved_metric ORDER BY position, created_at, id"
                )
        return [_row(row) for row in rows]

    async def get(self, holder_id: str, metric_id: UUID) -> SavedMetric | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await self._holder(connection, holder_id)
                row = await connection.fetchrow(
                    "SELECT * FROM assistant.saved_metric WHERE id=$1", metric_id
                )
        return None if row is None else _row(row)

    async def create(
        self, metric: SavedMetric, *, idempotency_key: str, request_hash: str
    ) -> SavedMetric:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await self._holder(connection, metric.holder_id)
                await connection.fetchval(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", metric.holder_id
                )
                old = await connection.fetchrow(
                    "SELECT * FROM assistant.saved_metric "
                    "WHERE holder_id=$1 AND idempotency_key=$2",
                    metric.holder_id,
                    idempotency_key,
                )
                if old is not None:
                    if old["request_hash"].strip() != request_hash:
                        raise IdempotencyConflictError(
                            "idempotency key was reused with another payload"
                        )
                    return _row(old)
                count = await connection.fetchval(
                    "SELECT count(*) FROM assistant.saved_metric WHERE holder_id=$1",
                    metric.holder_id,
                )
                if int(count) >= self.maximum:
                    raise ValueError("saved metric limit reached")
                position = int(count)
                plan_json, encoding_json = _columns(metric.definition)
                row = await connection.fetchrow(
                    "INSERT INTO assistant.saved_metric "
                    "(id,holder_id,title,plan_json,encoding_json,position,version,"
                    "idempotency_key,request_hash) "
                    "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,1,$7,$8) RETURNING *",
                    metric.id,
                    metric.holder_id,
                    metric.title,
                    plan_json,
                    encoding_json,
                    position,
                    idempotency_key,
                    request_hash,
                )
        assert row is not None
        return _row(row)

    async def update(self, metric: SavedMetric, *, expected_version: int) -> SavedMetric:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await self._holder(connection, metric.holder_id)
                row = await connection.fetchrow(
                    "UPDATE assistant.saved_metric SET title=$1, encoding_json=$2::jsonb, "
                    "version=version+1, updated_at=now() "
                    "WHERE id=$3 AND version=$4 RETURNING *",
                    metric.title,
                    _columns(metric.definition)[1],
                    metric.id,
                    expected_version,
                )
        if row is None:
            raise VersionConflictError("stale metric version")
        return _row(row)

    async def reorder(self, holder_id: str, metric_ids: Sequence[UUID]) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await self._holder(connection, holder_id)
                await connection.fetchval(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", holder_id
                )
                found = await connection.fetchval(
                    "SELECT count(*) FROM assistant.saved_metric "
                    "WHERE holder_id=$1 AND id=ANY($2::uuid[])",
                    holder_id,
                    metric_ids,
                )
                total = await connection.fetchval(
                    "SELECT count(*) FROM assistant.saved_metric WHERE holder_id=$1", holder_id
                )
                if (
                    found != total
                    or len(metric_ids) != int(total)
                    or len(set(metric_ids)) != len(metric_ids)
                ):
                    raise ValueError("order must contain exactly the holder's metrics")
                for position, metric_id in enumerate(metric_ids):
                    await connection.execute(
                        "UPDATE assistant.saved_metric SET position=$1, version=version+1, "
                        "updated_at=now() WHERE holder_id=$2 AND id=$3",
                        position,
                        holder_id,
                        metric_id,
                    )

    async def delete(self, holder_id: str, metric_id: UUID) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await self._holder(connection, holder_id)
                await connection.execute(
                    "DELETE FROM assistant.saved_metric WHERE id=$1", metric_id
                )


def _row(row: asyncpg.Record) -> SavedMetric:
    definition = {
        "version": 1,
        "plan": _json_value(row["plan_json"]),
        "chart": _json_value(row["encoding_json"]),
    }
    return SavedMetric(
        holder_id=row["holder_id"],
        title=row["title"],
        definition=decode_definition(definition),
        position=row["position"],
        version=row["version"],
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _json_value(value: object) -> object:
    """asyncpg returns json/jsonb as text unless a connection codec is installed."""
    return json.loads(value) if isinstance(value, str) else value
