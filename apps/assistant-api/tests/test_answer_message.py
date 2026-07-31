"""The fallback policy: what happens when the chosen model cannot answer."""

import pytest
from support import BACKUP, PRIMARY, FakeProvider, catalogue

from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.application.queries.answer_message import (
    AnswerMessageHandler,
    AnswerMessageQuery,
    AnswerMessageResult,
)
from finguard_assistant.domain.enums import FallbackPolicy
from finguard_assistant.domain.errors import NoModelAvailableError, ProviderFailedError


def handler(*, fallback: FallbackPolicy, primary_fails: bool) -> AnswerMessageHandler:
    models = catalogue(PRIMARY, BACKUP, fallback=fallback)
    providers = {
        "flaky": FakeProvider("flaky", fails=primary_fails),
        "echo": FakeProvider("echo"),
    }
    store = PolicyStore(models, models.default_policy())
    return AnswerMessageHandler(store, models, providers.__getitem__)


async def answer(*, fallback: FallbackPolicy, primary_fails: bool) -> AnswerMessageResult:
    use_case = handler(fallback=fallback, primary_fails=primary_fails)
    return await use_case.handle(AnswerMessageQuery("hola"))


async def test_the_chosen_model_answers_and_says_who_it_was() -> None:
    result = await answer(fallback=FallbackPolicy.NEXT, primary_fails=False)

    assert result.text == "[flaky:big] hola"
    # The model carries its provider, so the answer can say where it came from.
    assert result.model_alias == "primary"
    assert result.provider == "flaky"
    assert result.attempted == ()


async def test_with_next_it_falls_to_the_following_model() -> None:
    result = await answer(fallback=FallbackPolicy.NEXT, primary_fails=True)

    assert result.text == "[echo:small] hola"
    assert result.model_alias == "backup"
    assert result.provider == "echo"
    # The attempt is recorded: a degraded answer must be distinguishable from a good one.
    assert result.attempted == ("primary",)


async def test_with_fail_the_error_reaches_the_caller() -> None:
    # A measurement run wants this: a silent substitution would skew the numbers.
    with pytest.raises(ProviderFailedError):
        await answer(fallback=FallbackPolicy.FAIL, primary_fails=True)


async def test_when_every_model_fails_it_says_what_it_tried() -> None:
    models = catalogue(PRIMARY, BACKUP, fallback=FallbackPolicy.NEXT)
    broken = {"flaky": FakeProvider("flaky", fails=True), "echo": FakeProvider("echo", fails=True)}
    use_case = AnswerMessageHandler(
        PolicyStore(models, models.default_policy()), models, broken.__getitem__
    )

    with pytest.raises(NoModelAvailableError) as raised:
        await use_case.handle(AnswerMessageQuery("hola"))

    assert raised.value.attempted == ["primary", "backup"]


async def test_the_policy_is_read_once_per_request() -> None:
    models = catalogue(PRIMARY, BACKUP, fallback=FallbackPolicy.NEXT)
    provider = FakeProvider("echo")
    store = PolicyStore(models, models.default_policy().with_model(BACKUP))
    use_case = AnswerMessageHandler(store, models, lambda _: provider)

    result = await use_case.handle(AnswerMessageQuery("hola"))

    assert result.model_alias == "backup"
    assert provider.calls == [("small", "hola")]
