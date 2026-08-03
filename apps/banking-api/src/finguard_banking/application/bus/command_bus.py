"""Command side of CQRS.

A Command changes state. It answers with the resulting state rather than nothing: the
endpoints that issue one have to reply with it, and a second read to fetch what was just
written would be a race waiting to happen.

It is the seam phase 5 needs — idempotency, audit and correlation IDs all hook here,
once, instead of inside every handler. The audit lives here and not in the handlers
because a per-handler audit is one that will be missing from whichever handler somebody
writes next.

ponytail: this module is duplicated in orchestrator. Two copies of sixty lines beat a
shared package that nothing else uses yet — it moves to packages/python/ when a third
service needs it.
"""

import dataclasses
import logging
from typing import Any, Protocol

from finguard_banking.application.bus.errors import (
    HandlerAlreadyRegisteredError,
    HandlerNotRegisteredError,
)
from finguard_banking.domain.errors import DomainError
from finguard_banking.domain.models.audit_event import OK, AuditEvent
from finguard_banking.domain.ports.audit_log import AuditLogPort

logger = logging.getLogger(__name__)


class Command[R]:
    """Marker for a write. `R` is what the handler answers with."""


class CommandHandler[C, R](Protocol):
    async def handle(self, command: C) -> R: ...


class CommandBus:
    """Maps a command type to its single handler, and records every attempt.

    `audit` is optional so a test can build a bus without a database, and the composition
    root always passes one.
    """

    def __init__(self, audit: AuditLogPort | None = None) -> None:
        self._handlers: dict[type[Any], Any] = {}
        self._audit = audit

    def register(self, command: type[Any], handler: Any) -> None:
        if command in self._handlers:
            raise HandlerAlreadyRegisteredError(command)
        self._handlers[command] = handler

    async def dispatch[R](self, command: Command[R]) -> R:
        handler = self._handlers.get(type(command))
        if handler is None:
            # Not audited: nothing was attempted against any account. It is a wiring bug,
            # and it is already loud.
            raise HandlerNotRegisteredError(type(command))
        try:
            answer: R = await handler.handle(command)
        except DomainError as refusal:
            # A refusal is an event. An audit trail of successes cannot answer "did somebody
            # try to empty this account", which is the question it exists for.
            await self._record(command, refusal.code)
            raise
        await self._record(command, OK)
        return answer

    async def _record(self, command: object, outcome: str) -> None:
        if self._audit is None:
            return
        # Whatever the command carries, so a command added later is audited without anybody
        # remembering to extend this. The other side of that is in migration 0008: a command
        # with a secret field would write the secret here, and this is where it would have to
        # be excluded.
        detail: dict[str, object] = {}
        if dataclasses.is_dataclass(command) and not isinstance(command, type):
            detail = dataclasses.asdict(command)
        try:
            await self._audit.record(
                AuditEvent(
                    command=type(command).__name__,
                    account_id=getattr(command, "account_id", None),
                    outcome=outcome,
                    detail=detail,
                )
            )
        except Exception:
            # Best effort, and the ceiling is real: what has already happened cannot be undone
            # because the log failed, and failing the response would tell a caller their payment
            # did not go through when it did. A trail that cannot be lost has to be written in the
            # same transaction as the state change, which means the repository rather than the bus.
            logger.exception("could not record %s in the audit trail", type(command).__name__)
