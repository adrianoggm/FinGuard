"""The request id crosses to banking, and there is nowhere for a client to slip past.

`request_log.py` has always said the inbound `X-Request-Id` is honoured *"so a request crossing from
the web client to here to banking can be followed end to end"*. It was honoured, bounded, stamped on
every local record and echoed back — and then never forwarded, because each of the seven clients
built its own `httpx.AsyncClient` and there was no one place to put it. The promise was written
down, half kept, and nothing failed.

So the tests come in two kinds, and neither can be replaced by a unit test on the factory:

**The first drives a real request through the real app to a real socket.** A unit test asserting
that `banking_client()` sets a header would have passed on the morning of the defect, with all ten
call sites still building their own clients. The property is that the value survives the whole path
— middleware, context variable, handler, adapter, wire — so the test has to walk it. Nothing here
is patched: `TestClient` runs the app, `HttpContacts` is the real adapter, and the banking side is
an HTTP server on a real port that reads the header off the wire.

**The second is that a new eighth client cannot get this wrong.** Every `.py` in
`infrastructure/banking/` is parsed and refused if it constructs an `AsyncClient` of its own, and
every method of the seven is driven to check it actually reaches the wire through the seam rather
than merely importing it.
"""

import ast
import contextvars
import json
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import QueryBus
from finguard_orchestrator.application.queries.list_contacts import (
    ListContactsHandler,
    ListContactsQuery,
)
from finguard_orchestrator.bootstrap.dependencies import authenticated_caller, get_query_bus
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import BankingUnavailableError, QueryRejectedError
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements
from finguard_orchestrator.infrastructure.banking import client as seam
from finguard_orchestrator.infrastructure.banking.client import banking_client
from finguard_orchestrator.infrastructure.banking.http_account_summary import HttpAccountSummary
from finguard_orchestrator.infrastructure.banking.http_accounts import HttpAccounts
from finguard_orchestrator.infrastructure.banking.http_cards import HttpCards
from finguard_orchestrator.infrastructure.banking.http_contacts import HttpContacts
from finguard_orchestrator.infrastructure.banking.http_financial_query import HttpFinancialQuery
from finguard_orchestrator.infrastructure.banking.http_movement_categories import (
    HttpMovementCategories,
)
from finguard_orchestrator.infrastructure.banking.http_payments import HttpPayments
from finguard_orchestrator.infrastructure.request_log import HEADER
from finguard_orchestrator.observability import request_id

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
CARD = uuid.UUID("33333333-3333-3333-3333-333333333333")
INTENT = uuid.UUID("44444444-4444-4444-4444-444444444444")
MOVEMENT = uuid.UUID("55555555-5555-5555-5555-555555555555")

TODAY = date(2026, 8, 3)
ZONE = "Europe/Madrid"


# --- a banking side that is a real socket ------------------------------------------------


class Recorded:
    """What reached the far end of the wire."""

    def __init__(self) -> None:
        self.request_ids: list[str | None] = []
        self.idempotency_keys: list[str | None] = []

    @property
    def only_id(self) -> str | None:
        assert len(self.request_ids) == 1, f"expected one call, got {len(self.request_ids)}"
        return self.request_ids[0]


