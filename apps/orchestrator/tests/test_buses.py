"""The buses: one handler per message, and a loud failure when there is none."""

from dataclasses import dataclass

import pytest

from finguard_orchestrator.application import (
    Command,
    CommandBus,
    HandlerAlreadyRegisteredError,
    HandlerNotRegisteredError,
    Query,
    QueryBus,
)


@dataclass(frozen=True)
class Ping(Query[str]):
    value: str


@dataclass(frozen=True)
class Unhandled(Query[str]):
    pass


class PingHandler:
    async def handle(self, query: Ping) -> str:
        return f"pong:{query.value}"


@dataclass(frozen=True)
class Shout(Command[str]):
    value: str


class ShoutHandler:
    async def handle(self, command: Shout) -> str:
        return command.value.upper()


async def test_a_query_reaches_its_handler() -> None:
    bus = QueryBus()
    bus.register(Ping, PingHandler())

    assert await bus.ask(Ping("hola")) == "pong:hola"


async def test_a_command_reaches_its_handler() -> None:
    bus = CommandBus()
    bus.register(Shout, ShoutHandler())

    assert await bus.dispatch(Shout("hola")) == "HOLA"


async def test_a_query_with_no_handler_fails_loudly() -> None:
    # Silence here would mean an endpoint answering None, which is far harder to debug.
    bus = QueryBus()

    with pytest.raises(HandlerNotRegisteredError, match="Unhandled"):
        await bus.ask(Unhandled())


async def test_a_command_with_no_handler_fails_loudly() -> None:
    with pytest.raises(HandlerNotRegisteredError, match="Shout"):
        await CommandBus().dispatch(Shout("hola"))


def test_two_handlers_cannot_claim_the_same_query() -> None:
    # Otherwise the answer would depend on registration order.
    bus = QueryBus()
    bus.register(Ping, PingHandler())

    with pytest.raises(HandlerAlreadyRegisteredError, match="Ping"):
        bus.register(Ping, PingHandler())


def test_two_handlers_cannot_claim_the_same_command() -> None:
    bus = CommandBus()
    bus.register(Shout, ShoutHandler())

    with pytest.raises(HandlerAlreadyRegisteredError, match="Shout"):
        bus.register(Shout, ShoutHandler())


async def test_the_registry_is_per_bus() -> None:
    # Registering on one bus must not leak into another: tests build their own.
    first = QueryBus()
    first.register(Ping, PingHandler())

    with pytest.raises(HandlerNotRegisteredError):
        await QueryBus().ask(Ping("hola"))
