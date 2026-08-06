"""One attempt, and how it ended."""

from dataclasses import dataclass, field
from uuid import UUID

OK = "OK"


@dataclass(frozen=True)
class AuditEvent:
    """What was attempted, on whose account, and the outcome.

    `outcome` is `OK` or the domain error's `code` — not a boolean. "It failed" and "it failed
    because the balance did not cover it" are different facts, and only the second one is worth
    reading six months later.

    A refusal is an event like any other. The ledger records money that moved; this records what
    somebody tried to do, and those differ exactly where it matters: a payment refused for
    insufficient funds leaves no ledger row at all.
    """

    command: str
    account_id: UUID | None
    outcome: str
    detail: dict[str, object] = field(default_factory=dict)
