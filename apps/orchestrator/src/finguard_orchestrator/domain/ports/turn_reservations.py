"""Which request owns a turn, decided before any model or bank work (Task 8 state rules).

A turn is reserved as `(account_id, conversation_id, expected_version, request_id)`. The same
request coming back (a double submit, a client retry) finds its own reservation and resumes or
replays it; a different request against the same version, while that turn is in flight or once it
is done, is stale. A reservation whose turn failed may be taken over by a new request: nothing was
saved, so the version is still the one both were made against.

This is not the canonical store and holds no conversation: the store's compare-and-set still
decides which save wins. PostgreSQL reservations expire after a lease and every claim gets a
fencing token, so a crashed worker can be resumed and a superseded worker cannot close the new
worker's reservation.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from finguard_orchestrator.domain.engine.state import DialogueState


class Reservation(StrEnum):
    NEW = "NEW"  # this request now owns the turn
    RESUME = "RESUME"  # this request owned it and did not finish: resume from its checkpoint
    COMMITTED = "COMMITTED"  # state and exact response payload committed atomically
    DONE = "DONE"  # this request finished it: replay its answer
    TAKEN = "TAKEN"  # another request owns it: the caller answers stale
    # This request id was used for another sentence: refused, never resumed or replayed (R45).
    REUSED = "REUSED"
    ABANDONED = "ABANDONED"  # durable marker for a checkpoint past its retention window
    EXPIRED = "EXPIRED"  # this same request can no longer resume its deleted checkpoint


@dataclass(frozen=True)
class TurnKey:
    account_id: UUID
    conversation_id: UUID
    expected_version: int


@dataclass(frozen=True)
class Reserved:
    outcome: Reservation
    # The answer a DONE request gave, to replay as it was.
    answer: Any = None
    # Fencing token for this specific claim; request_id remains the public idempotency key.
    lease_token: UUID | None = None
    # A different request may take over a failed or expired lease. Its old checkpoint is turn-local
    # and must be discarded so bank results from the abandoned attempt do not accumulate.
    previous_request_id: str | None = None


@dataclass(frozen=True)
class AbandonedTurn:
    expected_version: int
    request_id: str


@dataclass(frozen=True)
class CommittedTurnResult:
    """A response saved in the same transaction as its canonical state version."""

    result: Any
    state: DialogueState
    model_calls: int
    financial_reads: int | None = None


@runtime_checkable
class TurnReservationsPort(Protocol):
    async def reserve(self, key: TurnKey, request_id: str, fingerprint: str) -> Reserved:
        """`fingerprint` is the sentence's: the same id with another one is `REUSED`."""
        ...

    async def replayed(self, key: TurnKey, request_id: str, fingerprint: str) -> Reserved | None:
        """DONE with the answer `request_id` gave for `key` when it finished one, REUSED when it
        was for another sentence, else None. Read before the version check: a duplicate of a turn
        that saved arrives with the version it moved from."""
        ...

    async def failed(self, key: TurnKey, request_id: str, lease_token: UUID) -> None:
        """The turn ended without a save: resumable by `request_id`, or taken over by another."""
        ...

    async def heartbeat(self, key: TurnKey, request_id: str, lease_token: UUID) -> bool:
        """Extend the lease; false means this claim no longer owns the turn."""
        ...

    async def abandon_expired_checkpoints(
        self, key: TurnKey, *, retention_seconds: int, limit: int = 20
    ) -> tuple[AbandonedTurn, ...]:
        """Fence stale resumable turns for this conversation and return their checkpoint ids."""
        ...

    async def checkpoint_deleted(self, key: TurnKey, request_id: str) -> None:
        """Record successful deletion so cleanup retries do not revisit the same thread."""
        ...

    async def done(self, key: TurnKey, request_id: str, lease_token: UUID, answer: Any) -> None: ...
