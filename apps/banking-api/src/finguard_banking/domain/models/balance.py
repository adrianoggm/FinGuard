from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from finguard_banking.domain.value_objects.money import Money


@dataclass(frozen=True)
class Balance:
    """An account balance at a point in time.

    `booked` is what the ledger says. `available` is what the customer can spend: today
    they are the same, and they will stop being the same once there are amounts held by
    operations awaiting confirmation (phase 5).

    Both are Money, so the currency travels with the figure instead of alongside it.
    """

    account_id: UUID
    available: Money
    booked: Money
    as_of: datetime

    @property
    def currency(self) -> str:
        return self.booked.currency
