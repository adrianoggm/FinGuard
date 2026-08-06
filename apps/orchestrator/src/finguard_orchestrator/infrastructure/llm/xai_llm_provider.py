"""Talks to xAI, which serves the Grok models.

An outbound adapter beside `ollama_llm_provider.py`, and the first one in this service that
**leaves the machine**. That is the whole difference and it is worth naming rather than burying:

    ollama   local inference. No key, no egress, works with the network unplugged.
    xai      somebody else's GPUs, a bearer token, and every prompt crosses the internet.

Both fulfil the same port, so nothing above this file knows which is installed — that is what
makes a comparison possible at all. What is *not* the same is what a deployment is agreeing to,
and the two decisions a reader should find here rather than infer:

**The prompt is the user's question.** A financial question is "¿cuánto gasté en el psicólogo?",
and sending it to a third party is a processing decision with a legal shape, not a performance
tuning knob. Nothing here is anonymised — the account identifier never travels, because the plan
is built from the sentence and the *figures* come from banking — but the sentence does. Whoever
turns this on is making that call; the code cannot make it for them, and it should not pretend to.

**There is no seed.** Ollama takes one and this deliberately does not offer a knob that would look
like the same promise: a hosted model gives no reproducibility guarantee, and the evaluation
harness leans on runs being comparable. `temperature=0` is what can honestly be asked for. Treat a
number produced through this provider as a measurement of *that day's* endpoint.

The API is OpenAI-shaped, which is why the payload below looks nothing like Ollama's. Structured
output goes through `response_format: json_schema` with `strict: true`, and strict is the only
setting worth using: it is the constrained decoding this whole pipeline is built on, and without it
a schema is a suggestion. It costs a requirement — every property must appear in `required` and
`additionalProperties` must be false — which all three of this service's schemas already satisfy,
because the plan schema was written that way for its own reasons ("every field is required, with
sentinels for the unused ones") and the other two followed it.
"""

from typing import Any, Final

import httpx2 as httpx

from finguard_orchestrator.domain.errors import ProviderFailedError

# The name a catalogue entry and the planner setting use. The vendor rather than the model:
# `grok-4` and `grok-3-mini` are two entries served by one provider, which is the same rule that
# makes `ollama` the name of the runtime and not of `qwen3.5:9b`.
PROVIDER_NAME: Final = "xai"

# What the schema is called on the wire. The API wants a name beside the schema; nothing reads it
# back, so it says what the object is rather than carrying an identifier.
_SCHEMA_NAME: Final = "answer"


class XaiLlmProvider:
    """Hosted inference for the Grok models. Needs an API key and network egress."""

    name = PROVIDER_NAME

    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float) -> None:
        """The key arrives as a plain string and is unwrapped by the caller, on purpose.

        Configuration holds it as a `SecretStr` so an accidental log line or a repr prints
        `**********`; an adapter that has to put it in a header needs the characters. Unwrapping
        at exactly one call site — the composition root — is what keeps the number of places that
        can leak it to one that a reader can check.
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

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
            "max_tokens": max_output_tokens,
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": _SCHEMA_NAME, "schema": schema, "strict": True},
            }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=self._headers()
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            # The status and nothing else. A 400 from a rejected schema carries the reason in the
            # body, and that body quotes the request — which contains the user's sentence. The
            # rule that nothing a person typed reaches a log file does not stop at our own logger.
            raise ProviderFailedError(self.name, f"HTTP {exc.response.status_code}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderFailedError(
                self.name, f"timed out after {self.timeout_seconds}s"
            ) from exc
        except httpx.HTTPError as exc:
            # DNS, TLS, no route. All the same to the caller: this provider cannot answer, and the
            # fallback policy decides what that means.
            raise ProviderFailedError(self.name, f"cannot reach {self._base_url}: {exc}") from exc
        except ValueError as exc:
            raise ProviderFailedError(self.name, f"malformed response: {exc}") from exc

        return _answer_in(body, self.name)

    async def ping(self) -> bool:
        """Whether the endpoint answers *and* the key is accepted.

        `/models` rather than a bare reachability check, because the failure this is for is a key
        that is missing, wrong or out of credit — and a socket that opens says nothing about any of
        those. It is the readiness check the planning path needs: a 401 here is a service that will
        refuse every question, reported before anybody asks one.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/models", headers=self._headers())
                return response.status_code == 200
        except httpx.HTTPError:
            return False


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
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ProviderFailedError(provider, "empty answer")
    return content
