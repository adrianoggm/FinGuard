"""What this service says about a turn, and what it will never say.

`observability.py` became this package so that the decision record has somewhere to live beside
the logging setup without the two sharing a module. Today it holds one module and re-exports
exactly what that module already exported, so the split is invisible to every existing import —
`observability.request_id` means what it meant before, and the four call sites outside this
package did not have to move for a change that is none of their business.
"""

from finguard_orchestrator.observability.logging import (
    JsonLines,
    RequestId,
    account_ref,
    configure_logging,
    request_id,
)

__all__ = [
    "JsonLines",
    "RequestId",
    "account_ref",
    "configure_logging",
    "request_id",
]
