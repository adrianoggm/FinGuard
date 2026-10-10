"""Talks to a hosted endpoint that speaks the OpenAI chat-completions protocol.

Named after the protocol and not the vendor, because the vendor is a base URL. Groq is what is
registered today; xAI, Together and OpenAI itself differ from it by one string. That is not
speculative generality — it was learned by writing this file for xAI first, being handed a Groq
key, and finding the diff was `api.x.ai/v1` → `api.groq.com/openai/v1`.

Cloudflare Workers AI is the second registration and the first to cost more than a string: it
speaks this protocol for inference and does *not* serve `/models`, so readiness had to become a
whole URL rather than a suffix. See `cloudflare_workers_ai.py`, which is a builder and not an
adapter — that it could be is the point of this class.

It is an outbound adapter beside `ollama_llm_provider.py`, and the first one that **leaves the
machine**:

    ollama              local inference. No key, no egress, works with the network unplugged.
    openai-compatible   somebody else's GPUs, a bearer token, every prompt crosses the internet.

Both fulfil the same port, which is what makes a comparison possible. What is not the same is what
a deployment is agreeing to, and the three decisions a reader should find here rather than infer.

**The prompt is the user's question.** A financial question is "¿cuánto gasté en el psicólogo?",
and sending it to a third party is a processing decision with a legal shape, not a tuning knob.
Nothing is anonymised — the account identifier never travels, because the plan is built from the
sentence and the *figures* come from banking — but the sentence does. Whoever turns this on is
making that call; the code cannot make it for them and should not pretend to.

**There is no seed.** Ollama takes one and this deliberately offers no knob that would look like
the same promise: a hosted model gives no reproducibility guarantee, and the evaluation harness
leans on runs being comparable. `temperature=0` is what can honestly be asked for. Treat a number
produced through this provider as a measurement of *that day's* endpoint.

**A reasoning model spends the token budget thinking.** This is the one that costs a morning if it
is not written down. `max_output_tokens` on the port means "how long may the answer be"; on a
reasoning model the hidden reasoning is generated first and billed against the same ceiling, so a
budget sized for the answer truncates the reasoning and the request fails. Measured against
`openai/gpt-oss-120b` with this service's own prompts:

    reading a turn    224 tokens generated, ~10 of them the answer.  Our budget: 32.
    planning a query  698-907 tokens generated, ~120 the plan.       Our budget: 384.

Both 400ed with "Failed to validate JSON" — which reads like a schema problem and is a budget
problem. `reasoning_headroom` is the floor this applies underneath the caller's number. It is a
ceiling and not a reservation, so raising it costs nothing until a model actually rambles.

It is the same trap the Ollama adapter documents from the other side: there, `think: true` plus a
schema put the JSON in the thinking channel and returned an empty answer. Two runtimes, two
symptoms, one cause — reasoning and constrained decoding interact, and neither runtime says so.
"""

import asyncio
import logging
from typing import Any, Final

import httpx2 as httpx

from finguard_orchestrator.domain.errors import AnswerRefusedError, ProviderFailedError

# What the schema is called on the wire. The API wants a name beside the schema; nothing reads it
# back, so it says what the object is rather than carrying an identifier.
_SCHEMA_NAME: Final = "answer"
logger = logging.getLogger(__name__)
# Groq's free tier allows 8,000 tokens a minute and an engine turn reserves about 6,000, so two
# turns typed at a normal pace meet a 429 that asks for a few seconds. Waited out once; a longer
# wait (the daily budget says minutes) fails the turn at once rather than hold the request open.
_LONGEST_RATE_WAIT_SECONDS: Final = 20.0
_TRANSIENT_RETRY_DELAY_SECONDS: Final = 0.5


