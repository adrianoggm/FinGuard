from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ControlEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    sessionId: str
    turnId: str | None = None
    sequence: int = Field(ge=0)
    at: datetime


class ConfigureEvent(ControlEvent):
    type: Literal["session.configure"]
    inputSampleRate: Literal[16000] = 16000
    outputSampleRate: Literal[24000] = 24000


class StartEvent(ControlEvent):
    type: Literal["input.start"]
    turnId: str


class CommitEvent(ControlEvent):
    type: Literal["input.commit"]
    turnId: str


class CancelEvent(ControlEvent):
    type: Literal["response.cancel"]


class CloseEvent(ControlEvent):
    type: Literal["session.close"]


class PingEvent(ControlEvent):
    type: Literal["ping"]


EVENT_TYPES: dict[str, type[ControlEvent]] = {
    "session.configure": ConfigureEvent,
    "input.start": StartEvent,
    "input.commit": CommitEvent,
    "response.cancel": CancelEvent,
    "session.close": CloseEvent,
    "ping": PingEvent,
}


def parse_event(value: Any) -> ControlEvent:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValueError("event must be a JSON object with a type")
    model = EVENT_TYPES.get(value["type"])
    if model is None:
        raise ValueError("unknown event type")
    return model.model_validate(value)
