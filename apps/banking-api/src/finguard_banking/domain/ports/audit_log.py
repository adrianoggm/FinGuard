from typing import Protocol, runtime_checkable

from finguard_banking.domain.models.audit_event import AuditEvent


@runtime_checkable
class AuditLogPort(Protocol):
    """Where the trail is written.

    `record` returns nothing and is expected not to raise for anything the caller can act on: it
    runs after the state change it describes, so refusing at that point would report a failure for
    something that already succeeded.
    """

    async def record(self, event: AuditEvent) -> None: ...
