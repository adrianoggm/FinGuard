"""The one module in this service that imports OpenTelemetry.

`decisions` records and this exports. The split is the whole point of the design and it is not
stylistic: the application and the domain call `record()` unconditionally, and whether anything is
listening is settled here, once, at the composition root. Six times in the last week this
repository found an instrument that was green because it had stopped executing the code it
measured, and every one of them had the same shape — the configuration that makes the
instrumentation run is exactly the configuration no test has. So:

    **recording is unconditional; exporting is optional.**

`register_turn_recording` is called always. `configure_tracing` returns immediately when
`otel_enabled` is false, which is the default, so a test run and a developer's laptop need no
collector. What that costs when tracing is off is one `TurnRecorder` per HTTP request and a
`ContextVar` set — and what it buys is that the decisions are still recorded and still assertable
when nothing is exporting them. `tests/test_otel.py` holds both halves.

**Where the turn boundary is, and why it is here rather than in the handler.** `recording_turn`
says to set and reset in the frame that owns the turn and never inside an async generator, because
generators have no context of their own: a `set` inside one leaks into whichever frame resumed it,
and an abandoned generator's `finally` runs `reset` in a different context and raises. The
handler's `stream()` *is* an async generator, consumed inside a `StreamingResponse` — so the
handler is precisely the place the boundary must not go. A raw ASGI middleware is the outermost
frame that is still one task per request: `await self.app(...)` returns only once the response body
has been produced, streaming included, so the whole turn is inside it. It has to be a raw ASGI
middleware and not `@app.middleware("http")`: `BaseHTTPMiddleware` returns as soon as the response
*starts*, which for an SSE answer is before a single decision has been made.

**Nothing the holder wrote leaves this service, and that rule reaches spans.** `decisions` already
scrubs what it records, so a `Decision` is safe to export as-is. The one place this module adds a
value of its own is the outbound span, where the URL path to banking carries account UUIDs — see
`_templated`. The conversation identifier is the one span attribute that is a UUID on purpose: it
is minted here with `uuid4`, is derived from nothing, and means nothing outside a trace — which is
exactly why `TurnRecord` carries it rather than a `Decision`, whose shape check refuses UUIDs.

**Two things the record grew, and what each becomes.** `TurnRecord.conversation` becomes one
attribute on the `turn` span, so a Tempo search returns every turn of one conversation instead of
one unrelated trace per turn.

**What is deliberately not here.**

* No `spanmetrics` connector in the collector. The counters below are incremented from the same
  `TurnRecord` that produced the spans, one increment per decision, so a rate and a waterfall
  cannot disagree. A connector would derive the rate from the spans instead, which is a second
  source of truth that can drift, and the reason to reach for one is always that the counters are
  fiddly.
* No `opentelemetry-instrumentation-httpx`. This service has `httpx2`, not `httpx`;
  `tests/test_dependency_provenance.py` holds the three facts and `import httpx` raises here. The
  outbound span goes through the client factory Section 4 built for it.
* No delta temporality and no exponential histograms. Both are silent failures against this
  stack — see `_CUMULATIVE` and `_EXPLICIT_BUCKETS`.
"""

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import UUID

import httpx2 as httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.metrics import Counter, Meter
from opentelemetry.propagate import inject
from opentelemetry.sdk.metrics import Counter as SdkCounter
from opentelemetry.sdk.metrics import Histogram as SdkHistogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics import ObservableCounter as SdkObservableCounter
from opentelemetry.sdk.metrics import ObservableGauge as SdkObservableGauge
from opentelemetry.sdk.metrics import ObservableUpDownCounter as SdkObservableUpDownCounter
from opentelemetry.sdk.metrics import UpDownCounter as SdkUpDownCounter
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import Aggregation, ExplicitBucketHistogramAggregation
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from finguard_orchestrator.infrastructure.banking import client as banking_client
from finguard_orchestrator.observability import logging as service_logging
from finguard_orchestrator.observability.decisions import (
    Decision,
    TurnRecord,
    recording_turn,
)

if TYPE_CHECKING:  # pragma: no cover - imported for types only
    from fastapi import FastAPI


class TracingSettings(Protocol):
    """The five settings this adapter reads, so it depends on their shape and not on the
    composition root that owns `Settings`."""

    otel_enabled: bool
    otel_endpoint: str
    build_sha: str
    build_ref: str


logger = logging.getLogger(__name__)

