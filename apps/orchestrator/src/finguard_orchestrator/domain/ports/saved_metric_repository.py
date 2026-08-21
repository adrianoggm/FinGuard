"""Port for assistant-owned metric persistence."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from finguard_orchestrator.domain.models.saved_metric import SavedMetric


class IdempotencyConflictError(ValueError):
    pass


class VersionConflictError(ValueError):
    pass


class SavedMetricRepository(Protocol):
    async def list(self, holder_id: str) -> list[SavedMetric]: ...
    async def get(self, holder_id: str, metric_id: UUID) -> SavedMetric | None: ...
    async def create(
        self, metric: SavedMetric, *, idempotency_key: str, request_hash: str
    ) -> SavedMetric: ...
    async def update(self, metric: SavedMetric, *, expected_version: int) -> SavedMetric: ...
    async def reorder(self, holder_id: str, metric_ids: Sequence[UUID]) -> None: ...
    async def delete(self, holder_id: str, metric_id: UUID) -> None: ...
