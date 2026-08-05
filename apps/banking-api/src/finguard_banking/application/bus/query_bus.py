"""Query side of CQRS.

A Query is a request for data that changes nothing. It is generic in what its handler
answers with, which is what keeps `ask` typed: asking a GetBalanceQuery is known to
return a GetBalanceResult, with no cast at the call site.

PEP 695 type parameters rather than Generic[...]: variance is inferred, so the handler
protocol does not need a covariant TypeVar spelled out.

This module is duplicated in orchestrator, and it stays that way. The two services are
independent deployables: the point is that neither has to be released because the other changed,
and a shared package would buy sixty lines and sell that back. Public contracts are shared
(packages/contracts); internal machinery is not, and a bus is machinery.

A third service does not change this. It gets its own copy.
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