# What this service calls itself, everywhere. Not read from `Settings`: `service.name` is the
# identity a dashboard, a Tempo query and `target_info` all join on, and a deployment that could
# rename it could silently orphan every panel. The version and the ref below are the deployment's;
# the name is the service's.
SERVICE_NAME: Final = "orchestrator"

# The instrumentation scope. The package name, so a span in Tempo says which library emitted it and
# `otel_scope_name` distinguishes these counters from the ones the FastAPI instrumentation emits.
SCOPE: Final = "finguard_orchestrator.observability"

# The counter Section 6's headline panel reads. Named with `_total` already on it deliberately: the
# collector's Prometheus translator strips a trailing `total` token and appends one for monotonic
# sums, so this arrives as `decisions_total` whether or not `add_metric_suffixes` is left at its
# default. Naming it `decisions` would arrive as `decisions_total` today and as `decisions` the day
# somebody turns suffixes off, and the panel would go flat with nothing anywhere reporting an
# error — which is the same failure the collector config's `without_units: true` exists to avoid.
DECISIONS: Final = "decisions_total"

# The outcome mix, same record, same reason it is a counter and not a connector.
TURNS: Final = "turns_total"

# CUMULATIVE, stated rather than inherited. `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`
# is read by the exporter's constructor, and setting it to `delta` makes every counter below
# vanish: there is no `deltatocumulative` processor in ops/observability/otel-collector/config.yaml
# and `prometheusremotewrite` handles cumulative sums, so delta points are dropped downstream with
# no error at either end. Python's default is cumulative, which is why leaving it alone works — but
# "works because nobody set the variable" is not a property, it is a coincidence. The exporter
# applies `preferred_temporality` *after* the environment, so this makes the variable inert.
#
# `dict[type, ...]` rather than the narrower key type mypy infers, because that is the signature
# the exporter declares: the keys are instrument classes it looks up by identity, not a hierarchy.
_CUMULATIVE: Final[dict[type, AggregationTemporality]] = {
    SdkCounter: AggregationTemporality.CUMULATIVE,
    SdkUpDownCounter: AggregationTemporality.CUMULATIVE,
    SdkHistogram: AggregationTemporality.CUMULATIVE,
    SdkObservableCounter: AggregationTemporality.CUMULATIVE,
    SdkObservableUpDownCounter: AggregationTemporality.CUMULATIVE,
    SdkObservableGauge: AggregationTemporality.CUMULATIVE,
}

# Explicit buckets, for the same kind of reason one line up. Nothing in this module records a
# histogram — see the note in `turn` about why the planner-latency one in the design cannot be
# built from the record — but `opentelemetry-instrumentation-fastapi` records `http.server.duration`
# and that is a histogram this stack has to be able to store. Native (exponential) histograms need
# `--enable-feature=native-histograms` on Prometheus, which compose.yaml does not set, so an
# exponential one is accepted by the collector and dropped by Prometheus. The switch is one
# environment variable, `OTEL_EXPORTER_OTLP_METRICS_DEFAULT_HISTOGRAM_AGGREGATION`; stating the
# aggregation here makes that variable inert too.
#
# The boundaries are the turn's, in milliseconds, not the SDK's: its default stops at 10 s, and a
# hosted model's turn that draws a chart takes 25-35 s (two calls and the pacing between them), so
# every such turn landed in the top bucket and P50/P95 read 10 s for a turn three times as long.
# Up to a minute, the provider timeout; anything longer is a failure, not a latency.
HTTP_DURATION_BUCKETS_MS: Final = (
    5,
    10,
    25,
    50,
    100,
    250,
    500,
    1_000,
    2_500,
    5_000,
    7_500,
    10_000,
    15_000,
    20_000,
    30_000,
    45_000,
    60_000,
)
_EXPLICIT_BUCKETS: Final[dict[type, Aggregation]] = {
    SdkHistogram: ExplicitBucketHistogramAggregation(boundaries=HTTP_DURATION_BUCKETS_MS)
}

# The signal paths OTLP/HTTP wants. The setting is a base URL because that is what an operator
# knows and what compose publishes, and the HTTP exporter does *not* append these when it is handed
# an explicit `endpoint=` — it posts to whatever it was given. A base URL passed straight through
# is a 404 from the collector on every export, retried and then dropped, which is visible only in
# the SDK's own logs. So the join happens here, once.
_TRACES: Final = "/v1/traces"
_METRICS: Final = "/v1/metrics"