@contextmanager
def banking_answering(status: int = 200, body: object = None) -> Iterator[tuple[str, Recorded]]:
    """A banking that answers on a real port and remembers the headers it was sent.

    A real server rather than `httpx.MockTransport`, because the usual way to reach these adapters
    in this suite is to replace `httpx.AsyncClient` — and a test for the seam cannot replace the
    object the seam configures without testing itself. This one patches nothing at all: the client
    under test opens a socket, and what is asserted is what a packet capture would show.
    """
    recorded = Recorded()
    answer = json.dumps({} if body is None else body).encode()

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.1 with an explicit length. httpx keeps the connection alive, and a 1.0 answer with
        # no length would make it wait for a close that a threaded server is in no hurry to send.
        protocol_version = "HTTP/1.1"

        def _answer(self) -> None:
            recorded.request_ids.append(self.headers.get(HEADER))
            recorded.idempotency_keys.append(self.headers.get("Idempotency-Key"))
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        # The names are `BaseHTTPRequestHandler`'s, which dispatches on the verb spelled this way.
        do_GET = do_POST = do_PUT = _answer  # noqa: N815

        def log_message(self, *args: object) -> None:
            """Silent: a passing test writes nothing to stderr."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", recorded
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def asking_contacts(base_url: str) -> TestClient:
    """The real route, over the real adapter, pointed at the server above."""
    bus = QueryBus()
    bus.register(
        ListContactsQuery,
        ListContactsHandler(HttpContacts(base_url=base_url, timeout_seconds=5.0)),
    )
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-TEST",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
        reporting_timezone=ZONE,
    )
    return TestClient(app)


# --- end to end: the id a caller sent is the id banking is told --------------------------


def test_the_id_a_caller_sends_reaches_banking_unchanged() -> None:
    """The whole promise, in one path.

    A web client sends `X-Request-Id`; the middleware honours it; a route runs; an adapter calls
    banking over a socket. This asserts the same string comes out of the far end. Before the seam
    existed every part of that sentence was true except the last, and no test said so.
    """
    sent = "web-7f3a2b1c-0001"

    with banking_answering(body={"contacts": []}) as (base_url, recorded):
        response = asking_contacts(base_url).get("/v1/contacts", headers={HEADER: sent})

    assert response.status_code == 200
    assert recorded.only_id == sent


def test_banking_is_told_the_same_id_the_caller_is_told() -> None:
    """The two ends of the correlation agree, whatever the caller sent.

    The response header is what a browser's network tab shows and what a support conversation quotes
    back; the header banking receives is what its logs will be grepped by. If those two could ever
    differ the id would be worse than useless — it would look like a correlation and join nothing.
    """
    with banking_answering(body={"contacts": []}) as (base_url, recorded):
        response = asking_contacts(base_url).get("/v1/contacts", headers={HEADER: "abc-123"})

    assert recorded.only_id == response.headers[HEADER]


def test_a_caller_who_sends_no_id_still_gives_banking_one_to_join_on() -> None:
    """The ordinary case. The middleware generates one, and the generated one travels too —
    otherwise correlation would only work for the callers who already had it."""
    with banking_answering(body={"contacts": []}) as (base_url, recorded):
        response = asking_contacts(base_url).get("/v1/contacts")

    assert recorded.only_id == response.headers[HEADER]
    assert recorded.only_id


def test_a_long_id_from_a_caller_is_cut_to_the_same_length_at_both_ends() -> None:
    """The bound `request_log.py` sets is the bound banking is told, not a longer one.

    The seam applies that rule a second time and this is what says it did not become a *different*
    rule: the filter is idempotent, so a value that came through the middleware leaves byte for byte
    as the middleware left it. That is what lets the tests above claim "unchanged" rather than
    "unchanged unless it was long".
    """
    with banking_answering(body={"contacts": []}) as (base_url, recorded):
        response = asking_contacts(base_url).get("/v1/contacts", headers={HEADER: "a" * 200})

    assert recorded.only_id == "a" * 64
    assert response.headers[HEADER] == recorded.only_id


async def test_a_request_splitting_id_never_reaches_the_wire() -> None:
    """The reason the bound is restated here and not merely trusted from the middleware.

    The middleware is not the only writer of this context variable, and this is the last code that
    runs before a header goes on a socket. httpx2 accepts a header value containing CRLF without
    complaint, so an unbounded seam is a request-splitting gadget pointed at the bank: one call
    arriving as two, the second one written by whoever set the header.

    Driven from the context variable rather than through `TestClient`, which refuses to carry a
    newline in a header at all — the path this guards is the one that gets past that refusal.
    """
    token = request_id.set("ok-1\r\nX-Injected: yes" + "z" * 200)
    try:
        with banking_answering(body={"contacts": []}) as (base_url, recorded):
            await HttpContacts(base_url=base_url, timeout_seconds=5.0).all_of(ACCOUNT)
    finally:
        request_id.reset(token)

    # The separators are gone, so there is one request and one header; the tail is cut at the bound.
    assert recorded.only_id == "ok-1X-Injectedyes" + "z" * 47
    assert len(recorded.only_id or "") == seam.LIMIT


# --- a call with no request behind it ----------------------------------------------------


def test_the_context_variable_still_reads_as_the_value_the_seam_calls_unset() -> None:
    """Pinned, because a `ContextVar` does not expose its default and the seam has to restate it.

    If `observability` ever changes it, this fails here rather than the seam silently forwarding a
    new placeholder as though it were an identifier.
    """
    # A fresh, empty context, so this reads the declared default and not whatever ran before it.
    assert contextvars.Context().run(request_id.get) == seam.UNSET


async def test_a_call_with_no_request_behind_it_sends_no_id_at_all() -> None:
    """A background task or a startup probe, and the decision this seam makes about it.

    `request_id` reads `-` when nothing set it. Forwarding that would look like an identifier and
    correlate nothing: banking honours this header the way this service does, so `-` survives its
    filter too and every unattended call in the estate would arrive claiming to be the same request.
    Omitting it makes banking generate a fresh one — which is what `request_log.py` already says
    about the inbound case, that an absent header "is not a reason to have nothing to correlate by".

    A false correlation is worse than a missing one. Only one of the two is visible to the person
    reading the logs while it misleads them.
    """
    with banking_answering(body={"contacts": []}) as (base_url, recorded):
        await HttpContacts(base_url=base_url, timeout_seconds=5.0).all_of(ACCOUNT)

    assert recorded.only_id is None


async def test_an_id_that_survives_nothing_of_the_bound_is_not_sent_either() -> None:
    """Same case, differently spelled. A value made entirely of characters the filter drops is no
    more of an identifier than the default is, and an empty header would be the same lie."""
    token = request_id.set("!!!@@@###")
    try:
        with banking_answering(body={"contacts": []}) as (base_url, recorded):
            await HttpContacts(base_url=base_url, timeout_seconds=5.0).all_of(ACCOUNT)
    finally:
        request_id.reset(token)

    assert recorded.only_id is None


# --- no client can be built without the seam ---------------------------------------------

BANKING = Path(seam.__file__).parent
ADAPTERS = sorted(p for p in BANKING.glob("*.py") if p.name not in ("__init__.py", "client.py"))


def test_the_glob_found_the_adapters() -> None:
    """The list is discovered, not written down, so an eighth client joins the rule below by
    existing rather than by somebody remembering. This guards the discovery itself: a glob that
    matched nothing would make every parametrised test under it pass by running zero times.

    A floor rather than an exact count, because an eighth client is a thing somebody is allowed to
    add. What it is not allowed to do is skip the seam, and that is the next test's business.
    """
    assert len(ADAPTERS) >= 7, [p.name for p in ADAPTERS]


@pytest.mark.parametrize("source", ADAPTERS, ids=lambda p: p.stem)
def test_no_adapter_builds_a_client_of_its_own(source: Path) -> None:
    """The defect, stated as a rule a new file cannot be written around.

    This is what the end-to-end test cannot say by itself: it proves one path carries the id, not
    that every path must. Ten call sites built their own client and adding a header to nine of them
    would have looked exactly as green.

    Parsed rather than grepped, so the words `httpx.AsyncClient` in a docstring or a comment are not
    a failure — this file's own prose would be one — and both spellings of the call are: through the
    module, and bare after a `from httpx2 import AsyncClient`.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    built = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "AsyncClient")
            or (isinstance(node.func, ast.Name) and node.func.id == "AsyncClient")
        )
    ]

    assert built == [], (
        f"{source.name} line {built[0].lineno if built else '?'} builds its own AsyncClient. "
        f"Every outbound call to banking goes through banking_client() in {seam.__name__}, which "
        f"is the only place the request id — and later the span — can be attached."
    )


