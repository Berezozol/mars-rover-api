"""Application settings. Every configurable value is read from here."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Mars Rover API"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://mars:mars@localhost:5433/mars"
    test_database_url: str = "postgresql+asyncpg://mars:mars@localhost:5433/mars_test"
    db_connect_timeout_seconds: float = 5.0

    sync_enabled: bool = False
    sync_interval_minutes: int = 360
    sync_overlap_sols: int = 7

    upstream_timeout_seconds: float = 30.0
    upstream_max_rps: float = 1.0
    upstream_user_agent: str = "mars-rover-api/0.1 (personal project)"

    admin_token: SecretStr | None = None
    cors_origins: list[str] = ["*"]

    @property
    def admin_enabled(self) -> bool:
        return self.admin_token is not None and bool(self.admin_token.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
