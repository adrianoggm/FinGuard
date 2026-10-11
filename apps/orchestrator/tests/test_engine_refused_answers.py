"""A hosted model's answer refused after it was generated is still an answer (ruling R31).

Groq's strict mode validates the generation against the schema after writing it, and a wrong shape
comes back as HTTP 400 `json_validate_failed`, with what the model wrote in `failed_generation`.
That is the same event as Ollama returning a shape the domain refuses: the model answered, and can
be told what was wrong once. So the provider names it apart from an outage, and the interpreter
gives it the one repair and then asks back, as for any unreadable answer.
"""

import json
import logging
from datetime import date
from typing import Any
from uuid import UUID

import pytest

from finguard_orchestrator.domain.engine.operations import Reset
from finguard_orchestrator.domain.engine.state import DialogueState
from finguard_orchestrator.domain.errors import AnswerRefusedError, ProviderFailedError
from finguard_orchestrator.domain.ports.turn_operations import InterpretationFailedError
from finguard_orchestrator.domain.services import category_words
from finguard_orchestrator.infrastructure.engine.llm_turn_operations import LlmTurnOperations
from finguard_orchestrator.infrastructure.llm import openai_compatible_provider
from finguard_orchestrator.infrastructure.llm.openai_compatible_provider import (
    OpenAiCompatibleProvider,
)

SENTENCE = "mi secreto: empecemos de nuevo"
WRITTEN = '{"operations": [{"forecast": {"months": 3}}]}'
RESET = json.dumps({"operations": [{"reset": {}}]})
CATEGORIES = {key: category_words.name_of(key) for key in ("RESTAURANTS", "LEISURE")}


class Provider:
    """Answers in order; an exception is raised instead of returned."""

    name = "scripted"

    def __init__(self, *answers: str | Exception) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    async def complete(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def interpreted(provider: Provider) -> Any:
    adapter = LlmTurnOperations(
        provider, model="m", temperature=0.0, max_output_tokens=600, categories=CATEGORIES
    )
    return await adapter.interpret(SENTENCE, DialogueState(UUID(int=1)), today=date(2026, 8, 5))


def refused(generation: str | None = WRITTEN) -> AnswerRefusedError:
    return AnswerRefusedError("groq", "HTTP 400 json_validate_failed", generation=generation)


async def test_a_refused_answer_gets_the_one_repair(caplog: pytest.LogCaptureFixture) -> None:
    provider = Provider(refused(), RESET)

    with caplog.at_level(logging.WARNING):
        ops = await interpreted(provider)

    assert ops == (Reset(),)
    first, repair = provider.prompts
    # Asked again with what it wrote, as for any answer that cannot be read.
    assert repair.startswith(first) and "forecast" in repair[len(first) :]
    # The log names the kind of refusal, never what was written or said.
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "forecast" not in logged and "secreto" not in logged


async def test_refused_twice_is_the_models_failure_not_a_question() -> None:
    provider = Provider(refused(), refused())

    with pytest.raises(InterpretationFailedError):
        await interpreted(provider)
    assert len(provider.prompts) == 2


async def test_a_refusal_without_the_generation_is_still_repaired() -> None:
    provider = Provider(refused(None), RESET)

    assert await interpreted(provider) == (Reset(),)
    assert len(provider.prompts) == 2


async def test_a_provider_that_cannot_answer_is_still_a_failure() -> None:
    provider = Provider(ProviderFailedError("groq", "HTTP 503"))

    with pytest.raises(InterpretationFailedError):
        await interpreted(provider)
    assert len(provider.prompts) == 1


# --- where the provider says so -----------------------------------------------------------------


class _Response:
    def __init__(self, payload: Any, status: int) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise openai_compatible_provider.httpx.HTTPStatusError(  # type: ignore[call-arg]
                "boom", request=None, response=self
            )

    def json(self) -> Any:
        return self._payload


def answering(monkeypatch: pytest.MonkeyPatch, body: Any, status: int) -> None:
    class FakeClient:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def post(self, url: str, **_: Any) -> _Response:
            return _Response(body, status)

    monkeypatch.setattr(openai_compatible_provider.httpx, "AsyncClient", FakeClient)


def groq() -> OpenAiCompatibleProvider:
    return OpenAiCompatibleProvider(
        name="groq",
        api_key="gsk-not-real",
        base_url="https://api.example/v1",
        timeout_seconds=5.0,
        reasoning_headroom=0,
    )


async def test_groqs_post_generation_refusal_is_a_refused_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answering(
        monkeypatch,
        {
            "error": {
                "message": f"Failed to validate JSON. «{SENTENCE}»",
                "type": "invalid_request_error",
                "code": "json_validate_failed",
                "failed_generation": WRITTEN,
            }
        },
        400,
    )

    with pytest.raises(AnswerRefusedError) as raised:
        await groq().complete("x", model="m", temperature=0.0, max_output_tokens=8, schema={})

    assert raised.value.generation == WRITTEN
    # Still a provider failure to everything that only knows those, and quoting nothing.
    assert isinstance(raised.value, ProviderFailedError)
    assert "secreto" not in str(raised.value) and "forecast" not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [{"error": {"message": "model not found", "code": "model_not_found"}}, "not json", None],
)
async def test_any_other_400_is_an_outage(monkeypatch: pytest.MonkeyPatch, body: Any) -> None:
    answering(monkeypatch, body, 400)

    with pytest.raises(ProviderFailedError) as raised:
        await groq().complete("x", model="m", temperature=0.0, max_output_tokens=8)

    assert not isinstance(raised.value, AnswerRefusedError)
