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

import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from finguard_orchestrator.domain.errors import AnswerRefusedError, ProviderFailedError
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort


@dataclass
class ModelCalls:
    """Per-request logical calls and outbound provider attempts.

    A logical call may perform several HTTP attempts (transient retry or model failover). Keep
    both counts so evaluation does not mistake one for the other.
    """

    count: int = 0
    provider_logical_calls: int = 0
    provider_requests: int = 0
    attempted_models: list[str] = field(default_factory=list)
    successful_models: list[str] = field(default_factory=list)
    provider_failures: list[str] = field(default_factory=list)
    terminal_provider_failure: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    token_reports: int = 0
    provider_request_limit: int | None = None
    # Where a turn's provider time went: waiting for a request slot (the per-provider pace, the
    # serialising lock, a retry's pause) and the HTTP requests themselves. A turn's latency minus
    # the wait is what the person would see with no pacing.
    provider_wait_ms: int = 0
    provider_request_ms: int = 0


def record_provider_request(model: str, provider: str) -> None:
    """Record one outbound generation HTTP request in the active request context."""
    calls = _current.get()
    if calls is not None:
        if (
            calls.provider_request_limit is not None
            and calls.provider_requests >= calls.provider_request_limit
        ):
            raise ProviderFailedError(provider, "per-turn generation request budget exhausted")
        calls.provider_requests += 1
        calls.attempted_models.append(model)


@contextmanager
def provider_request_budget(limit: int) -> Iterator[None]:
    """Cap hosted HTTP attempts for the current graph turn, including internal retries/fallback."""
    calls = _current.get()
    if calls is None:
        yield
        return
    previous = calls.provider_request_limit
    calls.provider_request_limit = limit if previous is None else min(previous, limit)
    try:
        yield
    finally:
        calls.provider_request_limit = previous


def record_provider_wait(seconds: float) -> None:
    """Time spent waiting for a provider request slot or a retry's pause, not on the request."""
    calls = _current.get()
    if calls is not None and seconds > 0:
        calls.provider_wait_ms += round(seconds * 1000)


def record_provider_request_time(seconds: float) -> None:
    """Time one outbound provider HTTP request took, from send to response."""
    calls = _current.get()
    if calls is not None and seconds > 0:
        calls.provider_request_ms += round(seconds * 1000)


def record_successful_model(model: str) -> None:
    """Record the model that returned usable output for one logical provider call."""
    calls = _current.get()
    if calls is not None:
        calls.successful_models.append(model)


def record_provider_failure(provider: str, category: str) -> None:
    """Record a bounded, secret-free provider failure label for the current request."""
    calls = _current.get()
    if calls is None:
        return
    calls.provider_failures.append(_failure_label(provider, category))


def _failure_label(provider: str, category: str) -> str:
    safe_provider = re.sub(r"[^a-zA-Z0-9_-]", "", provider)[:32].lower() or "unknown"
    safe_category = re.sub(r"[^a-zA-Z0-9_-]", "", category)[:32].lower() or "unknown"
    return f"{safe_provider}:{safe_category}"


def record_terminal_provider_failure(provider: str, category: str) -> None:
    """Record the terminal logical-call cause separately from failed HTTP attempts."""
    calls = _current.get()
    if calls is not None:
        calls.terminal_provider_failure = _failure_label(provider, category)


def record_usage(prompt_tokens: int | None, completion_tokens: int | None) -> None:
    """Accumulate token usage only when the provider returned numeric usage metadata."""
    if prompt_tokens is None and completion_tokens is None:
        return
    calls = _current.get()
    if calls is not None:
        calls.prompt_tokens += prompt_tokens or 0
        calls.completion_tokens += completion_tokens or 0
        calls.token_reports += 1


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


def budget_calls_so_far() -> int:
    """Count local logical calls plus each actual hosted generation attempt.

    Hosted provider retries and fallback models consume turn budget individually. Local providers
    have no HTTP-attempt instrumentation, so their wrapped logical call remains one budget unit.
    """
    calls = _current.get()
    if calls is None:
        return 0
    return calls.count - calls.provider_logical_calls + calls.provider_requests


class CountingProvider(LlmProviderPort):
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
            if getattr(self.inner, "reports_provider_requests", False):
                calls.provider_logical_calls += 1
        try:
            return await self.inner.complete(
                prompt,
                model=model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                schema=schema,
            )
        except AnswerRefusedError as failed:
            record_terminal_provider_failure(failed.provider, "schema_refusal")
            raise
        except ProviderFailedError as failed:
            reason = failed.reason.lower()
            if "http 429" in reason:
                category = "rate_limited"
            elif re.search(r"http 5\d\d", reason):
                category = "upstream_5xx"
            elif re.search(r"http 4\d\d", reason):
                category = "upstream_4xx"
            elif "timed out" in reason:
                category = "timeout"
            elif "per-turn generation request budget exhausted" in reason:
                category = "generation_budget"
            elif "cannot reach" in reason:
                category = "transport"
            elif "malformed response" in reason:
                category = "malformed_response"
            else:
                category = "provider_error"
            record_terminal_provider_failure(failed.provider, category)
            raise

    def __getattr__(self, name: str) -> Any:
        """Whatever else the adapter offers is the adapter's: readiness pings the planner's."""
        return getattr(self.inner, name)
