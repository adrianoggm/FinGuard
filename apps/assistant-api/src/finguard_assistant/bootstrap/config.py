from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from finguard_assistant.infrastructure import TomlModelCatalogue


class Settings(BaseSettings):
    """Service configuration. Overridden through ASSISTANT_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="ASSISTANT_", env_file=".env", extra="ignore", protected_namespaces=()
    )

    # Path to the model catalogue. Which models exist, which provider serves each one
    # and what happens when one fails are configuration, not code (see
    # infrastructure/config/toml_model_catalogue.py). Unset means the built-in echo
    # catalogue, so the service starts with no file at all.
    llm_catalogue_file: Path | None = None

    def catalogue(self) -> TomlModelCatalogue:
        if self.llm_catalogue_file is None:
            return TomlModelCatalogue.built_in()
        return TomlModelCatalogue.from_file(self.llm_catalogue_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()
