"""How this service says what it did, and what it will never say.

Configured once, from the composition root, and readable from every layer — which is why it
sits at the package root rather than under `bootstrap`: `bootstrap` imports the application, so
the application importing back into it would be a cycle waiting for one more field.

Everywhere else does the ordinary thing:

    logger = logging.getLogger(__name__)

rather than a logger threaded through constructors. A logger passed as an argument is a
dependency every layer has to declare in order to be silent, and the standard library already
solved this — the hierarchy is the wiring, and `configure_logging` is the only place that decides
what the handlers and the format are.

**What is worth logging here is a conversation degrading, not a stack trace.** The reported
defects were all figures that were real and about the wrong question, so the records that matter
are the ones that let somebody read, after the fact:

    ventana explícita detectada = últimos 3 meses
    ventana heredada            = últimos 5 meses
    ventana final               = últimos 5 meses

and see immediately which turn lost it. `turn.window` is exactly that record.

**What is never logged.** This service reads a person's bank account, so the default has to be
that nothing they wrote and nothing the ledger holds reaches a log file:

    the question text          it is free text a person typed into a bank app
    a Bizum's concept          "regalo de Marta", "abogado", "farmacia"
    a contact's name or phone  who somebody pays is who they know
    a movement's description   the ledger's own record of what they bought
    a raw account identifier   pseudonymised to eight hex characters, which is enough to
                               correlate two records and not enough to name an account

That list is a discipline at the call sites rather than a filter, because a filter cannot tell a
category name from a merchant name. What makes it hold is a test that drives a real turn and
asserts the sentence never appears in what was emitted.
"""

import hashlib
import json
import logging
import logging.config
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Final
from uuid import UUID

# The identifier tying every record of one HTTP request together. A ContextVar rather than an
# argument for the same reason the logger is not passed around: it is ambient to the request, and
# threading it through the planner and the ledger adapter would make every signature carry a field
# only the logs read. Set by the middleware, read by the filter.
request_id: ContextVar[str] = ContextVar("request_id", default="-")

# The keys every record carries, whatever emitted it. Anything else arrives through `extra`.
_ALWAYS: Final = ("levelname", "name", "message")

# Where the trace and span a record was emitted inside come from, when anything is tracing.
#
# **A slot rather than an import.** The design has exactly one module that may import
# OpenTelemetry, and it is not this one — so `telemetry/otel.py` installs the real source
# during set-up and this file keeps working, and keeps being testable, with no SDK anywhere near
# it. It is a plain module-level callable and not a `ContextVar` because it is a property of the
# process, decided once at the composition root; the per-request part is inside whatever it calls.
TraceSource = Callable[[], tuple[str, str] | None]


def _untraced() -> tuple[str, str] | None:
    """The default, and what a test run gets: nothing is tracing, so there is no trace to name."""
    return None


_trace_context: TraceSource = _untraced


def use_trace_context(source: TraceSource) -> None:
    """Called by `telemetry.otel` once the SDK exists. Never by anything else."""
    global _trace_context
    _trace_context = source


class RequestId(logging.Filter):
    """Stamps the current request onto every record, including the ones from libraries."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class TraceContext(logging.Filter):
    """Stamps the trace and span onto every record, so a log line and a trace find each other.

    **Why this rather than a second correlation scheme.** `request_id` already ties this service's
    own records together, and it is honoured across to banking — but it means nothing to Tempo,
    which indexes by trace id. Without these two fields, getting from a log line to the turn's
    waterfall means finding the request id in a span attribute somebody remembered to set. With
    them it is a copy and a paste into Tempo's search box.

    A filter and not a formatter change so the value is captured **when the record is created**,
    which for anything logged inside a span is the only moment the context is still current. The
    formatter runs on the handler's terms and, for a queue or a thread, on somebody else's.

    Absent rather than zeroed when nothing is tracing. See `_current_trace_context` in `otel`:
    thirty-two zeros look like an identifier and correlate nothing, and a false correlation is
    worse than a missing one because only one of the two is visible to whoever is reading.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        found = _trace_context()
        if found is not None:
            record.trace_id, record.span_id = found
        return True


class JsonLines(logging.Formatter):
    """One JSON object per line.

    Structured because these records are read by grepping for a conversation that went wrong, and
    "planned AGGREGATE in 812ms" is not something you can filter on. Hand-rolled rather than a
    dependency: the whole job is `json.dumps` over the record's own attributes, and a logging
    library would be the largest thing in this service's dependency list.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        # Beside `request_id` because they answer the same question — "what else belongs to this?"
        # — and omitted entirely when nothing is tracing rather than emitted empty. A line with no
        # `trace_id` key says there was no trace; a line with `trace_id: ""` says there was one and
        # it was lost, and those are different facts. With tracing off, every line in this service
        # is byte-for-byte what it was before this field existed.
        for key in ("trace_id", "span_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        payload.update(_extras(record))
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """Whatever the call site passed in `extra`, and nothing the machinery added."""
    standard = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
        "request_id",
        # Stamped by the filters above and placed by `format`, so they are the machinery's and not
        # a call site's `extra`. Without these two here they would be emitted twice over — once
        # where `format` puts them and once from this dict — and the second copy would decide the
        # order, which is a diff in every log line for no reason anybody could find.
        "trace_id",
        "span_id",
        "message",
        "asctime",
        "taskName",
    }
    return {key: value for key, value in record.__dict__.items() if key not in standard}


def account_ref(account_id: UUID) -> str:
    """An account as something two records can be correlated by and nobody can be named from.

    Eight hex characters of a digest. Not reversible, stable within a deployment, and useless to
    anybody who has the log and not the ledger — which is the whole point, because a log file
    leaves the trust boundary the moment it is shipped anywhere.
    """
    return hashlib.sha256(str(account_id).encode()).hexdigest()[:8]


def configure_logging(*, level: str = "INFO", as_json: bool = True) -> None:
    """Called once, from `create_app`. Plain text is for a terminal; JSON is for everything else."""
    logging.config.dictConfig(
        {
            "version": 1,
            # Uvicorn configures its own loggers before this runs, and taking them over is the
            # point: two formats interleaved in one stream is a stream nothing can parse.
            "disable_existing_loggers": False,
            # Both always, whether or not anything is tracing. The trace filter reads a slot that
            # returns None until `telemetry.otel` fills it, so an untraced process pays one
            # function call per record and every line comes out exactly as it did before.
            "filters": {
                "request_id": {"()": RequestId},
                "trace_context": {"()": TraceContext},
            },
            "formatters": {
                "json": {"()": JsonLines},
                "text": {
                    "format": "%(asctime)s %(levelname)-7s %(request_id)s %(name)s %(message)s",
                },
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json" if as_json else "text",
                    "filters": ["request_id", "trace_context"],
                }
            },
            "root": {"handlers": ["stdout"], "level": level},
            "loggers": {
                # It logs one line per request with the path in it, which duplicates the access
                # record below and carries a query string this service would rather not repeat.
                "uvicorn.access": {"handlers": [], "propagate": False},
            },
        }
    )


__all__ = ["account_ref", "configure_logging", "request_id", "use_trace_context"]