def _resource(settings: TracingSettings) -> Resource:
    """Who emitted this, in the three attributes `/version` already proves are stamped in.

    `service.version` is the build SHA and `deployment.ref` the branch or tag it came from, both
    from settings that already exist for `/version`. They are resource attributes and not metric
    labels on purpose: the collector's `resource_to_telemetry_conversion` is off, so a deploy does
    not mint a fresh set of time series and break every `rate()` across it. The resource still
    arrives as `target_info`, which is the join a dashboard wants.
    """
    return Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": settings.build_sha,
            "deployment.ref": settings.build_ref,
        }
    )


def _attributes(decision: Decision) -> dict[str, str | bool]:
    """The three labels the headline counter carries, and the span's own identity.

    `step` and `verdict` are `StrEnum`s and `changed` is a bool, so this is a fixed-width label set
    a dashboard can enumerate — which is exactly why `decisions` made those two enums rather than
    free strings. **`field` is deliberately not here.** It is a `str | None` a call site types by
    hand; as a label it would be the unbounded-cardinality metric that module's `Step` docstring
    describes, arriving by the one route the enums do not cover. It goes on the span, where
    cardinality costs nothing, and on the span event, where the proven shape puts it.
    """
    return {
        "step": decision.step.value,
        "verdict": decision.verdict.value,
        "changed": decision.changed,
    }


def _event(decision: Decision) -> dict[str, str]:
    """What happened, as the span event's attributes.

    Absent slots are omitted rather than sent as an empty string or the word "None": a whole-turn
    decision has no `field`, and a step with no before-and-after has no `was`. An attribute that is
    present and empty reads in Grafana as a value that was recorded and lost, which is a different
    and worse claim than one that was never applicable.
    """
    slots = {
        "field": decision.field,
        "was": decision.was,
        "now": decision.now,
        "detail": decision.detail,
    }
    return {key: value for key, value in slots.items() if value is not None}


def _templated(path: str) -> str:
    """A banking path with its account identifiers taken out.

    **The reason this exists rather than `str(request.url)`.** The redaction rule lists a raw
    account identifier among the things that never leave this service, and banking's URLs are
    `/accounts/<uuid>/movements` — so the obvious `url.full` attribute would put on every outbound
    span exactly the value `account_ref()` was written to keep out of every record. The query
    string goes entirely, which is the rule `request_log` already applies on the way in.

    Mechanical rather than hopeful, and it is the same exact rule `decisions._own_words` uses for
    the same value: a segment that parses as a UUID is replaced, and nothing else is. That leaves
    `/accounts/{id}/movements`, which says which endpoint was called and names no account.
    """
    segments = []
    for segment in path.split("/"):
        try:
            UUID(segment)
        except ValueError:
            segments.append(segment)
        else:
            segments.append("{id}")
    return "/".join(segments)


