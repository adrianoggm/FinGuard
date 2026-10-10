"""In-process turn reservations for tests and explicitly non-durable local runs.

Production graph mode uses `PostgresTurnReservations`; this adapter intentionally has no crash
recovery or cross-process coordination.
"""

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from finguard_orchestrator.domain.ports.turn_reservations import (
    AbandonedTurn,
    Reservation,
    Reserved,
    TurnKey,
)


@dataclass
class _Held:
    request_id: str
    fingerprint: str
    status: Reservation  # NEW while in flight, RESUME once failed, DONE once saved
    answer: Any = None
    lease_token: UUID | None = None


class MemoryTurnReservations:
    """Implements TurnReservationsPort."""

    def __init__(self) -> None:
        self._held: dict[TurnKey, _Held] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, key: TurnKey, request_id: str, fingerprint: str) -> Reserved:
        async with self._lock:
            held = self._held.get(key)
            if held is None or (
                held.status is Reservation.RESUME and held.request_id != request_id
            ):
                # Free, or a failed turn nobody is resuming: nothing was saved under it.
                token = uuid4()
                self._held[key] = _Held(request_id, fingerprint, Reservation.NEW, lease_token=token)
                return Reserved(Reservation.NEW, lease_token=token)
            if held.request_id != request_id:
                return Reserved(Reservation.TAKEN)
            if held.fingerprint != fingerprint:
                return Reserved(Reservation.REUSED)
            if held.status is Reservation.DONE:
                return Reserved(Reservation.DONE, held.answer)
            if held.status is Reservation.RESUME:
                held.status = Reservation.NEW
                held.lease_token = uuid4()
                return Reserved(Reservation.RESUME, lease_token=held.lease_token)
            # The same request while it is still in flight: a double submit. Stale, not a second
            # run of the same turn beside the first.
            return Reserved(Reservation.TAKEN)

    async def replayed(self, key: TurnKey, request_id: str, fingerprint: str) -> Reserved | None:
        held = self._held.get(key)
        if held is None or held.request_id != request_id:
            return None
        if held.fingerprint != fingerprint:
            return Reserved(Reservation.REUSED)
        return Reserved(Reservation.DONE, held.answer) if held.status is Reservation.DONE else None

    async def failed(self, key: TurnKey, request_id: str, lease_token: UUID) -> None:
        async with self._lock:
            held = self._held.get(key)
            if (
                held is not None
                and held.request_id == request_id
                and held.lease_token == lease_token
            ):
                held.status = Reservation.RESUME
                held.lease_token = None

    async def heartbeat(self, key: TurnKey, request_id: str, lease_token: UUID) -> bool:
        # The in-memory implementation has no expiring lease; ownership still fences close calls.
        held = self._held.get(key)
        return bool(
            held is not None
            and held.request_id == request_id
            and held.lease_token == lease_token
            and held.status is Reservation.NEW
        )

    async def abandon_expired_checkpoints(
        self, key: TurnKey, *, retention_seconds: int, limit: int = 20
    ) -> tuple[AbandonedTurn, ...]:
        # Memory reservations are a test/local adapter and do not persist checkpoint rows across
        # process lifetimes. It has nothing to expire.
        return ()

    async def checkpoint_deleted(self, key: TurnKey, request_id: str) -> None:
        return None

    async def done(self, key: TurnKey, request_id: str, lease_token: UUID, answer: Any) -> None:
        async with self._lock:
            held = self._held.get(key)
            if held is None or held.request_id != request_id or held.lease_token != lease_token:
                raise RuntimeError("turn reservation lease is no longer owned")
            self._held[key] = _Held(request_id, held.fingerprint, Reservation.DONE, answer)
