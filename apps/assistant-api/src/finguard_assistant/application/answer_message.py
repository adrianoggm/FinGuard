from collections.abc import Callable
from dataclasses import dataclass

from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.domain.llm_policy import LlmPolicy
from finguard_assistant.ports.llm_provider import LlmProvider


@dataclass(frozen=True)
class Answer:
    text: str
    policy: LlmPolicy


class AnswerMessage:
    """Answers a message with whichever provider the active policy names.

    The policy is read once per request: if someone swaps it while the answer is
    being generated, that answer finishes with the one it started with.
    """

    def __init__(
        self,
        policies: PolicyStore,
        resolve_provider: Callable[[str], LlmProvider],
    ) -> None:
        self._policies = policies
        self._resolve_provider = resolve_provider

    async def __call__(self, prompt: str) -> Answer:
        policy = self._policies.current()
        provider = self._resolve_provider(policy.provider)
        text = await provider.complete(prompt, model=policy.model, temperature=policy.temperature)
        return Answer(text=text, policy=policy)