@dataclass(frozen=True, slots=True)
class TurnTelemetry:
    """A `TurnRecord` turned into spans, span events and counters — all three from the one record.

    Built from providers rather than reading the global ones, so a test can hand it an in-memory
    span exporter and an in-memory metric reader and assert on what would have been exported with
    nothing listening on 4318. That is `tests/test_otel.py`, and it is the only way this adapter
    can be verified without the stack: a collector that is down looks identical to an adapter that
    emits nothing.

    **The shape, and the one place it departs from the design.** The design says "one span per step
    with the decisions as span events". This emits **one span per decision**, named for the step,
    and the reason is that `Decision` — not `Step` — is the thing that carries a `verdict`. A step
    is a label on a decision, not a container with a verdict of its own, and
    `explicit_facts.enforce` records one decision *per field*: window, metric and group_by are
    three decisions with three verdicts under one step name. A span per step would therefore need a
    step-level verdict that the record does not have, and anything put there would be an aggregate
    this module invented — a figure that is real and about the wrong question, which is the defect
    this whole design was written to make visible.

    What is emitted per span is unchanged from the shape the stack was proven with: a span named
    for the step carrying `step`, `verdict` and `changed`, holding a `decision` event carrying
    `field`, `was` and `now` (and `detail`, which the record grew after that round trip). The
    departure costs two extra rows in a waterfall when `enforce` looks at three fields, and buys
    the property the design asks for in its strongest form: one decision is one span *and* one
    counter increment, so the rate and the trace are not merely consistent, they are the same set.
    """

    tracer: Tracer
    decisions: Counter
    turns: Counter

    @classmethod
    def build(cls, tracer: Tracer, meter: Meter) -> "TurnTelemetry":
        return cls(
            tracer=tracer,
            decisions=meter.create_counter(
                DECISIONS,
                description="Decisions this service made, by step, verdict and whether it "
                "changed the answer.",
            ),
            turns=meter.create_counter(
                TURNS,
                description="Turns that reached an outcome, by outcome.",
            ),
        )

    def turn(self, record: TurnRecord, *, started_at: int, ended_at: int) -> None:
        """One turn: a `turn` span with a decision span per decision inside it.

        `started_at` and `ended_at` are the request's, in nanoseconds since the epoch, so the
        `turn` span has a real duration even though it is created after the fact. The decision
        spans do not: a `Decision` carries no timestamp, so each one is an instant rather than an
        interval. That is a deliberate limit of the record and not an omission here — the design's
        first line is that the unit of observation is a decision and not a duration, and adding a
        clock to `Decision` would be a change to a shape that call sites are being written against
        right now.

        **The planner-latency histogram the design names cannot be built from this.** `TurnRecord`
        holds no timing at all, so the only ways to get it are a second timer inside the planner —
        a second source of truth about a turn — or a `spanmetrics` connector, which the handoffs
        rule out for exactly that reason. It is left unbuilt and reported rather than manufactured.
        """
        turn = self.tracer.start_span("turn", start_time=started_at)
        try:
            with trace.use_span(turn, end_on_exit=False, record_exception=False):
                turn.set_attribute("decisions", len(record.decisions))
                turn.set_attribute("changed", bool(record.changed))
                if record.outcome is not None:
                    turn.set_attribute("outcome", record.outcome)
                if record.conversation is not None:
                    # **The attribute this whole conversation-identity change is for.** On the
                    # `turn` span and on nothing else: one trace is one turn, so
                    # `{ span.conversation.id = "…" }` in Tempo returns every turn of one
                    # conversation, which is the query that did not exist before. Putting it on
                    # every decision span too would multiply the same fact by seventeen and buy
                    # nothing — the search already lands on the trace that holds them.
                    #
                    # Named `conversation.id` rather than the Gen-AI convention's
                    # `gen_ai.conversation.id`: these are not Gen-AI spans, nothing else in this
                    # service emits that namespace, and half-adopting a convention is how a
                    # dashboard ends up joining on an attribute that means something adjacent.
                    turn.set_attribute("conversation.id", record.conversation)
                for decision in record.decisions:
                    self._decision(decision)
                if record.outcome is not None:
                    # Inside the turn span, for the same exemplar reason as the decision counter
                    # below: an outcome panel is one of the places section 6 wants a waterfall to
                    # be one click away from.
                    self.turns.add(1, {"outcome": record.outcome})
        finally:
            turn.end(end_time=ended_at)

    def _decision(self, decision: Decision) -> None:
        """One decision: a span, its `decision` event, and the counter increment that carries it.

        **The counter is incremented inside the span, and that is the load-bearing line.** The SDK
        attaches an exemplar to a measurement by reading the trace context that is current when
        `add` is called — the default `TraceBasedExemplarFilter` records one only when a sampled
        span is in context. Move this call outside the `with` and everything still works: the
        counter still counts, the span is still exported, Prometheus still draws the rate, and the
        little diamonds that get from a panel to this turn's waterfall are simply never there.
        Nothing logs anything. The three other things that link has to have — exemplar storage on
        Prometheus, remote write carrying exemplars, and `exemplarTraceIdDestinations` naming the
        literal `trace_id` label — were verified when the stack was built; this is the fourth.
        """
        attributes = _attributes(decision)
        span = self.tracer.start_span(decision.step.value, attributes=attributes)
        with trace.use_span(span, end_on_exit=True, record_exception=False):
            # `field` is on the span as well as on the event, and the duplication is deliberate: on
            # the event because that is the shape Tempo was proven with, and on the span because
            # otherwise three `explicit_facts.enforce` rows in a waterfall are indistinguishable
            # until you open each one.
            if decision.field is not None:
                span.set_attribute("field", decision.field)
            span.add_event("decision", attributes=_event(decision))
            self.decisions.add(1, attributes)


# The exporter, or None when nothing is configured. Set once by `configure_tracing` and read by
# `export_turn`; None is the default and the state every test runs in unless it says otherwise.
_telemetry: TurnTelemetry | None = None


