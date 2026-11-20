"""Application settings. Every configurable value is read from here."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """The directory containing pyproject.toml above this file (else the package's parent)."""
    here = Path(__file__).resolve()
    return next((p for p in here.parents if (p / "pyproject.toml").is_file()), here.parents[2])


PROJECT_ROOT = _project_root()


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
    # RSS feed (stage 16): only photos stored by the regular sync within this many days.
    feed_window_days: int = Field(default=30, ge=1)
    # Sols per backfill batch; each batch is one sync run (CLI `backfill`).
    backfill_batch_sols: int = Field(default=100, ge=1)
    # When the rover is locked by another sync: retry every N seconds, give up after M.
    backfill_lock_retry_seconds: float = Field(default=30.0, gt=0)
    backfill_lock_max_wait_seconds: float = Field(default=600.0, ge=0)
    # Stop the backfill after this many consecutive batches whose run failed.
    backfill_max_failed_batches: int = Field(default=3, ge=1)

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

    # Spirit/Opportunity: one-time import from the PDS Imaging Node index files (stage 8).
    mer_pds_base_url: str = "https://planetarydata.jpl.nasa.gov/img/data/mer/"
    # Local cache of <volume>/index/edrindex.{lbl,tab}; filled by `mer-download`.
    # A relative path is resolved against the project root, not the working directory.
    mer_cache_dir: Path = Path("data/mer")

    # Full-size Spirit/Opportunity frames: PDS3 .img converted to JPEG on demand (stage 9a).
    # A relative cache path is resolved against the project root.
    mer_image_cache_dir: Path = Path("data/mer-images")
    mer_image_cache_max_mb: int = Field(default=5120, ge=1)
    mer_convert_concurrency: int = Field(default=2, ge=1)
    mer_convert_timeout_seconds: float = Field(default=60, gt=0)
    # Distinct frames waiting for a conversion slot; more get 503 converter_busy.
    mer_convert_queue: int = Field(default=8, ge=0)
    # Request rate to PDS of the converter's own client (not shared with the sync client).
    mer_pds_max_rps: float = Field(default=2.0, gt=0)

    # Rover positions: MMGIS waypoint/traverse files, fetched at most once a day (stage 11).
    # Active only together with SYNC_ENABLED.
    location_sync_enabled: bool = True
    location_base_url: str = "https://mars.nasa.gov/mmgis-maps/"
    location_refresh_hours: int = Field(default=24, ge=1)
    location_max_bytes: int = Field(default=16 * 1024 * 1024, ge=1024)
    # A new file with fewer rows than this share of the stored ones is rejected.
    location_min_keep_ratio: float = Field(default=0.9, ge=0, le=1)
    # Alert when no check of a location file succeeded for this many days.
    location_stale_days: int = Field(default=3, ge=1)

    # Perseverance sample table of the NASA "Mars Rock Samples" page, checked at most once a
    # day (stage 13). Active only together with SYNC_ENABLED.
    samples_sync_enabled: bool = True
    samples_url: str = "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/"
    samples_refresh_hours: int = Field(default=24, ge=1)
    samples_max_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    # Alert when no check of the samples page succeeded for this many days.
    samples_stale_days: int = Field(default=3, ge=1)

    # Build of the Sol Index web interface (stage 10); served at / when index.html exists.
    # A relative path is resolved against the project root.
    frontend_dist_dir: Path = Path("frontend/dist")

    admin_token: SecretStr | None = None
    cors_origins: list[str] = ["*"]

    # Per-IP request limit on /api/v1, /mars-photos/api/v1 and /api/v2 (stage 9).
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = Field(default=60, ge=1)
    rate_limit_per_hour: int = Field(default=2000, ge=1)
    # Failed admin token checks per IP before admin answers 429 for the rest of the window.
    admin_auth_max_failures: int = Field(default=10, ge=1)
    admin_auth_window_minutes: int = Field(default=15, ge=1)

    # Data freshness (/health/sync and the alerts).
    sync_stale_hours: int = Field(default=24, ge=1)
    photos_stale_days: int = Field(default=21, ge=1)
    # /health/sync keeps its result in memory this long (0 = query the database every time).
    health_sync_cache_seconds: int = Field(default=30, ge=0)

    # Telegram alerts; enabled only when both the token and the chat id are set.
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    alert_check_interval_minutes: int = Field(default=60, ge=1)

    @field_validator("mer_cache_dir", "mer_image_cache_dir", "frontend_dist_dir", mode="after")
    @classmethod
    def _resolve_cache_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("admin_token", mode="after")
    @classmethod
    def _check_admin_token(cls, value: SecretStr | None) -> SecretStr | None:
        token = check_admin_token(value)
        return SecretStr(token) if token else None

    @model_validator(mode="after")
    def _check_rate_limits(self) -> Self:
        if self.rate_limit_per_hour < self.rate_limit_per_minute:
            raise ValueError("RATE_LIMIT_PER_HOUR must be >= RATE_LIMIT_PER_MINUTE")
        return self

    @property
    def admin_token_value(self) -> str:
        """The stripped admin token, "" when admin is disabled."""
        return check_admin_token(self.admin_token)

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_token_value)

    @property
    def telegram_token_value(self) -> str:
        """The stripped bot token, "" when unset. Never log it."""
        token = self.telegram_bot_token
        return token.get_secret_value().strip() if token is not None else ""

    @property
    def alerts_enabled(self) -> bool:
        return bool(self.telegram_token_value and (self.telegram_chat_id or "").strip())


MIN_ADMIN_TOKEN_LENGTH = 16
# Placeholder marker of the example env files; such a token must never go live.
ADMIN_TOKEN_PLACEHOLDER = "change-me"


def check_admin_token(value: SecretStr | None) -> str:
    """Stripped token; "" if unset or empty (admin disabled).

    ``ValueError`` (without the value) if it is set but blank or shorter than 16 characters
    after stripping, or still contains the "change-me" placeholder (any case): a
    misconfiguration, not a silent "admin off". Also called by create_app, since
    ``model_copy(update=...)`` skips field validators.
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
    if ADMIN_TOKEN_PLACEHOLDER in token.lower():
        raise ValueError(
            f"ADMIN_TOKEN still contains the '{ADMIN_TOKEN_PLACEHOLDER}' placeholder; "
            "set a random value (or leave it empty to disable the admin endpoints)"
        )
    return token


@lru_cache
def get_settings() -> Settings:
    return Settings()
