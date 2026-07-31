from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from finguard_banking.domain import constants


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

    # Query limits this deployment applies. They can only narrow the domain ceilings in
    # domain/constants.py, never widen them: a configuration mistake cannot turn into a
    # query that returns half the ledger.
    max_supporting_movements: int = Field(default=constants.MAX_SUPPORTING_MOVEMENTS, ge=0)
    max_groups: int = Field(default=constants.MAX_GROUPS, ge=1)

    @property
    def supporting_movements_limit(self) -> int:
        return min(self.max_supporting_movements, constants.MAX_SUPPORTING_MOVEMENTS)

    @property
    def groups_limit(self) -> int:
        return min(self.max_groups, constants.MAX_GROUPS)


@lru_cache
def get_settings() -> Settings:
    return Settings()