# --- and every method of every one really reaches the wire through it --------------------

Call = Callable[[str], Awaitable[object]]

# Every method of the seven that reaches banking: twelve, over the ten places a client is built —
# the payment client's three share one `_post`, and the query client has three of its own including
# the readiness probe. Written out because the point is coverage of the *methods*, which no glob can
# discover: importing the factory and forgetting to call it on one method is exactly the mistake the
# file-level rule above cannot see.
CALLS: dict[str, Call] = {
    "accounts.all_of": lambda url: HttpAccounts(base_url=url, timeout_seconds=5.0).all_of(ACCOUNT),
    "account_summary.of": lambda url: HttpAccountSummary(
        base_url=url, timeout_seconds=5.0, days=30, movements=10
    ).of(ACCOUNT, today=TODAY, reporting_timezone=ZONE),
    "cards.all_of": lambda url: HttpCards(base_url=url, timeout_seconds=5.0).all_of(
        ACCOUNT, since=date(2026, 7, 5), today=TODAY, timezone=ZONE
    ),
    "cards.charges_on": lambda url: HttpCards(base_url=url, timeout_seconds=5.0).charges_on(
        ACCOUNT, CARD, since=date(2026, 7, 5), today=TODAY, timezone=ZONE, limit=50
    ),
    "contacts.all_of": lambda url: HttpContacts(base_url=url, timeout_seconds=5.0).all_of(ACCOUNT),
    "financial_query.run": lambda url: HttpFinancialQuery(
        base_url=url, timeout_seconds=5.0, supporting_movements=10
    ).run(
        FinancialPlan(
            direction=Direction.ALL,
            period=RelativePeriod(period=Period.LAST_N_DAYS, n=30),
            group_by=GroupBy.NONE,
            metric=Metric.SUM,
        ),
        account_id=ACCOUNT,
        today=TODAY,
        reporting_timezone=ZONE,
    ),
    "financial_query.ranked": lambda url: HttpFinancialQuery(
        base_url=url, timeout_seconds=5.0, supporting_movements=10
    ).ranked(
        TopMovements(
            direction=Direction.EXPENSE,
            period=RelativePeriod(period=Period.LAST_N_DAYS, n=30),
            rank=1,
        ),
        account_id=ACCOUNT,
        today=TODAY,
        reporting_timezone=ZONE,
    ),
    # The one call site that does not take the deployment's timeout. It would have been the one a
    # factory reading `self.timeout_seconds` quietly changed, so it is driven like the rest.
    "financial_query.ping": lambda url: HttpFinancialQuery(
        base_url=url, timeout_seconds=5.0, supporting_movements=10
    ).ping(),
    "movement_categories.set": lambda url: HttpMovementCategories(
        base_url=url, timeout_seconds=5.0
    ).set(ACCOUNT, MOVEMENT, "GROCERIES"),
    "payments.prepare": lambda url: HttpPayments(base_url=url, timeout_seconds=5.0).prepare(
        PaymentRequest(recipient_name="Marta", amount_minor=1000, currency="EUR"),
        account_id=ACCOUNT,
        key="idem-key-1",
    ),
    "payments.confirm": lambda url: HttpPayments(base_url=url, timeout_seconds=5.0).confirm(
        account_id=ACCOUNT, intent_id=INTENT
    ),
    "payments.cancel": lambda url: HttpPayments(base_url=url, timeout_seconds=5.0).cancel(
        account_id=ACCOUNT, intent_id=INTENT
    ),
}


