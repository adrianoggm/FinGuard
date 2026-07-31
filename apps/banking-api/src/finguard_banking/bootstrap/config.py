from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration. Overridden through BANKING_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="BANKING_", env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql://finguard_banking:finguard_banking@127.0.0.1:5442/finguard_banking"
    )

    # Read-only connection for the query engine (vision.md §8.6). A different role on
    # purpose: with these credentials nothing can be written and no table can be read
    # directly, only the authorised views.
    query_database_url: str = (
        "postgresql://finguard_query_reader:finguard_query_reader@127.0.0.1:5442/finguard_banking"
    )

    # Time zone used to group reports by day, week or month. Under UTC a purchase made
    # at 23:30 in Spain would fall on the next day.
    reporting_timezone: str = "Europe/Madrid"


@lru_cache
def get_settings() -> Settings:
    return Settings()
