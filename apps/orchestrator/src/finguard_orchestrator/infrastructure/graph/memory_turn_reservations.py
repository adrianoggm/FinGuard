"""Turn reservations in this process. **Single instance only** and forgotten on a restart, like
`MemoryDialogueStore`: durable reservations would need the same Postgres the store does, and that
is a production gap the experiment reports rather than closes."""

import asyncio
from dataclasses import dataclass
from typing import Any

from finguard_orchestrator.domain.ports.turn_reservations import Reservation, Reserved, TurnKey


@dataclass
class _Held:
    request_id: str
    fingerprint: str
    status: Reservation  # NEW while in flight, RESUME once failed, DONE once saved
    answer: Any = None


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
                self._held[key] = _Held(request_id, fingerprint, Reservation.NEW)
                return Reserved(Reservation.NEW)
            if held.request_id != request_id:
                return Reserved(Reservation.TAKEN)
            if held.fingerprint != fingerprint:
                return Reserved(Reservation.REUSED)
            if held.status is Reservation.DONE:
                return Reserved(Reservation.DONE, held.answer)
            if held.status is Reservation.RESUME:
                held.status = Reservation.NEW
                return Reserved(Reservation.RESUME)
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

    async def failed(self, key: TurnKey, request_id: str) -> None:
        async with self._lock:
            held = self._held.get(key)
            if held is not None and held.request_id == request_id:
                held.status = Reservation.RESUME

    async def done(self, key: TurnKey, request_id: str, answer: Any) -> None:
        async with self._lock:
            fingerprint = self._held[key].fingerprint if key in self._held else ""
            self._held[key] = _Held(request_id, fingerprint, Reservation.DONE, answer)
