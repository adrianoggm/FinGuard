"""Which request owns a turn, decided before any model or bank work (Task 8 state rules).

A turn is reserved as `(account_id, conversation_id, expected_version, request_id)`. The same
request coming back (a double submit, a client retry) finds its own reservation and resumes or
replays it; a different request against the same version, while that turn is in flight or once it
is done, is stale. A reservation whose turn failed may be taken over by a new request: nothing was
saved, so the version is still the one both were made against.

This is not the canonical store and holds no conversation: the store's compare-and-set still
decides which save wins.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


class Reservation(StrEnum):
    NEW = "NEW"  # this request now owns the turn
    RESUME = "RESUME"  # this request owned it and did not finish: resume from its checkpoint
    DONE = "DONE"  # this request finished it: replay its answer
    TAKEN = "TAKEN"  # another request owns it: the caller answers stale


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


@runtime_checkable
class TurnReservationsPort(Protocol):
    async def reserve(self, key: TurnKey, request_id: str) -> Reserved: ...

    async def replayed(self, key: TurnKey, request_id: str) -> Any:
        """The answer `request_id` gave for `key`, when it finished one, else None. Read before the
        version check: a duplicate of a turn that saved arrives with the version it moved from."""
        ...

    async def failed(self, key: TurnKey, request_id: str) -> None:
        """The turn ended without a save: resumable by `request_id`, or taken over by another."""
        ...

    async def done(self, key: TurnKey, request_id: str, answer: Any) -> None: ...
