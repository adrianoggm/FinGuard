"""What the service says about a turn, and what it never says.

The redaction rule in `observability` is a discipline at the call sites, not a filter — a filter
cannot tell a category name from a merchant name. So the control is here: drive a real turn through
the real routes with a sentence full of things that must not be logged, and read everything that
was emitted.

The other half is the record that makes a degraded conversation readable. Every reported defect was
a real figure about the wrong question, so the useful line is the one naming the three windows —
what the sentence said, what the model produced, what ran — and this asserts it exists.
"""

import json
import logging
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    QueryBus,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_query_bus,
    reporting_today,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.request_log import HEADER
from finguard_orchestrator.observability import JsonLines, account_ref, request_id


class NoCards:
    """The card directory. Required like the balance and the address book, so no build can be
    half-wired — a chat that cannot reach the wallet answered "eso no puedo calcularlo todavía"
    about a screen the user can open."""

    async def all_of(self, account_id, **kwargs):
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=__import__("datetime").date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)

FIVE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=5)
THREE_MONTHS = RelativePeriod(period=Period.LAST_N_MONTHS, n=3)

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


class FakePlanner:
    def __init__(self, result: PlanningResult) -> None:
        self._result = result

    async def plan(self, sentence: str, *, today: date, previous=None, force_follow_up=False):
        return self._result


class FakeLedger:
    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid", **kw):
        return QueryAnswer(currency="EUR", total_minor=189_597, movement_count=12)

    async def ranked(self, ranking, *, account_id, today, reporting_timezone):
        return None


class FakeAccounts:
    async def of(self, account_id, *, today, reporting_timezone):
        raise AssertionError("not this path")


class FakeContacts:
    async def all_of(self, account_id):
        return ()


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


def wired(planned: FinancialPlan) -> TestClient:
    handler = AskFinancialQuestionHandler(
        FakePlanner(PlanningResult.planned(planned)),
        FakeLedger(),
        accounts=FakeAccounts(),
        contacts=FakeContacts(),
        cards=NoCards(),  # type: ignore[arg-type]
    )
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, handler)
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[get_ask_handler] = lambda: handler
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
    )
    app.state.questions_enabled = True
    return TestClient(app)


# --- what never leaves -----------------------------------------------------------------


def test_nothing_the_holder_wrote_reaches_a_log(recording) -> None:
    """The sentence, the person paid and the concept. This is the control the redaction rule
    depends on, because no formatter can tell a category name from a merchant name."""
    client = wired(FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS))

    client.post("/v1/questions", json={"question": PRIVATE})

    emitted = "\n".join(recording.lines)
    assert emitted, "the turn produced no records at all"
    for secret in ("cuánto le he pagado", "Marta", "Solís", "abogado", PRIVATE):
        assert secret not in emitted, f"{secret!r} reached a log"


def test_the_account_is_correlatable_and_not_nameable(recording) -> None:
    """Eight hex characters. Enough to tie two records together, useless to whoever has the log
    and not the ledger — which matters the moment a log file is shipped anywhere."""
    client = wired(FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS))

    client.post("/v1/questions", json={"question": "¿cuánto gasté este mes?"})

    turn = recording.event("turn")
    assert str(ACCOUNT) not in "\n".join(recording.lines)
    assert turn["account"] == account_ref(ACCOUNT)
    assert len(turn["account"]) == 8


# --- what a turn does say ---------------------------------------------------------------


def test_a_turn_records_its_outcome(recording) -> None:
    client = wired(FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS))

    client.post("/v1/questions", json={"question": "¿cuánto gasté este mes?"})

    turn = recording.event("turn")
    assert turn["outcome"] == PlanningOutcome.PLANNED.value
    assert turn["latency_ms"] >= 0


def test_the_three_windows_are_in_one_line(recording) -> None:
    """The record the reported defect needed. The model emitted the inherited five months and the
    sentence says three, so the line shows all three values and the turn that lost one is visible
    without reproducing the conversation."""
    inherited = FinancialPlan(direction=Direction.EXPENSE, period=FIVE_MONTHS)
    client = wired(inherited)
    conversation = {
        "last_plan": {"direction": "EXPENSE", "period": "LAST_N_MONTHS", "n": 5},
    }

    client.post(
        "/v1/questions",
        json={
            "question": "¿cuánto he gastado en los últimos 3 meses?",
            "conversation": conversation,
        },
    )

    window = recording.event("turn.window")
    assert window["planned"] == "LAST_N_MONTHS:5"
    assert window["inherited"] == "LAST_N_MONTHS:5"
    assert window["explicit"] == "LAST_N_MONTHS:3", "the window that actually ran"


# --- one identifier per request -----------------------------------------------------------


def test_every_record_of_a_request_carries_the_same_identifier(recording) -> None:
    client = wired(FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS))

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
    client = wired(FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS))

    response = client.post(
        "/v1/questions",
        json={"question": "¿cuánto gasté este mes?"},
        headers={HEADER: "trace-42\nlevel=CRITICAL " + "x" * 200},
    )

    assert response.headers[HEADER] == ("trace-42levelCRITICAL" + "x" * 200)[:64]
    assert "\n" not in response.headers[HEADER]


def test_the_request_itself_is_recorded_with_no_body(recording) -> None:
    client = wired(FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS))

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


def test_an_idle_conversation_says_so_rather_than_saying_nothing(recording) -> None:
    """`had_workflow` and `had_plan` are what tell a first turn from a follow-up when reading a
    log after the fact, and a first turn is the one case where inheriting nothing is correct."""
    client = wired(FinancialPlan(direction=Direction.EXPENSE, period=THREE_MONTHS))

    client.post("/v1/questions", json={"question": "¿cuánto gasté este mes?"})

    turn = recording.event("turn")
    assert turn["had_workflow"] is None
    assert turn["had_plan"] is False
    assert Conversation() == Conversation()  # the state this asserts about
