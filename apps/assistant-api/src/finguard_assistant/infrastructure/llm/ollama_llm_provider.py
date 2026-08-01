"""Talks to a local Ollama daemon.

An outbound adapter: it implements a domain port and speaks HTTP to something outside,
which is why it lives here and carries its technology in its name.

Three settings on every request are not defaults worth inheriting:

`think: false` — thinking is on by default in the runtimes that support it, so a model
asked for a 120-token plan would first generate a reasoning pass nobody reads. Verified
against Ollama 0.32.5 that sending it to a model without thinking is accepted rather
than rejected, so it can go on every request unconditionally.

`keep_alive` — the first call to a cold model pays the load. Measured at ~6.9s total
against ~0.1s once resident, which is the difference between a demo that looks broken
and one that does not.

`format` — the JSON Schema. This is the whole reason a local runtime is worth it: the
decoder cannot emit a token that breaks the schema, so structural validity is not
something the caller hopes for. It says nothing about whether the plan answers the
question asked; only the evaluation dataset says that.
"""

from typing import Any, Final

import httpx2 as httpx

from finguard_assistant.domain.errors import ProviderFailedError

DEFAULT_BASE_URL: Final = "http://127.0.0.1:11434"

# How long the model stays resident after a request. Long enough that a demo never pays
# a cold load between questions.
DEFAULT_KEEP_ALIVE: Final = "30m"

# A local model on a laptop GPU is slower than an API, and a cold load is slower still.
# This is a ceiling for "something is wrong", not a latency target.
DEFAULT_TIMEOUT_SECONDS: Final = 120.0


class OllamaLlmProvider:
    """Local inference. No API key, no network egress, no request leaves the machine."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        keep_alive: str = DEFAULT_KEEP_ALIVE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._keep_alive = keep_alive
        self._timeout = timeout_seconds

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
            "think": False,
            "keep_alive": self._keep_alive,
            "options": {"temperature": temperature, "num_predict": max_output_tokens},
        }
        if schema is not None:
            payload["format"] = schema

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self._base_url}/api/generate", json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderFailedError(self.name, f"HTTP {exc.response.status_code}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderFailedError(self.name, f"timed out after {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            # Daemon down, wrong port, DNS: all the same to the caller — this provider
            # cannot answer, and the fallback policy decides what that means.
            raise ProviderFailedError(self.name, f"cannot reach {self._base_url}: {exc}") from exc
        except ValueError as exc:
            raise ProviderFailedError(self.name, f"malformed response: {exc}") from exc

        # Ollama reports a missing model or a bad option as 200 with an `error` key,
        # so a status check alone would let an error through as if it were an answer.
        if error := body.get("error"):
            raise ProviderFailedError(self.name, str(error))

        answer = body.get("response")
        if not isinstance(answer, str) or not answer.strip():
            raise ProviderFailedError(self.name, "empty answer")
        return answer
