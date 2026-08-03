"""The local provider, and the three ways it can fail.

Everything here except the last test runs without a daemon: a provider is mostly
error handling, and error handling that only gets exercised when the happy path is
available is error handling nobody has run.
"""

import pytest

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.infrastructure.llm.echo_llm_provider import EchoLlmProvider
from finguard_orchestrator.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider
from finguard_orchestrator.infrastructure.llm.provider_registry import ProviderRegistry

SCHEMA = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "enum": ["THIS_MONTH", "LAST_MONTH"]},
        "direction": {"type": "string", "enum": ["EXPENSE", "INCOME", "ALL"]},
        "categories": {"type": "array", "items": {"type": "string"}},
        "explain": {"type": "boolean"},
    },
    "required": ["period", "direction", "categories", "explain"],
}

# Nothing listens on port 1, so this is an unreachable daemon without stubbing one.
LOCAL = "http://127.0.0.1:11434"


def provider_at(base_url: str, *, timeout_seconds: float = 120.0) -> OllamaLlmProvider:
    """The provider has no defaults any more — its numbers belong to a deployment and live
    in .env.defaults. A test states its own rather than reading that file: what is under
    test here is the adapter's behaviour, and pinning the values keeps the test independent
    of a configuration change it has no opinion about."""
    return OllamaLlmProvider(
        base_url=base_url,
        keep_alive="30m",
        timeout_seconds=timeout_seconds,
        context_tokens=4096,
        seed=0,
    )


UNREACHABLE = "http://127.0.0.1:1"


def test_only_providers_that_need_no_configuration_are_built_in() -> None:
    """Ollama used to be in the default registry, which meant a zero-argument builder had
    to invent a base URL, a timeout and a context window — a second source of truth for
    numbers that now live in .env.defaults. A provider nobody configured can no longer be
    built, so bootstrap supplies this one with the settings."""
    assert ProviderRegistry().available() == ["echo"]


async def test_an_unreachable_daemon_is_a_domain_error() -> None:
    """Not an httpx exception. The fallback policy reacts to ProviderFailedError, so a
    transport error that escapes as itself would bypass the fallback entirely."""
    provider = provider_at(UNREACHABLE, timeout_seconds=2.0)

    with pytest.raises(ProviderFailedError) as caught:
        await provider.complete("hola", model="whatever", temperature=0.0, max_output_tokens=8)

    assert caught.value.provider == "ollama"
    assert caught.value.code == "provider_failed"


async def test_echo_answers_a_schema_with_parseable_json() -> None:
    import json

    answer = await EchoLlmProvider().complete(
        "cualquier cosa", model="echo-1", temperature=0.0, max_output_tokens=64, schema=SCHEMA
    )

    parsed = json.loads(answer)
    assert set(parsed) == {"period", "direction", "categories", "explain"}
    # The first enum member, an empty list, False: the smallest thing the schema admits.
    assert parsed == {
        "period": "THIS_MONTH",
        "direction": "EXPENSE",
        "categories": [],
        "explain": False,
    }


async def test_echo_without_a_schema_still_echoes() -> None:
    answer = await EchoLlmProvider().complete(
        "hola", model="echo-2", temperature=0.0, max_output_tokens=64
    )

    assert answer == "[echo:echo-2] hola"


@pytest.mark.integration
async def test_a_real_daemon_honours_the_schema() -> None:
    """What only a running daemon can prove: the decoder is constrained, not persuaded.

    It asserts the shape and the enum membership, never which value was chosen —
    whether the plan matches the question is the evaluation dataset's job, and a
    provider test that asserted meaning would be measuring the model.
    """
    import json

    answer = await provider_at(LOCAL).complete(
        "El usuario pregunta cuánto gastó en gasolina el mes pasado.",
        model="qwen2.5-coder:7b",
        temperature=0.0,
        max_output_tokens=128,
        schema=SCHEMA,
    )

    parsed = json.loads(answer)
    assert parsed["period"] in {"THIS_MONTH", "LAST_MONTH"}
    assert parsed["direction"] in {"EXPENSE", "INCOME", "ALL"}
    assert isinstance(parsed["categories"], list)
