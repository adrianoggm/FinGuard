"""Loads the model catalogue from a TOML file.

TOML because `tomllib` is in the standard library: a configuration file that adds no
dependency. The file answers the three questions the review asked for — which
providers are available, which models each one serves, and what happens when one
fails:

    fallback = "NEXT"            # FAIL | NEXT
    default = "echo-1"
    temperature = 0.2
    max_output_tokens = 1024

    [[models]]
    alias = "echo-1"
    provider = "echo"
    model = "echo-1"

    [[models]]
    alias = "echo-fast"
    provider = "echo"
    model = "echo-2"

Two models of the same provider are two entries: the unit is the model. With no file
configured the catalogue falls back to a single `echo` model, so the service starts
out of the box.
"""

import tomllib
from pathlib import Path

from finguard_assistant.domain.enums import FallbackPolicy
from finguard_assistant.domain.errors import InvalidValueError, UnknownModelError
from finguard_assistant.domain.value_objects.llm_model import LlmModel
from finguard_assistant.domain.value_objects.llm_policy import LlmPolicy

BUILT_IN = LlmModel(alias="echo-1", provider="echo", model="echo-1")


class TomlModelCatalogue:
    """The catalogue this deployment may use, in fallback order."""

    def __init__(
        self,
        models: tuple[LlmModel, ...],
        fallback: FallbackPolicy,
        default_alias: str,
        temperature: float,
        max_output_tokens: int,
    ) -> None:
        if not models:
            raise InvalidValueError("the catalogue needs at least one model")
        self._models = models
        self._fallback = fallback
        self._by_alias = {model.alias: model for model in models}
        if default_alias not in self._by_alias:
            raise UnknownModelError(default_alias, sorted(self._by_alias))
        self._default_alias = default_alias
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    @classmethod
    def built_in(cls) -> "TomlModelCatalogue":
        """The catalogue used when no file is configured: echo only."""
        return cls(
            models=(BUILT_IN,),
            fallback=FallbackPolicy.FAIL,
            default_alias=BUILT_IN.alias,
            temperature=0.2,
            max_output_tokens=1024,
        )

    @classmethod
    def from_file(cls, path: Path) -> "TomlModelCatalogue":
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("models", [])
        models = tuple(
            LlmModel(alias=entry["alias"], provider=entry["provider"], model=entry["model"])
            for entry in entries
        )
        aliases = [model.alias for model in models]
        if len(set(aliases)) != len(aliases):
            raise InvalidValueError(f"duplicate aliases in the catalogue: {aliases}")

        return cls(
            models=models,
            fallback=FallbackPolicy(raw.get("fallback", FallbackPolicy.FAIL.value)),
            default_alias=raw.get("default", aliases[0] if aliases else ""),
            temperature=float(raw.get("temperature", 0.2)),
            max_output_tokens=int(raw.get("max_output_tokens", 1024)),
        )

    @property
    def fallback(self) -> FallbackPolicy:
        return self._fallback

    def models(self) -> tuple[LlmModel, ...]:
        return self._models

    def default(self) -> LlmModel:
        return self._by_alias[self._default_alias]

    def get(self, alias: str) -> LlmModel:
        model = self._by_alias.get(alias)
        if model is None:
            raise UnknownModelError(alias, sorted(self._by_alias))
        return model

    def default_policy(self) -> LlmPolicy:
        return LlmPolicy(
            model=self.default(),
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
        )
