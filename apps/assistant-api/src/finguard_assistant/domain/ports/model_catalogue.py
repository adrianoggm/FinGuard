from typing import Protocol

from finguard_assistant.domain.enums import FallbackPolicy
from finguard_assistant.domain.value_objects.llm_model import LlmModel


class ModelCataloguePort(Protocol):
    """The models this deployment may use, in fallback order.

    The catalogue comes from configuration, not from code: adding a model or changing
    the fallback policy must not need a release. The domain only asks it questions.
    """

    @property
    def fallback(self) -> FallbackPolicy: ...

    def models(self) -> tuple[LlmModel, ...]:
        """Every model available, in the order the fallback walks them."""
        ...

    def default(self) -> LlmModel:
        """The model the service starts with."""
        ...

    def get(self, alias: str) -> LlmModel:
        """Raises UnknownModelError if the alias is not in the catalogue."""
        ...
