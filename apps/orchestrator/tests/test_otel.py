"""The exporter, proved with nothing listening.

**Why an in-memory exporter and not a collector.** A collector that is down and an adapter that
emits nothing look identical from the application: no error, no log line, no failed request — the
SDK batches, retries and drops. So a test that needs the stack up cannot tell the two apart, and a
test that does not need it can. `InMemorySpanExporter` and `InMemoryMetricReader` ship with the SDK
for exactly this, and every assertion below runs with nothing on 4317 or 4318.

**The property these tests exist to hold.** Recording is unconditional; exporting is optional. Six
times in the last week this repository found an instrument that was green because it had stopped
executing the code it measured, and an adapter that quietly switched off the decisions it exports
would be the seventh. `test_a_turn_is_recorded_with_tracing_off` is the one that would catch that,
and it drives a real turn through the real routes to do it.
"""

import json
import logging
import uuid
from datetime import date

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import Counter as SdkCounter
from opentelemetry.sdk.metrics import Histogram as SdkHistogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    InMemoryMetricReader,
)
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.bootstrap.config import Settings
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.banking import client as banking_client
from finguard_orchestrator.infrastructure.http.dependencies import (
    authenticated_caller,
    reporting_today,
)
from finguard_orchestrator.infrastructure.telemetry import otel
from finguard_orchestrator.observability import logging as service_logging
from finguard_orchestrator.observability.decisions import (
    Consultation,
    Decision,
    Step,
    TurnRecord,
    Verdict,
    record,
)
from finguard_orchestrator.observability.logging import JsonLines

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)

# The same sentence `test_logging.py` uses, for the same reason: what somebody typed, who they pay
# and the note they wrote about it, all in one string that can be searched for afterwards.
PRIVATE = "¿cuánto le he pagado a Marta Solís Vega por el abogado en los últimos 3 meses?"


class Exported:
    """A tracer and a meter that keep everything, so a test can read what would have been sent."""

    def __init__(self, *, readers: bool = True) -> None:
        self.spans = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.reader = InMemoryMetricReader()
        meters = MeterProvider(metric_readers=[self.reader])
        self.telemetry = otel.TurnTelemetry.build(
            provider.get_tracer("test"), meters.get_meter("test"), readers=readers
        )

    def turn(self, sealed: TurnRecord) -> None:
        self.telemetry.turn(sealed, started_at=1_000_000_000, ended_at=1_000_500_000)

    def span(self, name: str):
        found = [span for span in self.spans.get_finished_spans() if span.name == name]
        assert found, f"no {name!r} span in {[s.name for s in self.spans.get_finished_spans()]}"
        return found[-1]

    def points(self, metric: str) -> list:
        collected = []
        for resource in self.reader.get_metrics_data().resource_metrics:
            for scope in resource.scope_metrics:
                for measured in scope.metrics:
                    if measured.name == metric:
                        collected.extend(measured.data.data_points)
        return collected


@pytest.fixture
def exported() -> Exported:
    return Exported()


def overrode_the_window() -> Decision:
    return Decision(
        step=Step.ENFORCE,
        verdict=Verdict.OVERRODE,
        changed=True,
        field="window",
        was="LAST_N_MONTHS:5",
        now="LAST_N_MONTHS:3",
    )


def claimed_the_turn() -> Decision:
    """What the `recogniser` seam records, unchanged by anything below. A reader that *declines*
    still records nothing here — that is the counter's shape and it is deliberately untouched."""
    return Decision(
        step=Step.RECOGNISER, verdict=Verdict.CLAIMED, changed=True, detail="_ratio_request"
    )


# --- a record becomes spans, events and counters ------------------------------------------


