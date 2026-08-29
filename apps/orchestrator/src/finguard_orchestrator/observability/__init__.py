"""What this service says about a turn, what it decided, and what it will never say.

Two modules, one rule. `logging` is the record a person greps; `decisions` is the record a
dashboard counts. They are split because they are read by different people at different times,
and joined because the thing neither may contain is the same thing: anything the holder wrote.

This was a single module until the decision record needed somewhere to live. The names below are
re-exported so that split stayed invisible to every existing import — `observability.request_id`
means what it meant before, and the four call sites outside this package did not have to move for
a change that is none of their business.
"""

from finguard_orchestrator.observability.decisions import (
    REDACTED,
    Decision,
    Step,
    TurnRecord,
    TurnRecorder,
    Verdict,
    current_turn,
    record,
    recording_turn,
)
from finguard_orchestrator.observability.logging import (
    JsonLines,
    RequestId,
    account_ref,
    configure_logging,
    request_id,
)

__all__ = [
    "REDACTED",
    "Decision",
    "JsonLines",
    "RequestId",
    "Step",
    "TurnRecord",
    "TurnRecorder",
    "Verdict",
    "account_ref",
    "configure_logging",
    "current_turn",
    "record",
    "recording_turn",
    "request_id",
]
