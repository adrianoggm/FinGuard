from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from finguard_assistant.domain.llm_policy import LlmPolicy


class Settings(BaseSettings):
    """Service configuration. Overridden through ASSISTANT_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="ASSISTANT_", env_file=".env", extra="ignore", protected_namespaces=()
    )

    # Default policy at startup. At runtime it is swapped over HTTP.
    llm_provider: str = "echo"
    llm_model: str = "echo-1"
    llm_temperature: float = 0.2
    llm_max_output_tokens: int = 1024

    def default_policy(self) -> LlmPolicy:
        return LlmPolicy(
            provider=self.llm_provider,
            model=self.llm_model,
            temperature=self.llm_temperature,
            max_output_tokens=self.llm_max_output_tokens,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
