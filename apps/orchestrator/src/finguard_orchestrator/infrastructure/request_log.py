"""One record per HTTP request, and the identifier every other record hangs off.

Middleware rather than a decorator on each route, because the property wanted here is that *every*
request is accounted for — including the ones that 404, the ones that raise before reaching a
handler, and the preflight a browser sends before anything interesting happens.

The path is logged and the query string is not. `/v1/questions` carries its question in the body
and nothing here reads a body, which is deliberate: see the note in `observability` about what
never reaches a log file.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from finguard_orchestrator.observability import request_id

logger = logging.getLogger(__name__)

# Honoured when a caller sends one, so a request crossing from the web client to here to banking
# can be followed end to end. Generated otherwise — an absent header is the ordinary case and not
# a reason to have nothing to correlate by.
HEADER = "X-Request-Id"


def register_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def _record(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        given = request.headers.get(HEADER, "")
        # Bounded and stripped of anything that is not an identifier: this value ends up in every
        # log line of the request, and a header is caller-controlled input like any other.
        current = "".join(c for c in given if c.isalnum() or c in "-_")[:64] or uuid.uuid4().hex
        token = request_id.set(current)
        started = time.perf_counter()

        def elapsed() -> float:
            return round((time.perf_counter() - started) * 1000, 1)

        try:
            response = await call_next(request)
            response.headers[HEADER] = current
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "latency_ms": elapsed(),
                },
            )
            return response
        except Exception:
            logger.exception(
                "request.failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "latency_ms": elapsed(),
                },
            )
            raise
        finally:
            # After both records, not around them. Resetting in a `finally` that runs before the
            # success log — which is what a bare try/except/finally does — leaves the one record
            # anybody actually greps for with no request id on it.
            request_id.reset(token)
