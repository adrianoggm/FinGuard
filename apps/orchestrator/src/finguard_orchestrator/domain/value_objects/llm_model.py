from dataclasses import dataclass

from finguard_orchestrator.domain.errors import InvalidValueError


@dataclass(frozen=True)
class LlmModel:
    """A model in the catalogue, and the provider it comes from.

    The unit of selection is the model, not the provider: two models of the same
    provider are different choices, and the same model name can be served by more
    than one provider. Because the model carries its provider, anything holding a
    model can say where an answer came from without a second lookup.
    """

    alias: str
    provider: str
    model: str

    def __post_init__(self) -> None:
        for field, value in (
            ("alias", self.alias),
            ("provider", self.provider),
            ("model", self.model),
        ):
            if not value or not value.strip():
                raise InvalidValueError(f"LlmModel.{field} cannot be empty")

    def __str__(self) -> str:
        return f"{self.alias} ({self.provider}:{self.model})"
