from collections.abc import Callable

from finguard_assistant.application.policy_store import PolicyStore
from finguard_assistant.domain.enums import FallbackPolicy
from finguard_assistant.domain.errors import NoModelAvailableError, ProviderFailedError
from finguard_assistant.domain.models.answer import Answer
from finguard_assistant.domain.ports.llm_provider import LlmProviderPort
from finguard_assistant.domain.ports.model_catalogue import ModelCataloguePort
from finguard_assistant.domain.value_objects.llm_model import LlmModel
from finguard_assistant.domain.value_objects.llm_policy import LlmPolicy


class AnswerMessage:
    """Answers a message with the model the active policy names.

    The policy is read once per request: if someone swaps it while the answer is being
    generated, that answer finishes with the model it started with.

    When a provider fails, the fallback policy decides. `FAIL` lets the error out, so a
    measurement run cannot be skewed by a silent substitution. `NEXT` walks the rest of
    the catalogue and records every alias it tried, so a degraded answer is
    distinguishable from a clean one afterwards.
    """

    def __init__(
        self,
        policies: PolicyStore,
        catalogue: ModelCataloguePort,
        resolve_provider: Callable[[str], LlmProviderPort],
    ) -> None:
        self._policies = policies
        self._catalogue = catalogue
        self._resolve_provider = resolve_provider

    async def __call__(self, prompt: str) -> Answer:
        policy = self._policies.current()
        attempts: list[str] = []

        for model in self._chain(policy.model):
            attempt = policy.with_model(model)
            try:
                text = await self._ask(attempt, prompt)
            except ProviderFailedError:
                if self._catalogue.fallback is FallbackPolicy.FAIL:
                    raise
                attempts.append(model.alias)
                continue
            return Answer(text=text, policy=attempt, attempts=tuple(attempts))

        raise NoModelAvailableError(attempts)

    def _chain(self, first: LlmModel) -> list[LlmModel]:
        """The chosen model, then the rest of the catalogue in its configured order."""
        if self._catalogue.fallback is FallbackPolicy.FAIL:
            return [first]
        rest = [model for model in self._catalogue.models() if model.alias != first.alias]
        return [first, *rest]

    async def _ask(self, policy: LlmPolicy, prompt: str) -> str:
        provider = self._resolve_provider(policy.model.provider)
        return await provider.complete(
            prompt,
            model=policy.model.model,
            temperature=policy.temperature,
            max_output_tokens=policy.max_output_tokens,
        )