def test_a_record_becomes_a_turn_span_with_a_span_per_decision(exported: Exported) -> None:
    """The shape the stack was proved with: a `turn`, a step-named child carrying step, verdict and
    changed, and a `decision` event carrying field, was and now."""
    exported.turn(TurnRecord(decisions=(overrode_the_window(),), outcome="PLANNED"))

    turn = exported.span("turn")
    enforce = exported.span("explicit_facts.enforce")
    assert enforce.parent is not None
    assert enforce.parent.span_id == turn.context.span_id, "a decision belongs to its turn"
    assert enforce.attributes["step"] == "explicit_facts.enforce"
    assert enforce.attributes["verdict"] == "overrode"
    assert enforce.attributes["changed"] is True
    (event,) = enforce.events
    assert event.name == "decision"
    assert dict(event.attributes) == {
        "field": "window",
        "was": "LAST_N_MONTHS:5",
        "now": "LAST_N_MONTHS:3",
    }


def test_the_turn_span_carries_the_outcome_and_the_headline(exported: Exported) -> None:
    exported.turn(TurnRecord(decisions=(overrode_the_window(),), outcome="PLANNED"))

    turn = exported.span("turn")
    assert turn.attributes["outcome"] == "PLANNED"
    assert turn.attributes["decisions"] == 1
    assert turn.attributes["changed"] is True, "did anything this turn did alter the answer"
    assert turn.end_time > turn.start_time, "the turn has a real duration, unlike its decisions"


def test_a_counter_is_incremented_from_the_same_record(exported: Exported) -> None:
    """The whole reason both come from one record: a rate and a waterfall cannot disagree if the
    span and the increment are produced by the same loop over the same decisions."""
    exported.turn(TurnRecord(decisions=(overrode_the_window(),), outcome="PLANNED"))

    (point,) = exported.points(otel.DECISIONS)
    assert point.value == 1
    assert dict(point.attributes) == {
        "step": "explicit_facts.enforce",
        "verdict": "overrode",
        "changed": True,
    }
    (turns,) = exported.points(otel.TURNS)
    assert dict(turns.attributes) == {"outcome": "PLANNED"}


def test_the_field_is_not_a_metric_label(exported: Exported) -> None:
    """`step` and `verdict` are enums because they are labels; `field` is a string a call site types
    by hand, so it stays on the span where cardinality costs nothing."""
    exported.turn(TurnRecord(decisions=(overrode_the_window(),), outcome="PLANNED"))

    (point,) = exported.points(otel.DECISIONS)
    assert "field" not in dict(point.attributes)
    assert exported.span("explicit_facts.enforce").attributes["field"] == "window"


def test_one_step_deciding_about_three_fields_is_three_spans(exported: Exported) -> None:
    """The departure from "one span per step", and the reason for it. `explicit_facts.enforce`
    records one decision per field with a verdict each, so a single span for the step would need a
    step-level verdict the record does not have — and any value put there would be an aggregate
    this adapter invented. One decision, one span, one increment."""
    fields = ("window", "metric", "group_by")
    decisions = tuple(
        Decision(
            step=Step.ENFORCE,
            verdict=verdict,
            changed=verdict is Verdict.OVERRODE,
            field=name,
        )
        for name, verdict in zip(
            fields, (Verdict.OVERRODE, Verdict.AGREED, Verdict.AGREED), strict=True
        )
    )

    exported.turn(TurnRecord(decisions=decisions, outcome="PLANNED"))

    spans = [s for s in exported.spans.get_finished_spans() if s.name == "explicit_facts.enforce"]
    assert len(spans) == 3
    assert {s.attributes["field"] for s in spans} == set(fields)
    # And the counter splits the same way, which is what makes the two agree by construction.
    assert {(p.attributes["verdict"], p.value) for p in exported.points(otel.DECISIONS)} == {
        ("overrode", 1),
        ("agreed", 2),
    }


def test_an_absent_slot_is_absent_rather_than_empty(exported: Exported) -> None:
    """A whole-turn decision has no field. Recording it as "" would claim a value was lost."""
    exported.turn(
        TurnRecord(
            decisions=(Decision(step=Step.OUTCOME, verdict=Verdict.AGREED, changed=False),),
            outcome="PLANNED",
        )
    )

    span = exported.span("outcome")
    assert "field" not in span.attributes
    assert dict(span.events[0].attributes) == {}


