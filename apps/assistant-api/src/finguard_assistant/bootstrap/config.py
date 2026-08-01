from functools import lru_cache
from pathlib import Path
from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict

from finguard_assistant.domain.value_objects.caller import Caller
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

    # Where the local runtime listens. Configurable because inside a container 127.0.0.1
    # is the container: compose points this at host.docker.internal so the service can
    # reach the daemon on the machine, which is where the GPU is.
    ollama_base_url: str = "http://127.0.0.1:11434"

    # Development identity, as "token:holder:account_uuid" entries. Not the design —
    # §12.2 asks for OAuth 2.0 with short-lived tokens — but the seam is real: the
    # account comes from the credential, so a caller cannot name someone else's.
    #
    # Set through ASSISTANT_DEV_TOKENS. It has no default on purpose: a service that
    # ships with a built-in credential ships with a credential everyone knows.
    dev_tokens: tuple[str, ...] = ()

    # The financial endpoints, off unless a deployment turns them on.
    #
    # Not caution for its own sake. The evaluation says the assistant refuses nothing it
    # cannot express (0% on the held-out set) and that one prompt injection still changes
    # a field, and both of those produce a confident, plausible, wrong figure rather than
    # an error. Documenting a risk is not controlling it; a flag is, and it makes turning
    # this on a decision somebody takes rather than a default nobody chose.
    #
    # It comes off when unsupported has a deterministic guard and injection clears the
    # agreed bar — see docs/next.md.
    questions_enabled: bool = False

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

    def callers(self) -> dict[str, Caller]:
        """Parses the token table. Fails loudly on a malformed entry rather than
        skipping it: a silently dropped token is a caller who cannot log in and an
        afternoon spent looking at the wrong layer."""
        table: dict[str, Caller] = {}
        for entry in self.dev_tokens:
            parts = entry.split(":")
            if len(parts) != 3:
                raise ValueError(f"a token entry is token:holder:account_uuid, got {entry!r}")
            token, holder, account = parts
            if token in table:
                # Two entries sharing a secret is a typo, and the quiet outcome is the
                # dangerous one: the token would resolve to whichever account came last.
                raise ValueError(f"token {token[:6]}... is configured twice")
            table[token] = Caller(holder_reference=holder, account_id=UUID(account))
        return table

    def catalogue(self) -> TomlModelCatalogue:
        if self.llm_catalogue_file is None:
            return TomlModelCatalogue.built_in()
        return TomlModelCatalogue.from_file(self.llm_catalogue_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()
