import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from finguard_voice_gateway.protocol import parse_event


def _base(event_type: str) -> dict[str, object]:
    return {
        "type": event_type,
        "sessionId": "session-1",
        "turnId": "turn-1",
        "sequence": 7,
        "at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    }


@pytest.mark.parametrize(
    ("event_type", "expected_type"),
    [
        ("session.configure", "ConfigureEvent"),
        ("input.start", "StartEvent"),
        ("input.commit", "CommitEvent"),
        ("response.cancel", "CancelEvent"),
        ("session.close", "CloseEvent"),
        ("ping", "PingEvent"),
    ],
)
def test_parse_accepts_every_client_control(event_type: str, expected_type: str) -> None:
    parsed = parse_event(_base(event_type))
    assert type(parsed).__name__ == expected_type
    assert json.loads(parsed.model_dump_json())["type"] == event_type


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"type": "unknown"},
        {**_base("ping"), "sequence": -1},
        {**_base("ping"), "at": "not-a-date"},
        {**_base("ping"), "extra": True},
        {**_base("input.start"), "turnId": None},
        {**_base("session.configure"), "inputSampleRate": 8000},
    ],
)
def test_parse_rejects_invalid_type_or_fields_deterministically(payload: object) -> None:
    with pytest.raises((ValueError, TypeError, ValidationError)):
        parse_event(payload)