# --- the exemplar, which is the fourth thing the link needs --------------------------------


def test_the_counter_carries_the_trace_context_of_its_decision(exported: Exported) -> None:
    """**The handoff.** An exemplar is attached by reading the trace context that is current when
    `add` is called. Move the increment outside the decision's span and everything still looks
    right — the counter counts, the span exports, the rate draws — and the jump from a panel to the
    turn's waterfall silently becomes a dead link. Nothing logs anything. This is the assertion
    that fails instead."""
    exported.turn(TurnRecord(decisions=(overrode_the_window(),), outcome="PLANNED"))

    span = exported.span("explicit_facts.enforce")
    (point,) = exported.points(otel.DECISIONS)
    (exemplar,) = point.exemplars
    assert exemplar.trace_id == span.context.trace_id
    assert exemplar.span_id == span.context.span_id, "the decision's own span, not the turn's"


def test_the_outcome_counter_carries_one_too(exported: Exported) -> None:
    """Outcome mix is a panel section 6 wants a waterfall one click from, like the others."""
    exported.turn(TurnRecord(decisions=(overrode_the_window(),), outcome="PLANNED"))

    (point,) = exported.points(otel.TURNS)
    (exemplar,) = point.exemplars
    assert exemplar.trace_id == exported.span("turn").context.trace_id


# --- the two silent failures the collector cannot report -------------------------------------


def test_cumulative_temporality_survives_the_environment(monkeypatch) -> None:
    """`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta` makes every counter vanish: there
    is no `deltatocumulative` processor in the collector and `prometheusremotewrite` handles
    cumulative sums, so delta points are dropped downstream with no error at either end. Stating
    the preference makes the variable inert, and this is what says so."""
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "delta")

    inherited = OTLPMetricExporter(endpoint="http://127.0.0.1:4318/v1/metrics")
    stated = OTLPMetricExporter(
        endpoint="http://127.0.0.1:4318/v1/metrics",
        preferred_temporality=otel._CUMULATIVE,
        preferred_aggregation=otel._EXPLICIT_BUCKETS,
    )

    assert inherited._preferred_temporality[SdkCounter] is AggregationTemporality.DELTA, (
        "the variable really does flip it, so this test is measuring something"
    )
    assert stated._preferred_temporality[SdkCounter] is AggregationTemporality.CUMULATIVE


def test_histograms_stay_explicit_bucket(monkeypatch) -> None:
    """Exponential histograms need `--enable-feature=native-histograms` on Prometheus, which
    compose.yaml does not set — so one is accepted by the collector and dropped by Prometheus.
    Nothing here records a histogram, but the FastAPI instrumentation records `http.server.duration`
    and it goes through this exporter."""
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_METRICS_DEFAULT_HISTOGRAM_AGGREGATION",
        "base2_exponential_bucket_histogram",
    )

    stated = OTLPMetricExporter(
        endpoint="http://127.0.0.1:4318/v1/metrics",
        preferred_temporality=otel._CUMULATIVE,
        preferred_aggregation=otel._EXPLICIT_BUCKETS,
    )

    assert isinstance(
        stated._preferred_aggregation[SdkHistogram], ExplicitBucketHistogramAggregation
    )


# --- off by default, and recording regardless -------------------------------------------------


def test_tracing_is_off_unless_a_deployment_says_otherwise() -> None:
    """A test run and a developer's laptop must not need a collector."""
    assert Settings.model_fields["otel_enabled"].default is False


def test_configure_tracing_does_nothing_at_all_when_disabled() -> None:
    """Not "sets up an SDK that exports to nowhere". Nothing: no provider, no exporter thread, and
    neither of the two seams it injects into modules that must not import OpenTelemetry."""
    settings = Settings(otel_enabled=False)  # type: ignore[call-arg]

    assert otel.configure_tracing(app, settings=settings) is False
    assert service_logging._trace_context is service_logging._untraced
    assert banking_client._transport is banking_client._untraced