def test_every_adapter_has_its_calls_driven_here() -> None:
    """The half of "an eighth client cannot get this wrong" that no glob can do.

    The rule above reads files and so finds a new client on its own. It cannot find a new *method*:
    a client that imports the factory, uses it on three calls and builds a raw one on the fourth
    passes every file-level check there is. So the methods are written out, and this fails when a
    client appears with none of them driven — which is the moment somebody is already editing this
    file and can add them.
    """
    driven = {key.split(".")[0] for key in CALLS}
    found = {source.stem.removeprefix("http_") for source in ADAPTERS}

    assert found - driven == set(), f"no outbound call is driven for {sorted(found - driven)}"
    assert driven - found == set(), f"driving a client that no longer exists: {driven - found}"


@pytest.mark.parametrize("call", CALLS.values(), ids=list(CALLS))
async def test_every_outbound_call_carries_the_request_id(call: Call) -> None:
    """Every method of every client, driven for real with an id in the context.

    The answer is a bare `{}`, which most of these refuse — deliberately. What is under test is the
    request that went out, not the body that came back, and the error ladders each of these owns are
    thoroughly tested elsewhere. Suppressing the refusal keeps this test about the seam.
    """
    token = request_id.set("turn-0042")
    try:
        with banking_answering() as (base_url, recorded):
            with suppress(BankingUnavailableError, QueryRejectedError):
                await call(base_url)
    finally:
        request_id.reset(token)

    assert recorded.request_ids, "the call never reached the wire"
    assert recorded.request_ids == ["turn-0042"] * len(recorded.request_ids)


async def test_a_payment_still_carries_its_idempotency_key_beside_the_id() -> None:
    """The header that was already there keeps working exactly as it did.

    `prepare` passes `Idempotency-Key` per request while the seam sets the id per request too, and
    the wrong shape here — a client-level `headers=` that a call site's own `headers=` replaced
    wholesale — would have silently dropped the one header in this service that stops a payment
    being made twice. This is the assertion that says a single factory was the right shape.
    """
    token = request_id.set("turn-0042")
    try:
        with banking_answering() as (base_url, recorded):
            with suppress(BankingUnavailableError, QueryRejectedError):
                await HttpPayments(base_url=base_url, timeout_seconds=5.0).prepare(
                    PaymentRequest(recipient_name="Marta", amount_minor=1000, currency="EUR"),
                    account_id=ACCOUNT,
                    key="idem-key-1",
                )
    finally:
        request_id.reset(token)

    assert recorded.request_ids == ["turn-0042"]
    assert recorded.idempotency_keys == ["idem-key-1"]


# --- the two ends are the same header ----------------------------------------------------


def test_the_header_forwarded_is_the_header_honoured() -> None:
    """Imported, not restated. Two literals could drift and the end-to-end test above would keep
    passing against a name banking no longer reads."""
    assert seam.HEADER is HEADER


async def test_the_factory_does_not_flatten_the_timeout_it_is_given() -> None:
    """The seven were not uniform, and this is where that mattered.

    Nine call sites pass the deployment's `banking_timeout_seconds`; `HttpFinancialQuery.ping`
    passes a short fixed one, because a readiness probe that waits as long as a real query is not a
    readiness probe. A factory that reached for `self.timeout_seconds` instead of taking a parameter
    would have made the seven look uniform by quietly making the tenth wrong.
    """
    async with banking_client(timeout=2.0) as probe, banking_client(timeout=30.0) as query:
        assert probe.timeout.read == 2.0
        assert query.timeout.read == 30.0
