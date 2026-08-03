"""GetLlmCatalogueQuery · CatalogueResult · GetLlmCatalogueHandler."""

from dataclasses import dataclass, field

from finguard_orchestrator.application.bus.query_bus import Query
from finguard_orchestrator.application.policy_store import PolicyStore
from finguard_orchestrator.domain.ports.model_catalogue import ModelCataloguePort
from finguard_orchestrator.domain.value_objects.llm_policy import LlmPolicy


@dataclass(frozen=True)
class ModelRow:
    alias: str
    provider: str
    model: str


@dataclass(frozen=True)
class PolicyRow:
    model: ModelRow
    temperature: float
    max_output_tokens: int


@dataclass(frozen=True)
class CatalogueResult:
    """The active policy plus everything this deployment could switch to."""

    active: PolicyRow
    fallback: str
    available_models: list[ModelRow] = field(default_factory=list)
    available_providers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GetLlmCatalogueQuery(Query[CatalogueResult]):
    pass


class GetLlmCatalogueHandler:
    def __init__(
        self,
        policies: PolicyStore,
        catalogue: ModelCataloguePort,
        providers: list[str],
    ) -> None:
        self._policies = policies
        self._catalogue = catalogue
        self._providers = providers

    async def handle(self, query: GetLlmCatalogueQuery) -> CatalogueResult:
        return build_result(self._policies.current(), self._catalogue, self._providers)


def policy_row(policy: LlmPolicy) -> PolicyRow:
    """Shared with the command handlers: they answer with the same shape."""
    return PolicyRow(
        model=ModelRow(
            alias=policy.model.alias,
            provider=policy.model.provider,
            model=policy.model.model,
        ),
        temperature=policy.temperature,
        max_output_tokens=policy.max_output_tokens,
    )


def build_result(
    active: LlmPolicy, catalogue: ModelCataloguePort, providers: list[str]
) -> CatalogueResult:
    return CatalogueResult(
        active=policy_row(active),
        fallback=catalogue.fallback.value,
        available_models=[
            ModelRow(alias=model.alias, provider=model.provider, model=model.model)
            for model in catalogue.models()
        ],
        available_providers=providers,
    )
