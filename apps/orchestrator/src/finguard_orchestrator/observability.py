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


class RequestId(logging.Filter):
    """Stamps the current request onto every record, including the ones from libraries."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
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
        payload.update(_extras(record))
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """Whatever the call site passed in `extra`, and nothing the machinery added."""
    standard = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
        "request_id",
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
            "filters": {"request_id": {"()": RequestId}},
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
                    "filters": ["request_id"],
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


__all__ = ["account_ref", "configure_logging", "request_id"]