def test_nothing_is_exported_when_no_telemetry_is_configured(monkeypatch) -> None:
    """`export_turn` is called on every request whether or not anything is listening. With nothing
    configured it must return quietly rather than raise, because it runs in a `finally` during
    response teardown."""
    monkeypatch.setattr(otel, "_telemetry", None)

    otel.export_turn(
        TurnRecord(decisions=(overrode_the_window(),)), started_at=1, ended_at=2
    )  # no exception is the assertion


def test_a_turn_that_decided_nothing_is_not_a_span(exported: Exported, monkeypatch) -> None:
    """Every HTTP request opens a recorder, including `/health` and a browser's preflight. A `turn`
    span per readiness poll would bury the turns anybody wants to read."""
    monkeypatch.setattr(otel, "_telemetry", exported.telemetry)

    otel.export_turn(TurnRecord(), started_at=1, ended_at=2)

    assert exported.spans.get_finished_spans() == ()


def test_an_exporter_that_raises_does_not_take_down_the_turn(monkeypatch) -> None:
    """A bookkeeping call that can abort a bank turn has made the service less reliable in exchange
    for watching it, which is the trade this design refuses on both sides of the seam."""

    class Broken:
        def turn(self, *args, **kwargs):
            raise RuntimeError("the collector ate it")

    monkeypatch.setattr(otel, "_telemetry", Broken())

    otel.export_turn(TurnRecord(decisions=(overrode_the_window(),)), started_at=1, ended_at=2)


# --- the property that matters most ------------------------------------------------------------


class RecordingGraph:
    """A graph that records a decision and hands the turn on.

    The point of the tests below is the middleware and the record, not the graph's nodes -- so
    this is the smallest thing that puts a real `record()` inside a real turn on a real route.
    """

    async def answer(self, *_: object, **__: object) -> EngineAnswer:
        record(
            Step.PLANNER,
            Verdict.AGREED,
            changed=False,
            field="window",
            now="LAST_N_MONTHS:3",
            detail="fake-model",
        )
        return EngineAnswer(None, DialogueState(uuid.UUID(int=1), version=1), "CHART", 0, {})


@pytest.fixture(autouse=True)
def _restore():
    app.dependency_overrides.clear()
    previous = getattr(app.state, "engine", None)
    yield
    app.state.engine = previous
    app.dependency_overrides.clear()


def wired() -> TestClient:
    app.state.engine = RecordingGraph()
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
    )
    app.state.questions_enabled = True
    return TestClient(app, headers={"Idempotency-Key": "otel-attempt"})


def test_a_turn_is_recorded_with_tracing_off(monkeypatch) -> None:
    """**The property this whole design is built on.** With `otel_enabled` false — the default, and
    what this suite runs under — the decisions of a live turn are still recorded and still
    assertable. Only the export is optional.

    Six times in the last week this repository found an instrument that was green because it had
    stopped executing the code it measured. An adapter whose settings default switched off the
    thing it exports would be the seventh, and this is the test that would catch it.
    """
    sealed: list[TurnRecord] = []
    monkeypatch.setattr(otel, "_telemetry", None, raising=False)
    monkeypatch.setattr(
        otel, "export_turn", lambda record, **kwargs: sealed.append(record), raising=True
    )

    wired().post("/v1/questions", json={"question": "¿cuánto gasté en los últimos 3 meses?"})

    assert sealed, "no turn was recorded at all"
    (planned,) = sealed[-1].where(Step.PLANNER)
    assert planned.verdict is Verdict.AGREED
    assert planned.detail == "fake-model"
    assert otel._telemetry is None, "and nothing was exporting while that happened"


