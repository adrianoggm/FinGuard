from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Header, Request

from finguard_assistant.application import AskFinancialQuestionHandler, CommandBus, QueryBus
from finguard_assistant.domain.ports.caller_identity import CallerIdentityPort
from finguard_assistant.domain.value_objects.caller import Caller

# The zone the user's day is measured in. Not UTC: "hoy" means the holder's today, and a
# purchase at 23:30 in Madrid belongs to that day, not the next one.
REPORTING_TIMEZONE = "Europe/Madrid"


def get_query_bus(request: Request) -> QueryBus:
    """Controllers ask the bus, never a handler directly."""
    bus: QueryBus = request.app.state.query_bus
    return bus


def get_command_bus(request: Request) -> CommandBus:
    bus: CommandBus = request.app.state.command_bus
    return bus


def reporting_today(request: Request) -> date:
    """What day it is where the money is.

    The clock lives here and nowhere deeper, exactly as in banking: the planner takes
    today as an argument, so a question asked on the 15th resolves the same way when the
    evaluation replays it in March.
    """
    return datetime.now(ZoneInfo(REPORTING_TIMEZONE)).date()


def get_ask_handler(request: Request) -> AskFinancialQuestionHandler:
    """The one handler a controller reaches without the bus.

    A bus is request and response: one message in, one answer out. A stream is a
    different shape, and bending `ask` into returning an async iterator would make every
    query pay for the one that streams. So this route takes the handler directly and
    says so, rather than quietly widening the bus.
    """
    handler: AskFinancialQuestionHandler = request.app.state.ask_handler
    return handler


async def authenticated_caller(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Caller:
    """Identity, the first layer of §12.1 and the one that was missing.

    A dependency rather than middleware, so a route that needs a caller says so in its
    signature and a route that does not cannot accidentally receive one. The account it
    carries is where the account now comes from — the request body no longer has a say.
    """
    identity: CallerIdentityPort = request.app.state.identity
    return await identity.resolve(authorization)
