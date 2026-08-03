"""Test helpers. A real package, not a conftest imported as a module."""

from finguard_orchestrator.domain.enums import FallbackPolicy
from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.value_objects.llm_model import LlmModel
from finguard_orchestrator.infrastructure.config.toml_model_catalogue import TomlModelCatalogue

PRIMARY = LlmModel(alias="primary", provider="flaky", model="big")
BACKUP = LlmModel(alias="backup", provider="echo", model="small")


class FakeProvider:
    """A provider that answers, or fails on demand."""

    def __init__(self, name: str, *, fails: bool = False) -> None:
        self.name = name
        self._fails = fails
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self, prompt: str, *, model: str, temperature: float, max_output_tokens: int
    ) -> str:
        self.calls.append((model, prompt))
        if self._fails:
            raise ProviderFailedError(self.name, "asked to fail")
        return f"[{self.name}:{model}] {prompt}"


def catalogue(
    *models: LlmModel,
    fallback: FallbackPolicy = FallbackPolicy.NEXT,
    default: str | None = None,
) -> TomlModelCatalogue:
    chosen = models or (PRIMARY, BACKUP)
    return TomlModelCatalogue(
        models=chosen,
        fallback=fallback,
        default_alias=default or chosen[0].alias,
        temperature=0.2,
        max_output_tokens=1024,
    )
