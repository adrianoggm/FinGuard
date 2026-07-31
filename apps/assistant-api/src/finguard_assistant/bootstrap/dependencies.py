from fastapi import Request

from finguard_assistant.application.bus.command_bus import CommandBus
from finguard_assistant.application.bus.query_bus import QueryBus


def get_query_bus(request: Request) -> QueryBus:
    """Controllers ask the bus, never a handler directly."""
    bus: QueryBus = request.app.state.query_bus
    return bus


def get_command_bus(request: Request) -> CommandBus:
    bus: CommandBus = request.app.state.command_bus
    return bus