def test_the_turn_survives_the_streaming_route(monkeypatch) -> None:
    """The reason the boundary is a raw ASGI middleware. `BaseHTTPMiddleware` hands control back as
    soon as the response *starts*, so on a streamed answer it would seal the record before the
    pipeline had made a single decision — an exporter that is perfectly wired and always empty."""
    sealed: list[TurnRecord] = []
    monkeypatch.setattr(
        otel, "export_turn", lambda record, **kwargs: sealed.append(record), raising=True
    )

    with wired().stream(
        "POST", "/v1/questions/stream", json={"question": "¿cuánto gasté en los últimos 3 meses?"}
    ) as streamed:
        streamed.read()

    assert sealed, "the streaming route recorded no turn"
    assert sealed[-1].where(Step.PLANNER), "the record was sealed before the pipeline decided"


def test_nothing_the_holder_wrote_reaches_a_span(exported: Exported, monkeypatch) -> None:
    """The constraint every section inherits, checked on the way out rather than only on the way
    in. `decisions` scrubs what it records; this asserts the adapter adds nothing back."""
    sealed: list[TurnRecord] = []
    monkeypatch.setattr(
        otel, "export_turn", lambda record, **kwargs: sealed.append(record), raising=True
    )

    wired().post("/v1/questions", json={"question": PRIVATE})

    exported.turn(sealed[-1])
    emitted = "\n".join(span.to_json() for span in exported.spans.get_finished_spans())
    for secret in ("Marta", "Solís", "abogado", "pagado", str(ACCOUNT)):
        assert secret not in emitted, f"{secret!r} reached a span"


# --- a log line and a trace find each other ----------------------------------------------------


@pytest.fixture
def untraced():
    """Restores the module-level slot, which is process-wide state a test must put back."""
    yield
    service_logging.use_trace_context(service_logging._untraced)


