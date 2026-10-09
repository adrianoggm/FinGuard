"""How many model calls one request made, whichever engine made them.

The evaluation reports model calls per turn for both engines (spec, "Metrics"), so they are counted
where both reach a model: the composition root wraps every provider it builds in this one, and
the planner, the turn reader, the payment reader, the visual planner, the expert agents' readers
and the engine's interpreter all call through it without knowing. A count kept by each caller
would be one per engine, and the two would not be the same number.

**A tally object in a `ContextVar`, not an int.** A call made in a task the request spawned (an
`asyncio.gather`, the body of a streaming response) runs in a copy of the request's context, where
`set` changes the copy and the request never sees the call. An object is shared by every copy, so
each call lands on the request that made it. Outside a request (a script, a test calling a handler
directly) there is no tally and nothing is counted.
"""

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort


@dataclass
class ModelCalls:
    count: int = 0


_current: ContextVar[ModelCalls | None] = ContextVar("finguard_model_calls", default=None)


@contextmanager
def counting_calls() -> Iterator[ModelCalls]:
    """Count the calls made inside, and hand back the tally that holds them.

    Read the tally this yields, never the variable: a reset that does not take leaves the variable
    in a context being torn down (see `observability.decisions.recording_turn`, which guards the
    same reset for the same reason).
    """
    calls = ModelCalls()
    token = _current.set(calls)
    try:
        yield calls
    finally:
        with suppress(ValueError):
            _current.reset(token)


def calls_so_far() -> int:
    """The calls this request has made so far, or 0 outside one."""
    calls = _current.get()
    return calls.count if calls is not None else 0


class CountingProvider:
    """Implements LlmProviderPort around another provider, counting every call made through it."""

    def __init__(self, inner: LlmProviderPort) -> None:
        self.inner = inner
        self.name = inner.name

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        schema: dict[str, Any] | None = None,
    ) -> str:
        if (calls := _current.get()) is not None:
            # Before the call rather than after it: one that fails still cost the turn its latency.
            calls.count += 1
        return await self.inner.complete(
            prompt,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            schema=schema,
        )

    def __getattr__(self, name: str) -> Any:
        """Whatever else the adapter offers is the adapter's: readiness pings the planner's."""
        return getattr(self.inner, name)
