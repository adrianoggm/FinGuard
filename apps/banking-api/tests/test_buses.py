"""The buses: one handler per message, and a loud failure when there is none."""

import uuid
from dataclasses import dataclass

import pytest

from finguard_banking.application import (
    Command,
    CommandBus,
    HandlerAlreadyRegisteredError,
    HandlerNotRegisteredError,
    Query,
    QueryBus,
)
from finguard_banking.domain.errors import InsufficientFundsError
from finguard_banking.domain.models.audit_event import AuditEvent


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


class Recorder:
    """A sink that remembers. The Postgres one is exercised in `test_audit.py`."""

    def __init__(self, broken: bool = False) -> None:
        self.events: list[AuditEvent] = []
        self._broken = broken

    async def record(self, event: AuditEvent) -> None:
        if self._broken:
            raise RuntimeError("the audit database is down")
        self.events.append(event)


@dataclass(frozen=True)
class Explode(Command[None]):
    account_id: uuid.UUID


class ExplodeHandler:
    async def handle(self, command: Explode) -> None:
        raise InsufficientFundsError(1_000, 2_000)


async def test_every_command_is_audited_without_the_handler_taking_part() -> None:
    """The bus is the seam, so a handler cannot forget. A per-handler audit is an audit that is
    missing from whichever handler somebody writes next."""
    recorder = Recorder()
    bus = CommandBus(audit=recorder)
    bus.register(Shout, ShoutHandler())

    await bus.dispatch(Shout("hola"))

    assert [(e.command, e.outcome) for e in recorder.events] == [("Shout", "OK")]


async def test_a_refused_command_is_audited_with_its_code_and_still_raises() -> None:
    recorder = Recorder()
    bus = CommandBus(audit=recorder)
    bus.register(Explode, ExplodeHandler())

    with pytest.raises(InsufficientFundsError):
        await bus.dispatch(Explode(account_id=uuid.uuid4()))

    assert [(e.command, e.outcome) for e in recorder.events] == [("Explode", "insufficient_funds")]


async def test_a_broken_audit_does_not_undo_what_already_happened() -> None:
    """The money moved. Failing the response would make the caller retry a payment that went
    through — idempotency would catch it, but the caller would have been told it failed.

    ponytail: best effort, and the ceiling is real. A trail that cannot be lost has to be written
    in the same transaction as the state change, which means the repository rather than the bus.
    What is here records attempts, including the refusals that never have a transaction at all.
    """
    bus = CommandBus(audit=Recorder(broken=True))
    bus.register(Shout, ShoutHandler())

    assert await bus.dispatch(Shout("hola")) == "HOLA"


async def test_the_account_travels_with_the_event_when_the_command_has_one() -> None:
    """Read off the command rather than passed in: an audit row without the account it concerns
    cannot be filtered by account, which is the only way anybody will ever read it."""
    recorder = Recorder()
    account = uuid.uuid4()
    bus = CommandBus(audit=recorder)
    bus.register(Explode, ExplodeHandler())

    with pytest.raises(InsufficientFundsError):
        await bus.dispatch(Explode(account_id=account))

    assert recorder.events[0].account_id == account
