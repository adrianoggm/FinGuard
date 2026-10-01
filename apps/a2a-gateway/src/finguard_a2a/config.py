from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEVELOPMENT_TOKEN = "development-only-a2a-gateway-token"


class Settings(BaseSettings):
    """`A2A_GATEWAY_*`. Credentials are not here: each is read by agent id (`CREDENTIAL_<ID>`)."""

    model_config = SettingsConfigDict(env_prefix="A2A_GATEWAY_", extra="ignore")
    service_version: str = "0.1.0"
    agents: str = ""
    token: SecretStr = SecretStr("")
    card_ttl_seconds: float = 300
    timeout_seconds: float = 10
    max_text_bytes: int = 16 * 1024
    max_data_bytes: int = 64 * 1024
    otel_enabled: bool = False
    otel_endpoint: str = ""

    def model_post_init(self, __context: object) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("A2A_GATEWAY_TIMEOUT_SECONDS must be positive")
        if self.card_ttl_seconds < 0:
            raise ValueError("A2A_GATEWAY_CARD_TTL_SECONDS must not be negative")
        if self.max_text_bytes <= 0 or self.max_data_bytes <= 0:
            raise ValueError("the reply limits must be positive")
