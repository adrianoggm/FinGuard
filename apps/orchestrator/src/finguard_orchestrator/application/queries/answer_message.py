"""AnswerMessageQuery · AnswerMessageResult · AnswerMessageHandler.

A query, not a command: it changes no state of ours. It does reach an external provider,
which is why the fallback policy lives here — deciding what to do when a model cannot
answer is application policy, not infrastructure detail.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.application.policy_store import PolicyStore
from finguard_orchestrator.domain.enums import FallbackPolicy
from finguard_orchestrator.domain.errors import NoModelAvailableError, ProviderFailedError
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.ports.model_catalogue import ModelCataloguePort
from finguard_orchestrator.domain.value_objects.llm_model import LlmModel
from finguard_orchestrator.domain.value_objects.llm_policy import LlmPolicy


@dataclass(frozen=True)
class AnswerMessageResult:
    """What the query answers with. Flat on purpose: a Result never carries a domain
    object out of the application layer."""

    text: str
    model_alias: str
    provider: str
    model: str
    temperature: float
    max_output_tokens: int
    # Empty on the happy path. Non-empty means a fallback served this answer.
    attempted: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AnswerMessageQuery(Query[AnswerMessageResult]):
    prompt: str


class AnswerMessageHandler:
    """Answers a message with the model the active policy names.

    The policy is read once: if someone swaps it while the answer is being generated,
    that answer finishes with the model it started with.

    When a provider fails, the fallback policy decides. `FAIL` lets the error out, so a
    measurement run cannot be skewed by a silent substitution. `NEXT` walks the rest of
    the catalogue and records every alias it tried.
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

    async def handle(self, query: AnswerMessageQuery) -> AnswerMessageResult:
        policy = self._policies.current()
        attempts: list[str] = []

        for model in self._chain(policy.model):
            attempt = policy.with_model(model)
            try:
                text = await self._ask(attempt, query.prompt)
            except ProviderFailedError:
                if self._catalogue.fallback is FallbackPolicy.FAIL:
                    raise
                attempts.append(model.alias)
                continue
            return _to_result(text, attempt, tuple(attempts))

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


def _to_result(text: str, policy: LlmPolicy, attempted: tuple[str, ...]) -> AnswerMessageResult:
    return AnswerMessageResult(
        text=text,
        model_alias=policy.model.alias,
        provider=policy.model.provider,
        model=policy.model.model,
        temperature=policy.temperature,
        max_output_tokens=policy.max_output_tokens,
        attempted=attempted,
    )
