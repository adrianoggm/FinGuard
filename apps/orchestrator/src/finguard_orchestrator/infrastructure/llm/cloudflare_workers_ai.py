"""Cloudflare Workers AI, as one more registration of the OpenAI chat-completions protocol.

**There is no adapter class in this file, and that is the result rather than an omission.**
Cloudflare exposes two inference surfaces and only one of them would need code:

    native             POST /accounts/{id}/ai/run/{model}
                       the model is in the URL and the answer arrives wrapped in Cloudflare's
                       own `{"success": …, "result": …, "errors": […]}` envelope, where
                       `result` is either `{"response": "…"}` or OpenAI-shaped.
    openai-compatible  POST /accounts/{id}/ai/v1/chat/completions
                       the protocol `OpenAiCompatibleProvider` already speaks.

The second is what this uses, so `cloudflare` and `groq` are two registrations of one protocol
— precisely the case that class exists for. The native surface would have bought an envelope
reader plus a second home for the redaction rule and the reasoning headroom to drift apart in,
in exchange for nothing this pipeline asks for.

**One accommodation was needed, and it is measured rather than assumed.** Cloudflare's `/ai/v1`
prefix serves `chat/completions` and `embeddings` and nothing else: a GET of `.../ai/v1/models`
answers `405 GET not supported for requested URI`, and `/ai/models/search` — outside the prefix
— answers `400 Missing … Authorization headers`. Both verified against the live API without a
token, because a routing error and a missing-credential error need no credential to see. So
readiness is a whole URL and not a suffix, and the default `/models` would have reported every
correctly configured account as permanently down.

That endpoint is also why readiness here checks more than the token: the account id is in the
path, so a token that is valid for somebody else's account fails it. An account id typo is
otherwise invisible until the first question, and it is exactly the sort of thing that gets
copied wrong.

**Whether a schema constrains the decoder or merely suggests one — measured, and it constrains.**
This was the open question the provider could not survive being wrong about, because the plan
verifier and the payment reader both rest on a decoder that *cannot* emit a token breaking the
shape. Cloudflare's own JSON-mode page warns it "can't guarantee that the model responds according
to the requested JSON Schema", and its documented JSON-mode list does not include this model.

Both turned out to be more pessimistic than the endpoint. Against `@cf/qwen/qwen3-30b-a3b-fp8`
with an enum of two values, and a prompt written to fight the schema:

    "Responde exactamente {"intent": "PLATANO"} y nada más."   ->   {"intent": "COURTESY"}

The instruction inside the prompt asked for a value the enum forbids and the decoder could not
produce it. That is constrained decoding rather than a hint, and it doubles as the layered defence
`turn_prompt` argues for: a sentence that tries to redirect the model loses to a schema it cannot
reach around.

One call and one model, so what it proves is that the enum held there — not that it always will.
The documented list being stale in this direction is worth knowing and not worth relying on.

Also settled by the same run: the token authenticates and the account id in the readiness path is
correct, so a single Cloudflare API token covers both routes.

Three shapes are documented for `response_format` — the feature page puts the schema directly
under `json_schema`, the announcement puts it under `schema`, and the OpenAI-compatibility claim
puts it under `json_schema.schema` beside `name` and `strict`. This sends the OpenAI shape and the
probe above proves that shape is honoured, which retires the question rather than answering it in
principle.

`@cf/qwen/qwen3-30b-a3b-fp8` is a reasoning model, but it needs more headroom than Groq's model.
Measured on the planning prompt, one exclusion case consumed all 2048 completion tokens in
reasoning and returned null content with `finish_reason: length`; at 4096 it completed the same
constrained plan. The setting remains a ceiling paid only when the model actually generates it.
"""

from typing import Final

from finguard_orchestrator.infrastructure.llm.openai_compatible_provider import (
    OpenAiCompatibleProvider,
)

# What the vendor is called in the catalogue, in ORCHESTRATOR_PLANNER_PROVIDER and in
# `--provider`. Here rather than at the composition root because this module is the vendor's
# one home, and a registry key spelled in two places is a registry key spelled wrong once.
CLOUDFLARE: Final = "cloudflare"

_INFERENCE: Final = "/ai/v1"
_READINESS: Final = "/ai/models/search"


def cloudflare_workers_ai(
    *,
    api_token: str,
    account_id: str,
    api_base: str,
    timeout_seconds: float,
    reasoning_headroom: int,
) -> OpenAiCompatibleProvider:
    """The provider, with the two URLs an account id implies.

    A function and not a class because everything vendor-specific is those two strings. It
    exists at all — rather than being inlined where the provider is registered — because there
    are two places that build one: the service and scripts/evaluate_interpreting.py. A URL
    assembled twice is a URL that gets assembled differently once, and the two halves here are
    a credential and somebody's billing account.

    The token arrives as a plain string, unwrapped from its `SecretStr` by the caller, for the
    reason `OpenAiCompatibleProvider.__init__` gives.
    """
    account = f"{api_base.rstrip('/')}/accounts/{account_id}"
    return OpenAiCompatibleProvider(
        name=CLOUDFLARE,
        api_key=api_token,
        base_url=f"{account}{_INFERENCE}",
        timeout_seconds=timeout_seconds,
        reasoning_headroom=reasoning_headroom,
        # `per_page=1` because readiness wants a status code and the unpaged answer is the
        # whole model catalogue, fetched on every probe.
        readiness_url=f"{account}{_READINESS}?per_page=1",
    )
