"""Talks to a local Ollama daemon.

An outbound adapter: it implements a domain port and speaks HTTP to something outside,
which is why it lives here and carries its technology in its name.

Three settings on every request are not defaults worth inheriting:

`think: false` — **a correctness requirement, not a latency choice**, which is the
opposite of why it was originally set. Thinking is on by default in the runtimes that
support it, and the obvious argument was that a 120-token plan does not need a reasoning
pass nobody reads. Measuring it found something worse: combined with `format`, the
schema-constrained JSON is emitted into the *thinking* channel and `response` comes back
empty.

    think: true + format   ->  thinking: '{"period": "LAST_MONTH", ...}'
                               response: ''

The planner receives nothing, and the whole dataset fails with PROVIDER_ERROR. What
catches it is the empty-answer check below, written as generic defence and turned out to
be load-bearing. Do not "improve" this flag without re-running
scripts/evaluate_planning.py --think.

Verified against Ollama 0.32.5 that sending it to a model *without* thinking is accepted
rather than rejected, so it can go on every request unconditionally.

`keep_alive` — the first call to a cold model pays the load. Measured at ~6.9s total
against ~0.1s once resident, which is the difference between a demo that looks broken
and one that does not.

`num_ctx` — the context window, and the setting that cost the most to find. Ollama
loads a model at its *default* window, which for a current model is 256K tokens. The
weights of qwen3.5:4b are 3.4GB; at 256K the KV cache takes it to 13GB, so `ollama ps`
reported "64%/36% CPU/GPU" and two thirds of a 4B model ran on the processor. It
measured slower than a 7B. Our prompt is around a thousand tokens, so a window sized
for the job keeps the whole model resident.

`format` — the JSON Schema. This is the whole reason a local runtime is worth it: the
decoder cannot emit a token that breaks the schema, so structural validity is not
something the caller hopes for. It says nothing about whether the plan answers the
question asked; only the evaluation dataset says that.
"""

from typing import Any

import httpx2 as httpx

from finguard_orchestrator.domain.errors import ProviderFailedError


class OllamaLlmProvider:
    """Local inference. No API key, no network egress, no request leaves the machine."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        keep_alive: str,
        timeout_seconds: float,
        context_tokens: int,
        seed: int,
        think: bool = False,
    ) -> None:
        """No defaults except `think`, and the absence is the design. Every number here is
        a measurement that belongs to a deployment, so it lives in .env.defaults with the
        experiment that produced it written beside it. Required rather than optional so an
        adapter cannot be built with numbers nobody chose.

        `think` keeps its default because it is not a tuning knob: false is a correctness
        requirement, for the reason in the module docstring."""
        self._base_url = base_url.rstrip("/")
        # Public because they are configuration, and configuration that reaches an adapter
        # without being observable is configuration nobody can prove arrived.
        self.keep_alive = keep_alive
        self.timeout_seconds = timeout_seconds
        self.context_tokens = context_tokens
        self.seed = seed
        self.think = think

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
            "prompt": prompt,
            "stream": False,
            "think": self.think,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": temperature,
                "num_predict": max_output_tokens,
                "num_ctx": self.context_tokens,
                "seed": self.seed,
            },
        }
        if schema is not None:
            payload["format"] = schema

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self._base_url}/api/generate", json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderFailedError(self.name, f"HTTP {exc.response.status_code}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderFailedError(
                self.name, f"timed out after {self.timeout_seconds}s"
            ) from exc
        except httpx.HTTPError as exc:
            # Daemon down, wrong port, DNS: all the same to the caller — this provider
            # cannot answer, and the fallback policy decides what that means.
            raise ProviderFailedError(self.name, f"cannot reach {self._base_url}: {exc}") from exc
        except ValueError as exc:
            raise ProviderFailedError(self.name, f"malformed response: {exc}") from exc

        if not isinstance(body, dict):
            # A JSON array parses fine and then has no .get, which used to surface as a
            # 500 from a provider whose whole contract is to fail as ProviderFailedError.
            raise ProviderFailedError(self.name, f"expected an object, got {type(body).__name__}")

        # Ollama reports a missing model or a bad option as 200 with an `error` key,
        # so a status check alone would let an error through as if it were an answer.
        if error := body.get("error"):
            raise ProviderFailedError(self.name, str(error))

        answer = body.get("response")
        if not isinstance(answer, str) or not answer.strip():
            raise ProviderFailedError(self.name, "empty answer")
        return answer

    async def ping(self) -> bool:
        """Whether the daemon answers. Readiness for the planning path, which the
        catalogue's provider says nothing about."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self._base_url}/api/version")
                return response.status_code == 200
        except httpx.HTTPError:
            return False
