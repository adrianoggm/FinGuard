"""Wiring failures, shared by both buses.

They used to be declared twice, once per bus, which meant `except HandlerNotRegisteredError`
imported from the query side did not catch the command side's. The concept is the same on
both: a message with no handler, or two handlers claiming one message.
"""

from typing import Any


class HandlerNotRegisteredError(Exception):
    """No handler for this message. A wiring bug, not a domain error."""

    def __init__(self, message: type[Any]) -> None:
        super().__init__(f"no handler registered for {message.__name__}")


class HandlerAlreadyRegisteredError(Exception):
    """Two handlers claimed one message: behaviour would depend on import order."""

    def __init__(self, message: type[Any]) -> None:
        super().__init__(f"{message.__name__} already has a handler")
