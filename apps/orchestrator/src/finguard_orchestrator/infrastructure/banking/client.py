"""The one place an outbound call to banking is built, so there is somewhere to put what all of
them need.

Until this existed there was nowhere. Each of the seven clients constructed its own
`httpx.AsyncClient` inline — ten sites — which is why `request_log.py` could promise that the
inbound `X-Request-Id` is honoured *"so a request crossing from the web client to here to banking
can be followed end to end"* and not keep it. The id was honoured, bounded, stamped on every local
record and echoed back on the response, and then nothing forwarded it. The only header any outbound
call sent was `Idempotency-Key`, on payments. Correlation stopped at this service's own edge, not
because anybody decided it should, but because adding it would have meant editing ten places and
remembering the eleventh.

**A factory rather than a base class**, because what the seven share is a *connection*, not
behaviour. Each owns its own URLs, its own error ladder — a 422 means something different to the
query client than to the payment one — and its own body reading, and those differ on purpose. A
base class would invite the next person to pull one of those up into it. This adds the connection's
properties and claims nothing about what the seven do with them.

**One client per call, still.** That is what the seven already did, and the `async with` stays at
the call site: nothing holds a pool across requests, so a header belonging to one request cannot be
read by the next.

**Why the id is stamped by an event hook and not by `headers=` on the client.** The request id
belongs to the request, not to the connection. `headers=` would freeze whatever was in the context
when the client was constructed, which is correct here only because these clients happen to be
built one per call — a property nothing enforces and a future pool would quietly break. The hook
reads the context variable when the request is actually sent, so it is right either way.

**Where a span goes — and it went in the transport.** Section 3 offered two shapes here, a second
`request` hook paired with a `response` hook, or a wrapping `AsyncBaseTransport`. The transport
won, for three reasons the hooks cannot cover: a `response` hook never runs when the connection
fails, which is the outbound call worth seeing most; a span opened in one hook and closed in
another has to be smuggled between them on the request object; and only a transport can hold the
span *current* while the request is in flight, which is what lets a `traceparent` be injected onto
it. As promised, neither reaches a call site — `_transport` below is None until
`telemetry/otel.py` installs one, and `banking_client`'s signature is unchanged.
`opentelemetry-instrumentation-httpx` is not an option and will not become one — it patches
`httpx`, and this service has `httpx2`. `tests/test_dependency_provenance.py` holds the three facts
behind that, and `import httpx` raises `ModuleNotFoundError` here.
"""

from collections.abc import Callable

import httpx2 as httpx

# Imported rather than restated. The property this whole seam exists for is that a value arriving
# under a name leaves under the same one, and two string literals cannot promise that.
from finguard_orchestrator.infrastructure.request_log import HEADER
from finguard_orchestrator.observability.logging import request_id

# What `request_id` reads as when no request set it. Restated here because a `ContextVar` does not
# expose its default, and pinned by a test, so changing it there fails here rather than silently
# turning every background call into a caller called "-".
UNSET = "-"

# The same bound `request_log.py` applies on the way in, applied again on the way out. Not a second
# opinion about the value: the rule is idempotent, so an id that arrived through the middleware
# leaves byte-identical and "unchanged end to end" is a claim that survives a long header. It is
# restated because the middleware is not the only thing that can set this context variable, and
# this is the last code before a socket — httpx2 forwards a value containing CRLF without
# complaint, which is a request-splitting gadget pointed at the bank.
LIMIT = 64

# What every client built here is put on top of, and None means httpx's own default — which is
# what this file did before and what it still does in every test run.
#
# **A slot rather than an import**, for the same reason `observability/logging.py` has one: this
# module must not import OpenTelemetry, and the design says exactly one module may.
# `telemetry/otel.py` installs a tracing transport during set-up if a deployment turned tracing
# on, and nothing else ever calls this. A callable rather than a stored instance because a
# transport owns a connection pool and this factory builds one client per call — a single shared
# instance would quietly become the pool the module docstring above says nothing holds.
Transports = Callable[[], httpx.AsyncBaseTransport | None]


def _untraced() -> httpx.AsyncBaseTransport | None:
    return None


_transport: Transports = _untraced


def use_transport(source: Transports) -> None:
    """Called by `telemetry.otel` when tracing is configured. Never by anything else."""
    global _transport
    _transport = source


def _forwarded() -> str | None:
    """The id to put on the wire, or None when there is nothing honest to put there.

    Two cases return None and they are the same case: no request is behind this call. A background
    task or a test gets `request_id`'s default, and a value that survived nothing of the filter is
    no more of an identifier than that.

    **`-` is not forwarded, deliberately.** It would look like an id and correlate nothing: banking
    honours the header the way this service does, so `-` passes its filter too and every unattended
    call in the estate would arrive claiming to be the same request. Omitting it makes banking
    generate a fresh one, which is exactly what `request_log.py` says about the inbound case — "an
    absent header is the ordinary case and not a reason to have nothing to correlate by". A false
    correlation is worse than a missing one, because only one of the two is visible to whoever is
    reading the logs at the time.
    """
    current = request_id.get()
    if current == UNSET:
        return None
    return "".join(c for c in current if c.isalnum() or c in "-_")[:LIMIT] or None


async def _stamp(request: httpx.Request) -> None:
    """Put this request's id on this request, at the moment it is sent."""
    current = _forwarded()
    if current is not None:
        request.headers[HEADER] = current


def banking_client(*, timeout: float) -> httpx.AsyncClient:
    """A client for a call to banking, carrying the request it is part of.

    `timeout` is passed rather than read off the caller because it is not uniform: every ordinary
    call uses the deployment's `banking_timeout_seconds`, and the readiness probe in
    `HttpFinancialQuery.ping` deliberately uses a short fixed one — a readiness check that waits as
    long as a real query is not a readiness check. A factory that reached for `self.timeout_seconds`
    would have flattened that distinction on the way past.

    Whatever a call site passes as its own `headers=` still arrives untouched. The hook runs after
    httpx has built the request, and sets one key on it — so `Idempotency-Key` on a payment travels
    *beside* the request id rather than being replaced by it. That is the header this service has
    that stops a payment being made twice, and it keeps working exactly as it did.

    `transport=None` is httpx's own default, so with nothing installed this returns precisely the
    client it returned before the span existed.
    """
    return httpx.AsyncClient(
        timeout=timeout,
        event_hooks={"request": [_stamp]},
        transport=_transport(),
    )
