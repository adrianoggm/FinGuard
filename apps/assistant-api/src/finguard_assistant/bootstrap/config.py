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
    # Not caution for its own sake. Both reasons produce a confident, plausible, wrong
    # figure rather than an error, which is the one failure mode nobody downstream
    # catches. Documenting a risk is not controlling it; a flag is, and it makes turning
    # this on a decision somebody takes rather than a default nobody chose.
    #
    # One of the two has moved: refusing what it cannot express was 0% on the held-out
    # set and is 60% since the planner changed model. Better is not the bar, and it is a
    # model's disposition rather than a guarantee — two in five still come back as a real
    # figure answering a question about a ratio. The known live injection has not moved
    # at all, even though the aggregate injection group score changed with the model.
    #
    # It comes off when unsupported has a deterministic guard and injection clears the
    # agreed bar — see docs/next.md.
    questions_enabled: bool = False

    # The model that turns a sentence into a plan. Separate from the catalogue on
    # purpose: the catalogue chooses who *answers*, and planning is a different job with
    # a different measurement — scripts/evaluate_planning.py is what picks this value.
    #
    # Eleven models were measured. This is not the most accurate of them: qwen2.5-coder
    # at 14B scores 92.4% held out against this one's 89.9%. It is the one that stopped
    # answering questions it cannot answer — `unsupported` went from 1/5 to 3/5 without
    # the over-asking that usually comes with it. A missing answer is visible to the
    # user; a confident wrong figure is not.
    #
    # qwen2.5-coder:7b stays the baseline every change is measured against, and it is
    # the one to fall back to: 4.7 GB fits a 6.6 GB budget whole, and this does not.
    # See evaluation/planning/RESULTS.md.
    planner_model: str = "qwen3.5:9b"

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
                # Configuration errors often end up in CI and container logs. The
                # operator already has the table in front of them; repeating even a
                # prefix of the shared secret there only creates another copy to leak.
                raise ValueError("a development token is configured twice")
            table[token] = Caller(holder_reference=holder, account_id=UUID(account))
        return table

    def catalogue(self) -> TomlModelCatalogue:
        if self.llm_catalogue_file is None:
            return TomlModelCatalogue.built_in()
        return TomlModelCatalogue.from_file(self.llm_catalogue_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()
