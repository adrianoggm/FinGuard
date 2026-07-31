"""Query side of CQRS.

A Query is a request for data that changes nothing. It is generic in what its handler
answers with, which is what keeps `ask` typed: asking a GetBalanceQuery is known to
return a GetBalanceResult, with no cast at the call site.

PEP 695 type parameters rather than Generic[...]: variance is inferred, so the handler
protocol does not need a covariant TypeVar spelled out.

ponytail: this module is duplicated in assistant-api. Two copies of sixty lines beat a
shared package that nothing else uses yet — it moves to packages/python/ when a third
service needs it.
"""

from typing import Any, Protocol

from finguard_banking.application.bus.errors import (
    HandlerAlreadyRegisteredError,
    HandlerNotRegisteredError,
)


class Query[R]:
    """Marker for a read. `R` is what the handler answers with."""


class QueryHandler[Q, R](Protocol):
    async def handle(self, query: Q) -> R: ...


class QueryBus:
    """Maps a query type to its single handler."""

    def __init__(self) -> None:
        self._handlers: dict[type[Any], Any] = {}

    def register(self, query: type[Any], handler: Any) -> None:
        if query in self._handlers:
            raise HandlerAlreadyRegisteredError(query)
        self._handlers[query] = handler

    async def ask[R](self, query: Query[R]) -> R:
        handler = self._handlers.get(type(query))
        if handler is None:
            raise HandlerNotRegisteredError(type(query))
        answer: R = await handler.handle(query)
        return answer
