"""The fallback policy: what happens when the chosen model cannot answer."""

import pytest
from support import BACKUP, PRIMARY, FakeProvider, catalogue

from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.application.use_cases.answer_message import AnswerMessage
from finguard_assistant.domain.enums import FallbackPolicy
from finguard_assistant.domain.errors import NoModelAvailableError, ProviderFailedError


def answering(*, fallback: FallbackPolicy, primary_fails: bool) -> AnswerMessage:
    models = catalogue(PRIMARY, BACKUP, fallback=fallback)
    providers = {
        "flaky": FakeProvider("flaky", fails=primary_fails),
        "echo": FakeProvider("echo"),
    }
    store = PolicyStore(models, models.default_policy())
    return AnswerMessage(store, models, providers.__getitem__)


async def test_the_chosen_model_answers_and_says_who_it_was() -> None:
    answer = await answering(fallback=FallbackPolicy.NEXT, primary_fails=False)("hola")

    assert answer.text == "[flaky:big] hola"
    # The model carries its provider, so the answer can say where it came from.
    assert answer.policy.model.alias == "primary"
    assert answer.provider == "flaky"
    assert answer.attempts == ()
    assert not answer.fell_back


async def test_with_next_it_falls_to_the_following_model() -> None:
    answer = await answering(fallback=FallbackPolicy.NEXT, primary_fails=True)("hola")

    assert answer.text == "[echo:small] hola"
    assert answer.policy.model.alias == "backup"
    assert answer.provider == "echo"
    # The attempt is recorded: a degraded answer must be distinguishable from a good one.
    assert answer.attempts == ("primary",)
    assert answer.fell_back


async def test_with_fail_the_error_reaches_the_caller() -> None:
    # A measurement run wants this: a silent substitution would skew the numbers.
    with pytest.raises(ProviderFailedError):
        await answering(fallback=FallbackPolicy.FAIL, primary_fails=True)("hola")


async def test_when_every_model_fails_it_says_what_it_tried() -> None:
    models = catalogue(PRIMARY, BACKUP, fallback=FallbackPolicy.NEXT)
    broken = {"flaky": FakeProvider("flaky", fails=True), "echo": FakeProvider("echo", fails=True)}
    use_case = AnswerMessage(
        PolicyStore(models, models.default_policy()), models, broken.__getitem__
    )

    with pytest.raises(NoModelAvailableError) as raised:
        await use_case("hola")

    assert raised.value.attempted == ["primary", "backup"]


async def test_the_policy_is_read_once_per_request() -> None:
    models = catalogue(PRIMARY, BACKUP, fallback=FallbackPolicy.NEXT)
    provider = FakeProvider("echo")
    store = PolicyStore(models, models.default_policy().with_model(BACKUP))
    use_case = AnswerMessage(store, models, lambda _: provider)

    answer = await use_case("hola")

    assert answer.policy.model.alias == "backup"
    assert provider.calls == [("small", "hola")]
