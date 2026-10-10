"""What the service says about a turn, and what it never says.

The redaction rule in `observability` is a discipline at the call sites, not a filter — a filter
cannot tell a category name from a merchant name. So the control is here: drive a turn through
the real question route with a sentence full of things that must not be logged, and read everything
that was emitted.

The other half is the request identifier: one per request, on every line it produces, so a turn can
be followed from the browser through here.
"""

import json
import logging
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.bootstrap.dependencies import authenticated_caller, reporting_today
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.request_log import HEADER
from finguard_orchestrator.observability import (
    JsonLines,
    configure_logging,
    request_id,
)

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)

# Everything a log must not repeat, in one sentence: what somebody typed, who they pay, and the
# note they wrote about it.
PRIVATE = "¿cuánto le he pagado a Marta Solís Vega por el abogado en los últimos 3 meses?"


class Recording(logging.Handler):
    """Captures records as the JSON they would be written as, which is what actually leaves."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(JsonLines())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    @property
    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.lines]

    def event(self, name: str) -> dict:
        found = [event for event in self.events if event["event"] == name]
        assert found, f"no {name!r} in {[e['event'] for e in self.events]}"
        return found[-1]


@pytest.fixture
def recording():
    handler = Recording()
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(previous)


@pytest.fixture(autouse=True)
def _restore():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class Delegating:
    """A graph that hands every turn on. Enough to carry a request through the question route and
    the dispatch seam -- which logs the delegation -- without scripting an answer this file never
    reads; what the graph's own nodes log is not exercised here."""

    async def answer(self, *_: object, **__: object) -> EngineAnswer:
        return EngineAnswer(None, DialogueState(uuid.UUID(int=1), version=1), "CHART", 0, {})


@pytest.fixture(autouse=True)
def _graph():
    previous = getattr(app.state, "engine", None)
    app.state.engine = Delegating()
    yield
    app.state.engine = previous


def wired() -> TestClient:
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
    )
    app.state.questions_enabled = True
    return TestClient(app, headers={"Idempotency-Key": "logging-attempt"})


# --- what never leaves -----------------------------------------------------------------


def test_nothing_the_holder_wrote_reaches_a_log(recording) -> None:
    """The sentence, the person paid and the concept. This is the control the redaction rule
    depends on, because no formatter can tell a category name from a merchant name."""
    client = wired()

    client.post("/v1/questions", json={"question": PRIVATE})

    emitted = "\n".join(recording.lines)
    assert emitted, "the turn produced no records at all"
    for secret in ("cuánto le he pagado", "Marta", "Solís", "abogado", PRIVATE):
        assert secret not in emitted, f"{secret!r} reached a log"


# --- one identifier per request -----------------------------------------------------------


def test_every_record_of_a_request_carries_the_same_identifier(recording) -> None:
    client = wired()

    response = client.post("/v1/questions", json={"question": "¿cuánto gasté este mes?"})

    given_back = response.headers[HEADER]
    assert given_back
    ours = [e for e in recording.events if e["logger"].startswith("finguard_orchestrator")]
    assert len(ours) >= 2, "the turn and the request, at least"
    for event in ours:
        assert event["request_id"] == given_back
    # The test client's own httpx record is emitted from outside the server, so it carries the
    # dash. That is right rather than a gap: it is the caller logging its call, not this service
    # logging the request it served.


def test_a_callers_identifier_is_honoured_and_bounded(recording) -> None:
    """So a request can be followed from the browser through here. It is caller-controlled input
    like any other, so it is stripped to an identifier and cut — this value ends up on every line
    of the request, and a header with a newline in it would forge records."""
    client = wired()

    response = client.post(
        "/v1/questions",
        json={"question": "¿cuánto gasté este mes?"},
        headers={HEADER: "trace-42\nlevel=CRITICAL " + "x" * 200},
    )

    assert response.headers[HEADER] == ("trace-42levelCRITICAL" + "x" * 200)[:64]
    assert "\n" not in response.headers[HEADER]


def test_the_request_itself_is_recorded_with_no_body(recording) -> None:
    client = wired()

    client.post("/v1/questions", json={"question": PRIVATE})

    request = recording.event("request")
    assert request["path"] == "/v1/questions"
    assert request["status"] == 200
    assert "question" not in request


# --- the identifier outside a request -------------------------------------------------------


def test_a_record_with_no_request_behind_it_still_formats() -> None:
    """Anything logged at startup, or by a background task, has no request. A missing identifier
    is a dash rather than a KeyError in the formatter: a logger that raises is worse than silent."""
    assert request_id.get() == "-"

    record = logging.LogRecord("x", logging.INFO, "", 0, "started", None, None)

    assert json.loads(JsonLines().format(record))["request_id"] == "-"


# --- the trace a line was emitted inside ----------------------------------------------------


def test_with_tracing_off_a_line_is_what_it_always_was() -> None:
    """A log line can name the trace it belongs to, so getting from a record to the turn's
    waterfall is a copy and a paste rather than a second correlation scheme somebody maintains.

    With nothing tracing — which is this suite, and a developer's laptop — the two keys are
    **absent** rather than zeroed. Thirty-two zeros look like an identifier and correlate nothing,
    which is the argument `banking/client.py` already makes about forwarding a `-` request id: a
    false correlation is worse than a missing one, because only one of the two is visible to
    whoever is reading at the time. So every line here is what it was before the field existed.
    """
    from finguard_orchestrator.observability.logging import TraceContext

    line = logging.LogRecord("x", logging.INFO, "", 0, "turn", None, None)

    assert TraceContext().filter(line) is True

    emitted = json.loads(JsonLines().format(line))
    assert "trace_id" not in emitted
    assert "span_id" not in emitted
    assert emitted["request_id"] == "-", "and the correlation that already worked still does"


def test_the_trace_filter_is_installed_whether_or_not_anything_traces() -> None:
    """The filter always runs and the *source* it reads is what gets swapped, so turning tracing on
    does not need the logging configuration rebuilt — and a filter that only exists when a
    collector does is a filter nothing has ever executed."""
    from finguard_orchestrator.observability.logging import RequestId, TraceContext

    configure_logging(level="INFO", as_json=True)
    handler = logging.getLogger().handlers[0]

    assert any(isinstance(f, TraceContext) for f in handler.filters)
    assert any(isinstance(f, RequestId) for f in handler.filters)
