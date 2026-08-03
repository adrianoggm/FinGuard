from dataclasses import dataclass

from finguard_orchestrator.domain.constants import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TEMPERATURE,
    MAX_TEMPERATURE,
)
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.llm_model import LlmModel


@dataclass(frozen=True)
class LlmPolicy:
    """The active generation policy: which model answers and with what settings.

    It carries the model, and the model carries its provider, so a policy is enough
    to say who is going to answer and where it runs.

    Frozen on purpose: changing policy means replacing the whole object, never mutating
    it, so a request already in flight cannot see the settings change halfway through.
    """

    model: LlmModel
    temperature: float = DEFAULT_TEMPERATURE
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS

    def __post_init__(self) -> None:
        if not 0.0 <= self.temperature <= MAX_TEMPERATURE:
            raise InvalidValueError(f"temperature must be between 0 and {MAX_TEMPERATURE}")
        if self.max_output_tokens <= 0:
            raise InvalidValueError("max_output_tokens must be positive")

    def with_model(self, model: LlmModel) -> "LlmPolicy":
        """The same settings pointed at another model: what a fallback attempt is."""
        return LlmPolicy(
            model=model,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
        )
