"""The local provider, and the three ways it can fail.

Everything here except the last test runs without a daemon: a provider is mostly
error handling, and error handling that only gets exercised when the happy path is
available is error handling nobody has run.
"""

import pytest

from finguard_assistant.domain.errors import ProviderFailedError
from finguard_assistant.infrastructure.llm.echo_llm_provider import EchoLlmProvider
from finguard_assistant.infrastructure.llm.ollama_llm_provider import OllamaLlmProvider
from finguard_assistant.infrastructure.llm.provider_registry import ProviderRegistry

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
UNREACHABLE = "http://127.0.0.1:1"


def test_the_provider_is_registered() -> None:
    # Registering a provider is one line; forgetting it is silent until a catalogue
    # entry names it and the whole request fails at runtime.
    assert "ollama" in ProviderRegistry().available()


async def test_an_unreachable_daemon_is_a_domain_error() -> None:
    """Not an httpx exception. The fallback policy reacts to ProviderFailedError, so a
    transport error that escapes as itself would bypass the fallback entirely."""
    provider = OllamaLlmProvider(base_url=UNREACHABLE, timeout_seconds=2.0)

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

    answer = await OllamaLlmProvider().complete(
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
