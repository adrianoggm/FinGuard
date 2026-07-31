from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Balance:
    """An account balance at a point in time.

    `booked` is what the ledger says. `available` is what the customer can spend:
    today they are the same, and they will stop being the same once there are amounts
    held by operations awaiting confirmation (phase 5).
    """

    account_id: UUID
    available_minor: int
    booked_minor: int
    currency: str
    as_of: datetime