def export_turn(record: TurnRecord, *, started_at: int, ended_at: int) -> None:
    """Export one sealed turn, if anything is listening.

    A turn that decided nothing is not exported. That is not an optimisation: every HTTP request
    opens a recorder, including `/health` and the browser's preflight, and a `turn` span per
    readiness poll would bury the turns anybody wants to read under thousands that decided nothing.
    A record with no decisions and no outcome is a request, and the FastAPI instrumentation already
    has a span for that.
    """
    if _telemetry is None:
        return
    if not record.decisions and record.outcome is None:
        return
    try:
        _telemetry.turn(record, started_at=started_at, ended_at=ended_at)
    except Exception:
        # A bookkeeping call must not be able to take down a bank turn. `decisions` refuses that
        # trade for the recording half and it is refused here for the exporting half, with the same
        # reasoning and one more reason: this runs in a `finally` during response teardown, where
        # an exception would replace a clean disconnect with a 500 on a request that had already
        # succeeded. Logged rather than swallowed silently, so a broken exporter is still a record
        # somebody can grep.
        logger.exception("otel.export_failed")


class RecordingTurns:
    """Opens a turn around every HTTP request, and hands the sealed record to the exporter.

    Raw ASGI rather than `@app.middleware("http")`, and the difference is not stylistic:
    `BaseHTTPMiddleware` hands control back as soon as the response *starts*, so on the streaming
    answer route it would seal and export the record before the pipeline had made a single
    decision — an exporter that is perfectly wired and always empty. `await self.app(...)` here
    returns only when the body is finished.

    Installed unconditionally, so the decisions of a live turn are recorded whether or not anything
    exports them. `export_turn` is the only part that asks.

    It exports the recorder `recording_turn` handed it and never re-reads the `ContextVar`, which
    is the discipline that module asks for by name: if a reset does not take — an SSE client
    closing the tab mid-answer resets in a foreign context — the variable can outlive the turn, and
    an exporter that trusted it would attribute one holder's decisions to the next turn that asked.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started_at = time.time_ns()
        with recording_turn() as recorder:
            try:
                await self.app(scope, receive, send)
            finally:
                export_turn(recorder.sealed(), started_at=started_at, ended_at=time.time_ns())


def register_turn_recording(app: "FastAPI") -> None:
    """Always. See the module docstring: recording is unconditional, exporting is optional."""
    app.add_middleware(RecordingTurns)


def _current_trace_context() -> tuple[str, str] | None:
    """The trace and span a log record was emitted inside, or None outside one.

    Lowercase hex of the conventional width, which is the form Tempo's search box and every OTLP
    tool expect. None rather than a zeroed id when no span is recording: an id of thirty-two zeros
    looks like an identifier and correlates nothing, which is the argument `banking/client.py`
    already makes about forwarding a `-` request id. A false correlation is worse than a missing
    one, because only one of the two is visible to whoever is reading at the time.
    """
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


class _TracedTransport(httpx.AsyncBaseTransport):
    """The outbound span, as the wrapping transport `banking/client.py` says it was built for.

    A transport and not the event-hook pair the factory also offers, for three reasons the hooks
    cannot cover: the `response` hook never runs when the connection fails, which is the outbound
    call worth seeing most; a span started in one hook and ended in another has to be smuggled
    between them on the request object; and only a transport can make the span *current* while the
    request is in flight, which is what lets `inject` put a `traceparent` on it. That header is the
    difference between two disconnected traces and one that crosses into banking — the same
    end-to-end promise the request id half of this factory keeps.

    Nothing reaches a call site. The factory asks for a transport and gets None unless
    `configure_tracing` has installed this one.
    """

    def __init__(self, wrapped: httpx.AsyncBaseTransport, tracer: Tracer) -> None:
        self._wrapped = wrapped
        self._tracer = tracer

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = _templated(request.url.path)
        with self._tracer.start_as_current_span(
            f"{request.method} {path}",
            kind=trace.SpanKind.CLIENT,
            # **Both off deliberately, and this is a redaction decision rather than a preference.**
            # Left at their defaults, a failed call attaches an `exception` event carrying the
            # exception's message and its stack trace, and the status carries the message too. An
            # httpx error's message is written by httpx and can quote the URL it was given — and
            # the URL is the one thing on this span that had to be templated, because banking's
            # paths carry raw account identifiers. So the failure is recorded as the exception's
            # *type* and nothing else, which is what `_status` and the `except` below do by hand.
            record_exception=False,
            set_status_on_exception=False,
            attributes={
                "http.request.method": request.method,
                # The templated path, never `url.full` and never the query string. See `_templated`.
                "url.template": path,
                "server.address": request.url.host,
                "server.port": request.url.port or 0,
                "peer.service": "banking",
            },
        ) as span:
            inject(request.headers)
            try:
                response = await self._wrapped.handle_async_request(request)
            except Exception as error:
                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
                raise
            _status(span, response.status_code)
            return response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def _status(span: Span, code: int) -> None:
    """5xx is this service's problem to see; 4xx is an answer banking gave and often the right one.

    A 404 from the account directory is how "that account is not yours" is spelled on this wire, so
    marking it an error would paint the ordinary refusal path red and teach everybody to ignore the
    colour. This is also what the HTTP semantic conventions say for a client span.
    """
    span.set_attribute("http.response.status_code", code)
    if code >= 500:
        span.set_status(Status(StatusCode.ERROR, f"HTTP {code}"))


def _transports(tracer: Tracer) -> "banking_client.Transports":
    """A factory the banking client can call, closed over this set-up's tracer.

    Closed over rather than reaching for `trace.get_tracer` at call time so the outbound spans and
    the turn spans always come from the same provider. They would not otherwise: setting the global
    provider a second time in one process is a warning and a no-op, so a second `create_app` — which
    is a thing the tests do — would leave this half wired to the first call's exporter and the other
    half wired to the second's, with the only sign of it a line in somebody's log.

    A new transport per call, because this factory builds one client per call and a shared
    transport is a shared connection pool. See the note beside `Transports` in the client factory.
    """

    def build() -> httpx.AsyncBaseTransport:
        return _TracedTransport(httpx.AsyncHTTPTransport(), tracer)

    return build


def configure_tracing(app: "FastAPI", *, settings: TracingSettings) -> bool:
    """Set up the SDK, or do nothing at all. Returns whether it did anything.

    Called from `create_app` beside `configure_logging`, and off unless a deployment turns it on:
    a test run and a developer's laptop must not need a collector, and a default that needed one
    would make every test either slow or dependent on something that is not running.

    **The inbound span ends up outside the turn middleware, and not because of the call order
    here.** `instrument_app` does not use `add_middleware` at all — it replaces the app's
    `build_middleware_stack`, so `OpenTelemetryMiddleware` wraps every user middleware however
    they were registered. That is what makes each `turn` span a child of the request that produced
    it rather than a root of its own, and it holds whichever of these two lines runs first. Worth
    writing down because the obvious guess — that this must be registered last to be outermost — is
    wrong, and testing it by reading `app.user_middleware` shows nothing at all.
    """
    global _telemetry
    if not settings.otel_enabled:
        return False
    endpoint = settings.otel_endpoint.rstrip("/")
    resource = _resource(settings)
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + _TRACES))
    )
    trace.set_tracer_provider(tracer_provider)
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=endpoint + _METRICS,
                    preferred_temporality=_CUMULATIVE,
                    preferred_aggregation=_EXPLICIT_BUCKETS,
                )
            )
        ],
    )
    # Not `metrics.set_meter_provider`. The global can only be set once per process and a second
    # attempt is a warning and a no-op, so a service that called `create_app` twice — which the
    # tests do — would get a provider wired to the first call's exporter and no sign of it. The
    # instruments below hold their meter directly, and the FastAPI instrumentation is handed the
    # provider explicitly for the same reason.
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=tracer_provider, meter_provider=meter_provider
    )
    tracer = tracer_provider.get_tracer(SCOPE)
    _telemetry = TurnTelemetry.build(
        tracer,
        meter_provider.get_meter(SCOPE),
    )
    # The two seams that make a log line and a trace find each other, and an outbound call part of
    # the turn that made it. Both are injected rather than imported: `logging` and
    # `banking/client.py` must not import OpenTelemetry, and this is the module that may.
    service_logging.use_trace_context(_current_trace_context)
    banking_client.use_transport(_transports(tracer))
    logger.info(
        "otel.configured",
        extra={
            "endpoint": endpoint,
            "service": SERVICE_NAME,
        },
    )
    return True


__all__ = [
    "DECISIONS",
    "SERVICE_NAME",
    "TURNS",
    "RecordingTurns",
    "TurnTelemetry",
    "configure_tracing",
    "export_turn",
    "register_turn_recording",
]