class OpenAiCompatibleProvider:
    """Hosted inference over the OpenAI chat-completions protocol. Needs a key and egress."""

    reports_provider_requests = True

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        reasoning_headroom: int,
        readiness_url: str | None = None,
        min_request_interval_seconds: float = 0.0,
        shared_request_pacer: Any | None = None,
    ) -> None:
        """`name` is what a catalogue entry and the planner setting say, and it names the vendor.

        An instance attribute rather than a class one, which is the difference from the other two
        adapters and the reason this class exists once instead of twice: `groq` and `xai` are two
        registrations of one protocol, not two implementations.

        The key arrives as a plain string. Configuration holds it as a `SecretStr` so a stray log
        line or a repr prints `**********`; an adapter that has to put it in a header needs the
        characters. Unwrapping at exactly one call site — the composition root — keeps the number
        of places that can leak it to one a reader can check.

        `readiness_url` overrides where `ping` looks. It defaults to the OpenAI convention,
        `/models` beside the completions route, and it is a default rather than a rule because
        one endpoint already breaks it — see the note on `ping`. A whole URL and not a path
        suffix, because the endpoint that broke it lives outside the base URL entirely.
        """
        self.name = name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.reasoning_headroom = reasoning_headroom
        self._readiness_url = readiness_url or f"{self._base_url}/models"
        self._min_request_interval_seconds = max(0.0, min_request_interval_seconds)
        self._shared_request_pacer = shared_request_pacer
        self._request_lock = asyncio.Lock()
        self._last_request_started: float | None = None

    async def _post(self, client: Any, payload: dict[str, Any]) -> Any:
        """Serialize and pace generation requests through local or shared provider coordination.

        Every attempt, including retry and model failover, consumes a slot. The local lock protects
        one provider instance; Gemini additionally reserves shared Postgres slots across replicas.
        """
        async with self._request_lock:
            # Import locally so the generic provider remains usable without request middleware.
            # The tally is ContextVar-backed and shared by child tasks spawned for this request.
            from finguard_orchestrator.infrastructure.llm.counting_provider import (
                record_provider_request,
            )

            loop = asyncio.get_running_loop()
            pacer = self._shared_request_pacer
            if pacer is not None and pacer.active and await pacer.wait_for_slot():
                self._last_request_started = loop.time()
                record_provider_request(str(payload.get("model", "unknown")), self.name)
                return await client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=self._headers()
                )
            if self._last_request_started is not None and self._min_request_interval_seconds:
                wait = self._min_request_interval_seconds - (
                    loop.time() - self._last_request_started
                )
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_request_started = loop.time()
            record_provider_request(str(payload.get("model", "unknown")), self.name)
            return await client.post(
                f"{self._base_url}/chat/completions", json=payload, headers=self._headers()
            )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        schema: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            # The caller's number is about the answer; this ceiling has to cover the reasoning
            # too. See the module docstring for the measurements — a 32-token classification
            # generated 224 tokens and still failed at 128.
            "max_tokens": max(max_output_tokens, self.reasoning_headroom),
        }
        if schema is not None:
            # `strict` is the whole point rather than a detail. Without it a JSON schema on this
            # protocol is a prompt-level suggestion: the answer usually matches, the caller parses
            # it, and nothing looks wrong until a model improvises a field. With it the decoder
            # cannot emit a token that breaks the shape — which is what the plan verifier and the
            # payment reader are built on top of.
            #
            # Not every model behind an OpenAI-compatible endpoint supports it. Measured on Groq:
            # the gpt-oss family does, and `llama-3.3-70b-versatile` and `qwen/qwen3.6-27b` refuse
            # the parameter outright with a 400 that names the limitation. That is the right
            # failure — a model that cannot be constrained must not serve this pipeline quietly.
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": _SCHEMA_NAME, "schema": schema, "strict": True},
            }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await self._post(client, payload)
                if (wait := _short_rate_wait(response)) is not None:
                    _record_provider_failure(self.name, "rate_limited")
                    await asyncio.sleep(wait)
                    response = await self._post(client, payload)
                elif 500 <= response.status_code < 600:
                    _record_provider_failure(self.name, "upstream_5xx")
                    # Provider capacity and gateway errors are often transient. Retry this
                    # read-only model request once; never retry arbitrary 4xx or financial tools.
                    logger.warning(
                        "llm.provider_transient_failure provider=%s status=%s retrying=true",
                        self.name,
                        response.status_code,
                    )
                    await asyncio.sleep(_TRANSIENT_RETRY_DELAY_SECONDS)
                    response = await self._post(client, payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            # The status and nothing else. A 400 from a rejected schema carries the reason in the
            # body, and that body quotes the request — which contains the user's sentence. The
            # rule that nothing a person typed reaches a log file does not stop at our own logger.
            status = exc.response.status_code
            if status == 429:
                _record_provider_failure(self.name, "rate_limited")
                limits = _rate_limit_headers(exc.response)
                provider_code, quota_ids = _rate_limit_details(exc.response)
                logger.warning(
                    "llm.provider_rate_limited provider=%s retry_after=%s "
                    "remaining_requests=%s reset_requests=%s remaining_tokens=%s reset_tokens=%s "
                    "provider_code=%s quota_ids=%s",
                    self.name,
                    limits.get("retry-after", "unknown"),
                    limits.get("x-ratelimit-remaining-requests", "unknown"),
                    limits.get("x-ratelimit-reset-requests", "unknown"),
                    limits.get("x-ratelimit-remaining-tokens", "unknown"),
                    limits.get("x-ratelimit-reset-tokens", "unknown"),
                    provider_code or "unknown",
                    ",".join(quota_ids) or "unknown",
                )
                details = "; ".join(f"{name}={value}" for name, value in limits.items() if value)
                if provider_code:
                    details = "; ".join(filter(None, (details, f"provider_code={provider_code}")))
                if quota_ids:
                    details = "; ".join(filter(None, (details, f"quota_ids={','.join(quota_ids)}")))
                reason = f"HTTP {status}" + (f" ({details})" if details else "")
                raise ProviderFailedError(self.name, reason) from None
            if (refused := _refused_generation(exc.response)) is not None:
                _record_provider_failure(self.name, "schema_refusal")
                # Groq's strict mode checks the answer after generating it: the model did answer,
                # in the wrong shape, and the caller may ask once more (ruling R31). What it wrote
                # travels on the error, never in its message.
                raise AnswerRefusedError(
                    self.name, f"HTTP {status} json_validate_failed", generation=refused or None
                ) from None
            _record_provider_failure(self.name, "upstream_5xx" if status >= 500 else "upstream_4xx")
            raise ProviderFailedError(self.name, f"HTTP {status}") from exc
        except httpx.TimeoutException as exc:
            _record_provider_failure(self.name, "timeout")
            raise ProviderFailedError(
                self.name, f"timed out after {self.timeout_seconds}s"
            ) from exc
        except httpx.HTTPError as exc:
            _record_provider_failure(self.name, "transport")
            # DNS, TLS, no route. All the same to the caller: this provider cannot answer, and the
            # fallback policy decides what that means.
            raise ProviderFailedError(self.name, f"cannot reach {self._base_url}: {exc}") from exc
        except ValueError as exc:
            _record_provider_failure(self.name, "malformed_response")
            raise ProviderFailedError(self.name, f"malformed response: {exc}") from exc

        answer = _answer_in(body, self.name)
        from finguard_orchestrator.infrastructure.llm.counting_provider import (
            record_successful_model,
            record_usage,
        )

        used_model = str(payload.get("model", model))
        record_successful_model(used_model)
        usage = body.get("usage") if isinstance(body, dict) else None
        if isinstance(usage, dict):
            record_usage(
                _usage_count(usage.get("prompt_tokens", usage.get("input_tokens"))),
                _usage_count(usage.get("completion_tokens", usage.get("output_tokens"))),
            )
        return answer

    async def ping(self) -> bool:
        """Whether the endpoint answers *and* the key is accepted.

        An authenticated GET rather than a bare reachability check, because the failure this is
        for is a key that is missing, wrong or out of credit — and a socket that opens says
        nothing about any of those. It is the readiness check the planning path needs: a refused
        credential here is a service that will refuse every question, reported before anybody
        asks one.

        `/models` is the OpenAI convention and the default, and it is only a convention.
        Cloudflare's `/ai/v1` prefix serves `chat/completions` and `embeddings` and nothing else;
        a GET of `.../ai/v1/models` answers `405 GET not supported for requested URI` — verified
        against the live API, which needs no token to return a routing error. Assuming the
        convention there would report a perfectly healthy account as down for ever.

        The comparison is to 200 and not "below 400" for the same kind of reason: Cloudflare
        answers a bad token on its own readiness route with a 400, not a 401.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(self._readiness_url, headers=self._headers())
                return response.status_code == 200
        except httpx.HTTPError:
            return False


def _short_rate_wait(response: Any) -> float | None:
    """The seconds a 429 asks for, from the header or a Google RetryInfo detail.

    Daily quota failures usually have no short retry delay and must fail fast. A per-minute
    RESOURCE_EXHAUSTED response may carry its delay in the JSON body instead of Retry-After.
    """
    if response.status_code != 429:
        return None
    seconds: float | None
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except (TypeError, ValueError):
        seconds = None
    if seconds is None:
        seconds = _retry_info_delay(response)
    return seconds if seconds is not None and 0 <= seconds <= _LONGEST_RATE_WAIT_SECONDS else None


def _retry_info_delay(response: Any) -> float | None:
    """Read only the structured retry duration; never inspect/log a provider's error message."""
    try:
        body = response.json()
    except (AttributeError, ValueError):
        return None
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(details, list):
        return None
    for detail in details:
        if not isinstance(detail, dict) or not str(detail.get("@type", "")).endswith(
            "google.rpc.RetryInfo"
        ):
            continue
        value = detail.get("retryDelay")
        if not isinstance(value, str) or not value.endswith("s"):
            continue
        try:
            return float(value[:-1])
        except ValueError:
            return None
    return None


def _rate_limit_headers(response: Any) -> dict[str, str]:
    """Only copy the numeric/reset metadata needed to diagnose a 429, never its body or prompt."""
    names = (
        "retry-after",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-reset-requests",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-tokens",
    )
    found: dict[str, str] = {}
    for name in names:
        value = response.headers.get(name, "").strip()
        if value and len(value) <= 32 and all(char.isalnum() or char in ".:+- " for char in value):
            found[name] = value
    return found


def _rate_limit_details(response: Any) -> tuple[str | None, tuple[str, ...]]:
    """Machine-readable quota identifiers only; never log an upstream error message."""
    try:
        body = response.json()
    except (AttributeError, ValueError):
        return None, ()
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, ()
    code = error.get("code") or error.get("status")
    if not isinstance(code, str) or not code or len(code) > 64:
        code = None
    else:
        code = code if all(char.isalnum() or char in "_.-" for char in code) else None
    details = error.get("details")
    quota_ids: set[str] = set()
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict) or not str(detail.get("@type", "")).endswith(
                "google.rpc.QuotaFailure"
            ):
                continue
            violations = detail.get("violations")
            if not isinstance(violations, list):
                continue
            for violation in violations:
                quota_id = violation.get("quotaId") if isinstance(violation, dict) else None
                if (
                    isinstance(quota_id, str)
                    and quota_id
                    and len(quota_id) <= 100
                    and all(char.isalnum() or char in "_.-" for char in quota_id)
                ):
                    quota_ids.add(quota_id)
    return code, tuple(sorted(quota_ids))


