"""Command side of CQRS.

A Command changes state. It answers with the resulting state rather than nothing: the
endpoints that issue one have to reply with it, and a second read to fetch what was just
written would be a race waiting to happen.

It is the seam phase 5 needs — idempotency, audit and correlation IDs all hook here,
once, instead of inside every handler.

ponytail: this module is duplicated in banking-api. Two copies of sixty lines beat a
shared package that nothing else uses yet — it moves to packages/python/ when a third
service needs it.
"""

from typing import Any, Protocol

from finguard_assistant.application.bus.errors import (
    HandlerAlreadyRegisteredError,
    HandlerNotRegisteredError,
)


class Command[R]:
    """Marker for a write. `R` is what the handler answers with."""


class CommandHandler[C, R](Protocol):
    async def handle(self, command: C) -> R: ...


class CommandBus:
    """Maps a command type to its single handler."""

    def __init__(self) -> None:
        self._handlers: dict[type[Any], Any] = {}

    def register(self, command: type[Any], handler: Any) -> None:
        if command in self._handlers:
            raise HandlerAlreadyRegisteredError(command)
        self._handlers[command] = handler

    async def dispatch[R](self, command: Command[R]) -> R:
        handler = self._handlers.get(type(command))
        if handler is None:
            raise HandlerNotRegisteredError(type(command))
        answer: R = await handler.handle(command)
        return answer
