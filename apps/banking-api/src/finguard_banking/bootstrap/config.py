from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from finguard_banking.domain import constants


class Settings(BaseSettings):
    """Service configuration. Overridden through BANKING_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="BANKING_", env_file=".env", extra="ignore")

    build_sha: str = "unknown"
    build_ref: str = "unknown"
    build_source_sha256: str = "unknown"

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

    # How long a payment preview stands. Long enough for somebody to read it and answer, short
    # enough that the balance it quoted is still roughly true. A product decision, so it is
    # configuration rather than a constant in the adapter that uses it.
    payment_validity_seconds: int = Field(default=300, ge=30, le=3_600)

    # The counterparty for money leaving the bank: a payment to a phone number has no account
    # here, so the other side of the double entry is the clearing account. It is an account that
    # exists in *this* ledger, which makes it a deployment fact — the seed creates one with this
    # reference, and a real bank's would be a real account.
    clearing_account_reference: str = "ES-COMPENSACION"

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
