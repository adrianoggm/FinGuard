from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GYM_", extra="ignore")

    style: Literal["norte", "sur", "legacy"]
    a2a_url: str
    public_url: str
    api_key: str | None = None
    bearer_token: str | None = None
    seed: int = 0
    timezone: str = "Europe/Madrid"
    today: date | None = None

    def model_post_init(self, __context: object) -> None:
        if self.style == "norte" and not self.api_key:
            raise ValueError("GYM_API_KEY is required for the norte style")
        if self.style == "sur" and not self.bearer_token:
            raise ValueError("GYM_BEARER_TOKEN is required for the sur style")

    def current_day(self) -> date:
        return self.today or datetime.now(ZoneInfo(self.timezone)).date()
