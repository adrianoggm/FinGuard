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

    # Where the ledger lives. Localhost by default: development credentials never leave
    # 127.0.0.1 (rule 13), and this service is useless without banking anyway.
    banking_base_url: str = "http://127.0.0.1:8001"

    # The model that turns a sentence into a plan. Separate from the catalogue on
    # purpose: the catalogue chooses who *answers*, and planning is a different job with
    # a different measurement — scripts/evaluate_planning.py is what picks this value.
    planner_model: str = "qwen2.5-coder:7b"

    # The categories that exist in the data. They belong to banking; until there is a
    # legitimate place to read them from, they are stated here and drift shows up as a
    # rejected plan rather than as a wrong figure.
    categories: tuple[str, ...] = (
        "GROCERIES",
        "TRANSPORT_FUEL",
        "RESTAURANTS",
        "TRANSPORT_PUBLIC",
        "HEALTH",
        "LEISURE",
        "SUBSCRIPTIONS",
        "HOUSING_RENT",
        "SALARY",
        "TRANSFER_BIZUM",
    )

    def catalogue(self) -> TomlModelCatalogue:
        if self.llm_catalogue_file is None:
            return TomlModelCatalogue.built_in()
        return TomlModelCatalogue.from_file(self.llm_catalogue_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()
