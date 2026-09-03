"""Deterministic repository used by the demo and unit tests.

Production deployments replace this with the asyncpg adapter; keeping the port here means the HTTP
surface can be exercised without a database and never falls back to banking tables.
"""

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from finguard_orchestrator.domain.models.saved_metric import SavedMetric
from finguard_orchestrator.domain.ports.saved_metric_repository import (
    IdempotencyConflictError,
    VersionConflictError,
)


class MemorySavedMetricRepository:
    def __init__(self, maximum: int = 50) -> None:
        self._rows: dict[UUID, SavedMetric] = {}
        self._keys: dict[tuple[str, str], tuple[str, UUID]] = {}
        self._maximum = maximum

    async def list(self, holder_id: str) -> list[SavedMetric]:
        return sorted(
            (row for row in self._rows.values() if row.holder_id == holder_id),
            key=lambda row: (row.position, row.created_at or 0, str(row.id)),
        )

    async def get(self, holder_id: str, metric_id: UUID) -> SavedMetric | None:
        row = self._rows.get(metric_id)
        return row if row is not None and row.holder_id == holder_id else None

    async def create(
        self, metric: SavedMetric, *, idempotency_key: str, request_hash: str
    ) -> SavedMetric:
        key = (metric.holder_id, idempotency_key)
        old = self._keys.get(key)
        if old is not None:
            if old[0] != request_hash:
                raise IdempotencyConflictError("idempotency key was reused with another payload")
            return self._rows[old[1]]
        if len(await self.list(metric.holder_id)) >= self._maximum:
            raise ValueError("saved metric limit reached")
        self._rows[metric.id] = metric
        self._keys[key] = (request_hash, metric.id)
        return metric

    async def update(self, metric: SavedMetric, *, expected_version: int) -> SavedMetric:
        old = self._rows.get(metric.id)
        if old is None or old.holder_id != metric.holder_id:
            return metric
        if old.version != expected_version:
            raise VersionConflictError("stale metric version")
        next_row = replace(metric, version=old.version + 1)
        self._rows[metric.id] = next_row
        return next_row

    async def reorder(self, holder_id: str, metric_ids: Sequence[UUID]) -> None:
        rows = await self.list(holder_id)
        if {row.id for row in rows} != set(metric_ids) or len(metric_ids) != len(set(metric_ids)):
            raise ValueError("order must contain exactly the holder's metrics")
        for position, metric_id in enumerate(metric_ids):
            self._rows[metric_id] = replace(
                self._rows[metric_id], position=position, version=self._rows[metric_id].version + 1
            )

    async def delete(self, holder_id: str, metric_id: UUID) -> None:
        row = await self.get(holder_id, metric_id)
        if row is not None:
            self._rows.pop(metric_id, None)
