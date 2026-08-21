"""Private saved-metric HTTP surface.

The demo repository is intentionally assistant-owned.  The item endpoint accepts an injected
executor so production wiring can run a typed plan through the normal banking application port.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from finguard_orchestrator.bootstrap.dependencies import metrics_enabled, requires
from finguard_orchestrator.domain.models.saved_metric import SavedMetric
from finguard_orchestrator.domain.ports.saved_metric_repository import (
    IdempotencyConflictError,
    SavedMetricRepository,
    VersionConflictError,
)
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.metrics.codec import CodecError
from finguard_orchestrator.infrastructure.security.metric_tokens import (
    MetricTokenCodec,
    MetricTokenError,
)

router = APIRouter(prefix="/v1/metrics", tags=["metrics"], dependencies=[Depends(metrics_enabled)])
WhoRead = Annotated[Caller, Depends(requires(Scope.METRICS_READ))]
WhoWrite = Annotated[Caller, Depends(requires(Scope.METRICS_WRITE))]


class CreateMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)
    definition_token: str = Field(min_length=1, max_length=20000)

    @field_validator("title")
    @classmethod
    def _meaningful_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("title cannot be blank")
        return title


class MetricPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def _meaningful_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            raise ValueError("title cannot be blank")
        return title


def _repo(request: Request) -> SavedMetricRepository:
    return cast(SavedMetricRepository, request.app.state.metrics_repository)


def _token_codec(request: Request) -> MetricTokenCodec:
    codec = request.app.state.metric_tokens
    if not isinstance(codec, MetricTokenCodec):
        raise RuntimeError("invalid metrics token codec")
    return codec


def _metadata(row: SavedMetric) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "position": row.position,
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_metric(
    body: CreateMetric,
    request: Request,
    who: WhoWrite,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    if not idempotency_key or len(idempotency_key) > 200:
        raise HTTPException(422, "Idempotency-Key is required")
    try:
        definition = _token_codec(request).verify(body.definition_token, who.holder_reference)
    except (MetricTokenError, CodecError) as exc:
        raise HTTPException(422, "invalid definition token") from exc
    payload_hash = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    row = SavedMetric.new(who.holder_reference, body.title, definition)
    try:
        saved = await _repo(request).create(
            row, idempotency_key=idempotency_key, request_hash=payload_hash
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(
        content=json.dumps(_metadata(saved), default=str),
        status_code=201,
        media_type="application/json",
        headers={"Location": f"/v1/metrics/{saved.id}"},
    )


@router.get("")
async def list_metrics(request: Request, who: WhoRead) -> dict[str, Any]:
    return {"metrics": [_metadata(row) for row in await _repo(request).list(who.holder_reference)]}


@router.put("/order")
async def reorder_metrics(ids: list[UUID], request: Request, who: WhoWrite) -> Response:
    try:
        await _repo(request).reorder(who.holder_reference, ids)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(status_code=204)


@router.get("/{metric_id}", dependencies=[Depends(requires(Scope.QUESTIONS_READ))])
async def get_metric(metric_id: UUID, request: Request, who: WhoRead) -> dict[str, Any]:
    row = await _repo(request).get(who.holder_reference, metric_id)
    if row is None:
        raise HTTPException(404, "metric not found")
    # Execution is injected by the composition root. Never return stored figures as a fallback.
    executor = getattr(request.app.state, "metrics_executor", None)
    if executor is None:
        raise HTTPException(503, "metrics execution is unavailable")
    fresh = await executor(row.definition, who)
    return {**_metadata(row), "answer": fresh}


@router.patch("/{metric_id}")
async def patch_metric(
    metric_id: UUID,
    body: MetricPatch,
    request: Request,
    who: WhoWrite,
    if_match: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    row = await _repo(request).get(who.holder_reference, metric_id)
    if row is None:
        raise HTTPException(404, "metric not found")
    if body.title is None:
        raise HTTPException(422, "title is required")
    if if_match is None:
        raise HTTPException(428, "If-Match is required")
    try:
        expected = int(if_match.strip('"'))
        saved = await _repo(request).update(
            SavedMetric(
                holder_id=row.holder_id,
                title=body.title,
                definition=row.definition,
                position=row.position,
                version=row.version,
                id=row.id,
                created_at=row.created_at,
                updated_at=row.updated_at,
            ),
            expected_version=expected,
        )
    except (ValueError, VersionConflictError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return _metadata(saved)


@router.delete("/{metric_id}", status_code=204)
async def delete_metric(metric_id: UUID, request: Request, who: WhoWrite) -> Response:
    await _repo(request).delete(who.holder_reference, metric_id)
    return Response(status_code=204)