def test_a_log_line_carries_the_trace_it_was_emitted_inside(untraced) -> None:
    """So getting from a line to the turn's waterfall is a copy and a paste, not a second
    correlation scheme somebody has to maintain."""
    provider = TracerProvider()
    service_logging.use_trace_context(otel._current_trace_context)
    handler = logging.StreamHandler()
    filtered = service_logging.TraceContext()

    with provider.get_tracer("test").start_as_current_span("turn") as span:
        line = logging.LogRecord("x", logging.INFO, "", 0, "turn", None, None)
        filtered.filter(line)

    emitted = json.loads(JsonLines().format(line))
    assert emitted["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert emitted["span_id"] == format(span.get_span_context().span_id, "016x")
    assert len(emitted["trace_id"]) == 32, "the width Tempo's search box expects"
    handler.close()


# --- the outbound seam ------------------------------------------------------------------------


class Answering(httpx.AsyncBaseTransport):
    """A transport that answers without a socket, so the span can be read without banking."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.seen: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen = request
        return httpx.Response(self.status, request=request)


async def call_through(transport: httpx.AsyncBaseTransport, url: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport) as client:
        return await client.get(url)


async def test_the_outbound_span_names_the_endpoint_and_no_account(exported: Exported) -> None:
    """Banking's URLs are `/accounts/<uuid>/movements`, so the obvious `url.full` attribute would
    put a raw account identifier on every outbound span — the exact value `account_ref()` exists to
    keep out of every record."""
    inner = Answering()
    traced = otel._TracedTransport(inner, exported.telemetry.tracer)

    await call_through(traced, f"http://banking.test/accounts/{ACCOUNT}/movements?since=2026-01-01")

    span = exported.span("GET /accounts/{id}/movements")
    assert span.attributes["url.template"] == "/accounts/{id}/movements"
    assert span.attributes["server.address"] == "banking.test"
    assert span.attributes["http.response.status_code"] == 200
    assert str(ACCOUNT) not in span.to_json()
    assert "2026-01-01" not in span.to_json(), "and the query string does not travel either"


async def test_the_outbound_call_carries_the_trace_across_to_banking(exported: Exported) -> None:
    """The span propagation half of the promise `request_log` makes about following a request from
    the web client to here to banking. The request id half was already kept by this factory."""
    inner = Answering()
    traced = otel._TracedTransport(inner, exported.telemetry.tracer)

    await call_through(traced, "http://banking.test/accounts")

    assert inner.seen is not None
    assert "traceparent" in inner.seen.headers
    assert exported.span("GET /accounts").context.trace_id == int(
        inner.seen.headers["traceparent"].split("-")[1], 16
    )


async def test_a_refusal_from_banking_is_not_an_error_span(exported: Exported) -> None:
    """A 404 from the account directory is how "that account is not yours" is spelled on this wire.
    Painting the ordinary refusal path red teaches everybody to ignore the colour."""
    traced = otel._TracedTransport(Answering(404), exported.telemetry.tracer)

    await call_through(traced, "http://banking.test/accounts")

    assert exported.span("GET /accounts").status.is_ok


async def test_a_broken_connection_is_still_a_span(exported: Exported) -> None:
    """The call worth seeing most, and the one a `response` event hook never runs for — which is
    why the span is a transport and not the hook pair the factory also offered."""

    class Refusing(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nothing is listening", request=request)

    traced = otel._TracedTransport(Refusing(), exported.telemetry.tracer)

    with pytest.raises(httpx.ConnectError):
        await call_through(traced, "http://banking.test/accounts")

    span = exported.span("GET /accounts")
    assert not span.status.is_ok
    assert span.status.description == "ConnectError"


async def test_a_failure_records_its_type_and_not_its_message(exported: Exported) -> None:
    """The SDK's defaults would attach the exception's message and stack trace to the span. An
    httpx error's message is written by httpx and can quote the URL it was handed — and that URL
    is the one thing here that had to be templated, because banking's paths carry raw account
    identifiers. So the type is recorded and nothing else."""

    class Refusing(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach /accounts/{ACCOUNT}", request=request)

    traced = otel._TracedTransport(Refusing(), exported.telemetry.tracer)

    with pytest.raises(httpx.ConnectError):
        await call_through(traced, f"http://banking.test/accounts/{ACCOUNT}")

    span = exported.span("GET /accounts/{id}")
    assert span.events == (), "no exception event, which is where the message and the trace go"
    assert str(ACCOUNT) not in span.to_json()


def test_the_client_factory_is_unchanged_when_nothing_installed_a_transport() -> None:
    """The promise `banking/client.py` made about this seam: neither shape reaches a call
    site, so with nothing installed the factory returns exactly the client it always did."""
    assert banking_client._transport is banking_client._untraced

    built = banking_client.banking_client(timeout=1.0)

    assert isinstance(built._transport, httpx.AsyncHTTPTransport)


def test_the_templated_path_only_replaces_identifiers() -> None:
    """Mechanical, and the same exact rule `decisions._own_words` applies to the same value: a
    segment that parses as a UUID goes, and nothing else does."""
    assert otel._templated(f"/accounts/{ACCOUNT}/movements") == "/accounts/{id}/movements"
    assert otel._templated("/accounts") == "/accounts"
    assert otel._templated("/cards/visa-gold/charges") == "/cards/visa-gold/charges"


# --- the refusal the design is built on --------------------------------------------------------


def test_the_call_graph_draws_itself_from_the_span_tree(exported: Exported) -> None:
    """**The callback case, as spans.** `_ratio_request` reads a percent-change follow-up by asking
    `_direct_period_comparison`, and before this the two arrived as unrelated rows.

    Nothing in the adapter, the decorator or the record names either function. The inner span is a
    child because the inner consultation is, and the inner consultation is one because its frame
    opened while the outer one was still on the stack. The graph is not computed anywhere.
    """
    inner = Consultation(
        reader="_direct_period_comparison", claimed=True, started_at=1_100, ended_at=1_400
    )
    outer = Consultation(
        reader="_ratio_request", claimed=True, started_at=1_000, ended_at=1_500, consulted=(inner,)
    )

    exported.turn(
        TurnRecord(decisions=(claimed_the_turn(),), consulted=(outer,), outcome="PLANNED")
    )

    turn = exported.span("turn")
    ratio = exported.span("_ratio_request")
    comparison = exported.span("_direct_period_comparison")
    assert ratio.parent is not None and ratio.parent.span_id == turn.context.span_id
    assert comparison.parent is not None
    assert comparison.parent.span_id == ratio.context.span_id, "the callback nests under its caller"
    assert ratio.attributes["claimed"] is True
    assert comparison.attributes["claimed"] is True
    assert ratio.end_time > ratio.start_time, "a consultation is a call and has a duration"


def test_a_reader_that_declined_is_a_span_and_never_a_count(exported: Exported) -> None:
    """**The line this change is careful about.** Eighteen "not mine" rows a turn would bury the
    one that matters in a counter, and telling them apart would need the reader's name as a label —
    unbounded cardinality by the one route `Step` and `Verdict` do not cover. A trace has neither
    problem and there the order is the finding. So the path is drawn and never counted."""
    declined = Consultation(reader="_gambling_plan", claimed=False, started_at=1, ended_at=2)

    exported.turn(TurnRecord(decisions=(claimed_the_turn(),), consulted=(declined,)))

    assert exported.span("_gambling_plan").attributes["claimed"] is False
    # One point, from the one `CLAIMED` decision. The declining reader contributed nothing.
    (point,) = exported.points(otel.DECISIONS)
    assert point.value == 1
    assert dict(point.attributes) == {"step": "recogniser", "verdict": "claimed", "changed": True}


def test_the_setting_turns_the_path_down_and_leaves_everything_else(exported: Exported) -> None:
    """`otel_recogniser_spans=False`. The cost is about nineteen spans a turn, so it has to be
    possible to stop paying it without a redesign — and stopping must cost nothing else: the
    decision spans, the counters and the exemplars are what a live dashboard is built on."""
    consulted = (Consultation(reader="_gambling_plan", claimed=False, started_at=1, ended_at=2),)
    record = TurnRecord(decisions=(overrode_the_window(),), consulted=consulted, outcome="PLANNED")
    quiet = Exported(readers=False)

    exported.turn(record)
    quiet.turn(record)

    assert [span.name for span in quiet.spans.get_finished_spans()] == [
        "explicit_facts.enforce",
        "turn",
    ]
    assert len(exported.spans.get_finished_spans()) == 3, "the reader span is the difference"
    for both in (exported, quiet):
        (point,) = both.points(otel.DECISIONS)
        assert point.value == 1 and len(point.exemplars) == 1
        assert [dict(p.attributes) for p in both.points(otel.TURNS)] == [{"outcome": "PLANNED"}]


def test_the_turn_span_says_which_conversation_it_belongs_to(exported: Exported) -> None:
    """Gap one, as the attribute a Tempo search reads. On the `turn` span and nowhere else: one
    trace is one turn, so `{ span.conversation.id = "…" }` returns every turn of one conversation,
    and repeating it on seventeen decision spans would multiply one fact and buy nothing."""
    identifier = str(uuid.uuid4())

    exported.turn(TurnRecord(decisions=(overrode_the_window(),), conversation=identifier))

    assert exported.span("turn").attributes["conversation.id"] == identifier
    assert "conversation.id" not in exported.span("explicit_facts.enforce").attributes


def test_only_one_module_imports_opentelemetry() -> None:
    """No OpenTelemetry import outside `telemetry/otel.py` is the first thing this design
    refuses, and the one that stops being true quietly: the domain and the application record
    decisions, one adapter exports them. A grep is the only control that scales to the next person
    who wants a span in a handler.

    Import statements rather than the word, because two of the files that must *not* import it
    explain at length why they do not — `banking/client.py` names the instrumentation package it
    cannot use, and a grep for the string would fail on the comment that keeps the rule.
    """
    import re
    from pathlib import Path

    statement = re.compile(r"^\s*(?:from|import)\s+opentelemetry", re.MULTILINE)
    source = Path(__file__).parents[1] / "src" / "finguard_orchestrator"
    importing = sorted(
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if statement.search(path.read_text(encoding="utf-8"))
    )

    assert importing == ["infrastructure/telemetry/otel.py"]
