from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import Request

from finguard_assistant.application import CommandBus, QueryBus

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
