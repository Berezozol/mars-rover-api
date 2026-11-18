"""Application settings. Every configurable value is read from here."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Validation errors must never echo secrets such as ADMIN_TOKEN.
        hide_input_in_errors=True,
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

    @field_validator("admin_token", mode="after")
    @classmethod
    def _check_admin_token(cls, value: SecretStr | None) -> SecretStr | None:
        token = check_admin_token(value)
        return SecretStr(token) if token else None

    @property
    def admin_token_value(self) -> str:
        """The stripped admin token, "" when admin is disabled."""
        return check_admin_token(self.admin_token)

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_token_value)


MIN_ADMIN_TOKEN_LENGTH = 16


def check_admin_token(value: SecretStr | None) -> str:
    """Stripped token; "" if unset or empty (admin disabled).

    ``ValueError`` (without the value) if it is set but blank or shorter than 16 characters
    after stripping: a misconfiguration, not a silent "admin off". Also called by create_app,
    since ``model_copy(update=...)`` skips field validators.
    """
    raw = value.get_secret_value() if value is not None else ""
    if not raw:
        return ""
    token = raw.strip()
    if len(token) < MIN_ADMIN_TOKEN_LENGTH:
        raise ValueError(
            f"ADMIN_TOKEN must be at least {MIN_ADMIN_TOKEN_LENGTH} non-blank characters long "
            "(leave it empty to disable the admin endpoints)"
        )
    return token


@lru_cache
def get_settings() -> Settings:
    return Settings()
