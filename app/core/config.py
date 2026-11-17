"""Application settings. Every configurable value is read from here."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
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
    sync_chunk_sols: int = Field(default=10, ge=1)

    upstream_timeout_seconds: float = Field(default=30.0, gt=0)
    upstream_max_rps: float = 1.0
    upstream_user_agent: str = "mars-rover-api/0.1 (personal project)"
    upstream_max_retries: int = Field(default=4, ge=0)
    upstream_retry_base_seconds: float = Field(default=1.0, ge=0)
    upstream_max_pages: int = Field(default=200, ge=1)

    m2020_feed_url: str = "https://mars.nasa.gov/rss/api/"
    msl_feed_url: str = "https://mars.nasa.gov/api/v1/raw_image_items/"
    # The Perseverance feed caps `num` at 100.
    m2020_page_size: int = Field(default=100, ge=1, le=100)
    msl_page_size: int = Field(default=200, ge=1)

    admin_token: SecretStr | None = None
    cors_origins: list[str] = ["*"]

    @property
    def admin_enabled(self) -> bool:
        return self.admin_token is not None and bool(self.admin_token.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