def _refused_generation(response: Any) -> str | None:
    """What the model wrote, when the endpoint refused it after generating (`json_validate_failed`,
    "" when it did not say what), or None for any other error."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict) or error.get("code") != "json_validate_failed":
        return None
    generation = error.get("failed_generation")
    return generation if isinstance(generation, str) else ""


def _answer_in(body: Any, provider: str) -> str:
    """The message content, or a failure that says which part was missing.

    Every shape below has been a real outage on some OpenAI-compatible endpoint: a top-level
    `error` with a 200, an empty `choices`, a `content` of null when the model emitted only a
    refusal. Reading them defensively costs a few lines; reading them optimistically costs a
    `TypeError: 'NoneType' object is not subscriptable` in a 500 from a provider whose entire
    contract is to fail as `ProviderFailedError`.
    """
    if not isinstance(body, dict):
        raise ProviderFailedError(provider, f"expected an object, got {type(body).__name__}")
    if error := body.get("error"):
        detail = error.get("message") if isinstance(error, dict) else error
        raise ProviderFailedError(provider, str(detail))

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderFailedError(provider, "no choices in the answer")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        if isinstance(choice, dict) and choice.get("finish_reason") == "length":
            raise ProviderFailedError(provider, "output budget exhausted before final answer")
        raise ProviderFailedError(provider, "empty answer")
    return content


def _usage_count(value: Any) -> int | None:
    """Accept only nonnegative integer token counters from provider usage metadata."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _record_provider_failure(provider: str, category: str) -> None:
    """Keep attempt telemetry separate from response bodies, prompts and provider details."""
    from finguard_orchestrator.infrastructure.llm.counting_provider import (
        record_provider_failure,
    )

    record_provider_failure(provider, category)
