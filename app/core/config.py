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

    database_url: str = "postgresql+asyncpg://mars:mars@127.0.0.1:5433/mars"
    test_database_url: str = "postgresql+asyncpg://mars:mars@127.0.0.1:5433/mars_test"
    db_connect_timeout_seconds: float = 5.0
    # HTTP request connections only (sync, backfill and loaders are not limited); 0 = off.
    db_statement_timeout_seconds: float = Field(default=5.0, ge=0)
    # How long a request waits for a free pooled connection.
    db_pool_timeout_seconds: float = Field(default=5.0, gt=0)
    # Pool of the engine serving HTTP requests: pooled connections plus overflow.
    db_pool_size: int = Field(default=10, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    # Heavy v2 searches running at the same time on the whole server; an extra one gets 503
    # service_busy at once. Must stay below the pool so light requests always find a connection.
    heavy_query_concurrency: int = Field(default=2, ge=1)

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

    # MSL RAD radiation summaries from the PDS PPI volume MSL-M-RAD-3-RDR-V1.0 (stage 21).
    # Loaded only by the CLI (`rad sync`), never by the application process.
    rad_base_url: str = "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/"
    # Pause between the end of one request to the host and the start of the next.
    rad_pause_seconds: float = Field(default=2.0, ge=1.0)
    # Full attempts at one file (broken body, timeout, 5xx, 429), pauses of 30 s and 120 s.
    rad_file_attempts: int = Field(default=3, ge=1)
    # After the attempts are used up: wait this long, try once more, then stop the run.
    rad_cooldown_seconds: float = Field(default=900.0, ge=0)
    # No bytes for this long ends an attempt; the whole file has RAD_FILE_DEADLINE_SECONDS.
    rad_read_timeout_seconds: float = Field(default=120.0, ge=120.0)
    rad_file_deadline_seconds: float = Field(default=1800.0, gt=0)
    rad_max_file_bytes: int = Field(default=256 * 1024 * 1024, ge=1024)
    rad_max_line_bytes: int = Field(default=64 * 1024, ge=1024)
    # Observation blocks of one file (real files: hundreds); more fails the file.
    rad_max_blocks: int = Field(default=5000, ge=1)
    rad_list_max_bytes: int = Field(default=16 * 1024 * 1024, ge=1024)
    # A listing with fewer files than this share of the known ones deletes nothing.
    rad_min_keep_ratio: float = Field(default=0.9, ge=0, le=1)
    # An observation counting below this share of the sol's median rate is dropped.
    rad_low_rate_ratio: float = Field(default=0.5, ge=0, le=1)
    # Unpacked size limit of an export file read by `rad import`.
    rad_import_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)

    # Weather per sol (stage 17): summaries of the REMS (Curiosity) and MEDA (Perseverance)
    # files that were downloaded to WEATHER_RAW_DIR by scripts/weather_download.py. Read only by
    # the CLI (`weather parse`); the application process never reads the raw files and never
    # calls the archive (the two base URLs only make the links of the answers).
    # A relative path is resolved against the project root.
    weather_raw_dir: Path = Path("data/weather_raw")
    # Processes of `weather parse`; 0 = max(1, cpu_count - 2).
    weather_parse_workers: int = Field(default=0, ge=0)
    # A sol with fewer hours of air temperature than this gets the flag partial_coverage.
    weather_full_hours: int = Field(default=18, ge=1, le=24)
    # Relative humidity above this is an instrument fault (out_of_range); 100..this is kept.
    weather_humidity_max: float = Field(default=200.0, gt=100)
    # `weather parse --prune` and `weather import` that would drop more than 1 - this share of
    # the stored sols are refused.
    weather_min_keep_ratio: float = Field(default=0.9, ge=0, le=1)
    # Unpacked size limit of an export file read by `weather import`.
    weather_import_max_bytes: int = Field(default=256 * 1024 * 1024, ge=1024)
    # Perseverance ground temperature estimate (part 17G, from MEDA DER_TIRS); a kill switch.
    weather_m20_ground_enabled: bool = True
    weather_meda_base_url: str = "https://atmos.nmsu.edu/PDS/data/PDS4/Mars2020/mars2020_meda/"
    weather_rems_base_url: str = "https://atmos.nmsu.edu/PDS/data/mslrem_1001/"
    # Files modified more recently than this are still being written by the downloader.
    weather_min_file_age_seconds: float = Field(default=60.0, ge=0)

    # Build of the Sol Index web interface (stage 10); served at / when index.html exists.
    # A relative path is resolved against the project root.
    frontend_dist_dir: Path = Path("frontend/dist")

    # Origin (scheme://host[:port]) of absolute links and of /developers and /llms.txt; when set
    # the Host header is ignored. Empty: a valid Host is used, else http://localhost.
    public_base_url: str | None = None

    admin_token: SecretStr | None = None
    cors_origins: list[str] = ["*"]

    # Per-IP request limit on /api/v1, /mars-photos/api/v1 and /api/v2 (stage 9).
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = Field(default=60, ge=1)
    rate_limit_per_hour: int = Field(default=2000, ge=1)
    # Requests of one IP served at the same time; an extra one gets 429 at once.
    rate_limit_concurrent: int = Field(default=4, ge=1)
    # Requests of one client address served at the same time on paths outside the API (site,
    # /health, /docs, ...); an extra one gets 429 at once. Separate from the API limiters.
    rate_limit_page_concurrent: int = Field(default=16, ge=1)
    # All IPv6 addresses of one /48 network share a budget this many times larger than one
    # client's (per minute, per hour and concurrent); a request counts in both.
    rate_limit_ipv6_site_factor: int = Field(default=8, ge=1)
    # A v1 response costs one request per started chunk of this many bytes (after gzip).
    rate_limit_v1_bytes_per_request: int = Field(default=262_144, ge=1024)
    # ...or per started chunk of this many bytes of the uncompressed body (also for HEAD and
    # 304); the larger of the two counts. Must be >= the wire unit.
    rate_limit_v1_raw_bytes_per_request: int = Field(default=1_048_576, ge=1024)
    # API keys (stage 20). Off: ``api_key`` / ``X-Api-Key`` are ignored and every request is
    # limited by IP, as before the stage; the key and account admin endpoints are not mounted.
    api_keys_enabled: bool = False
    # Limits of a request with a valid key, counted per account (all its keys share them).
    rate_limit_key_per_minute: int = Field(default=120, ge=1)
    rate_limit_key_per_hour: int = Field(default=10_000, ge=1)
    rate_limit_key_concurrent: int = Field(default=8, ge=1)
    # Active (not revoked) keys per account.
    api_key_max_per_account: int = Field(default=2, ge=1)
    # How long a key lookup (also a negative one) is kept in memory.
    api_key_cache_seconds: float = Field(default=60.0, ge=0)
    # Wrong or revoked keys per IP before requests with a key from it get 429 for the window.
    api_key_auth_max_failures: int = Field(default=20, ge=1)
    api_key_auth_window_minutes: int = Field(default=15, ge=1)
    # Usage counters are kept in memory and written to the database this often.
    api_key_usage_flush_seconds: int = Field(default=60, ge=1)
    api_key_usage_retention_days: int = Field(default=90, ge=1)
    # Accounts with no key created or used for this long are deleted by the daily cleanup.
    api_account_inactive_days: int = Field(default=730, ge=30)
    # Failed admin token checks per IP before admin answers 429 for the rest of the window.
    admin_auth_max_failures: int = Field(default=10, ge=1)
    admin_auth_window_minutes: int = Field(default=15, ge=1)

    # Self-service keys by e-mail (stage 20B); works only with API_KEYS_ENABLED and SMTP_HOST.
    key_signup_enabled: bool = False
    # Requests for a key or a deletion link per client address (IPv6: per /48 network) and hour.
    key_signup_per_ip_per_hour: int = Field(default=5, ge=1)
    # Mails per address and 24 hours (key and deletion mails together).
    key_signup_per_email_per_day: int = Field(default=3, ge=1)
    # Mail requests of the whole server per UTC day (reset at 00:00 UTC), all valid requests
    # counted. 50 is a temporary measure (owner's decision, 2026-11-24): a margin below the
    # provider's limit.
    key_signup_global_per_day: int = Field(default=50, ge=1)
    key_token_ttl_minutes: int = Field(default=60, ge=1)
    key_mail_max_attempts: int = Field(default=4, ge=1)
    # Pauses before the 2nd, 3rd... attempt, comma separated; the last one repeats.
    key_mail_retry_minutes: str = "1,5,15"
    # A failed send this recent (and no success after it) makes the answers say "delayed".
    # Put the send job into the scheduler (tests switch it off and call run_once()).
    key_mail_job_enabled: bool = True
    key_mail_degraded_minutes: int = Field(default=15, ge=1)
    # Comma separated domains that cannot get a key (exact match, lowercase).
    key_email_blocked_domains: str = ""
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    # Implicit TLS (port 465); then STARTTLS is not used.
    smtp_ssl: bool = False
    smtp_timeout_seconds: float = Field(default=15.0, gt=0)
    mail_from: str | None = None
    mail_from_name: str = "Sol Index"
    mail_reply_to: str | None = None
    # Named in the text about data ("mails are sent through ...").
    mail_provider_name: str = ""

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
        if self.api_keys_enabled:
            self._check_key_limits()
        if self.rate_limit_v1_raw_bytes_per_request < self.rate_limit_v1_bytes_per_request:
            raise ValueError(
                "RATE_LIMIT_V1_RAW_BYTES_PER_REQUEST must be >= RATE_LIMIT_V1_BYTES_PER_REQUEST"
            )
        return self

    def _check_key_limits(self) -> None:
        """Key limits matter only with API_KEYS_ENABLED (the values are unused otherwise)."""
        if self.rate_limit_key_per_hour < self.rate_limit_key_per_minute:
            raise ValueError("RATE_LIMIT_KEY_PER_HOUR must be >= RATE_LIMIT_KEY_PER_MINUTE")
        if (
            self.rate_limit_key_per_minute < self.rate_limit_per_minute
            or self.rate_limit_key_per_hour < self.rate_limit_per_hour
            or self.rate_limit_key_concurrent < self.rate_limit_concurrent
        ):
            raise ValueError("RATE_LIMIT_KEY_* must be >= the matching RATE_LIMIT_* values")

    @model_validator(mode="after")
    def _check_heavy_query_concurrency(self) -> Self:
        check_pool_settings(self)
        return self

    @model_validator(mode="after")
    def _check_signup(self) -> Self:
        check_signup_settings(self)
        return self

    @property
    def key_signup_available(self) -> bool:
        """The mail endpoints exist: keys and sign-up on, SMTP configured."""
        return self.api_keys_enabled and self.key_signup_enabled and bool(self.smtp_host)

    @property
    def key_mail_retry_schedule(self) -> list[int]:
        """Minutes to wait after the 1st, 2nd... failed attempt (never empty)."""
        values = [int(p) for p in self.key_mail_retry_minutes.split(",") if p.strip().isdigit()]
        return values or [1]

    @property
    def key_email_blocked_domain_set(self) -> frozenset[str]:
        parts = (p.strip().lower() for p in self.key_email_blocked_domains.split(","))
        return frozenset(p for p in parts if p)

    @property
    def smtp_password_value(self) -> str:
        """The SMTP password, "" when unset. Never log it."""
        password = self.smtp_password
        return password.get_secret_value() if password is not None else ""

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


MAX_DB_POOL = 50
# API pool (size + overflow) leaves room under Postgres max_connections=100 for the worker,
# pg_dump and admin sessions.
MAX_DB_CONNECTIONS = 50


def check_pool_settings(settings: Settings) -> None:
    """Pool bounds and the gate rule; ``create_app`` repeats it for ``model_copy`` settings."""
    if not 1 <= settings.db_pool_size <= MAX_DB_POOL:
        raise ValueError(f"DB_POOL_SIZE must be 1..{MAX_DB_POOL}")
    if not 0 <= settings.db_max_overflow <= MAX_DB_POOL:
        raise ValueError(f"DB_MAX_OVERFLOW must be 0..{MAX_DB_POOL}")
    if settings.db_pool_size + settings.db_max_overflow > MAX_DB_CONNECTIONS:
        raise ValueError(f"DB_POOL_SIZE + DB_MAX_OVERFLOW must be <= {MAX_DB_CONNECTIONS}")
    if settings.heavy_query_concurrency < 1:
        raise ValueError("HEAVY_QUERY_CONCURRENCY must be >= 1")
    if settings.heavy_query_concurrency >= settings.db_pool_size + settings.db_max_overflow:
        raise ValueError("HEAVY_QUERY_CONCURRENCY must be < DB_POOL_SIZE + DB_MAX_OVERFLOW")


def check_signup_settings(settings: Settings) -> None:
    """Sign-up needs the mail settings; the error names the missing variable (never a value).
    ``create_app`` repeats it for ``model_copy`` settings."""
    if not settings.key_signup_enabled:
        return
    for name, value in (
        ("SMTP_HOST", settings.smtp_host),
        ("MAIL_FROM", settings.mail_from),
        ("PUBLIC_BASE_URL", settings.public_base_url),
    ):
        if not (value or "").strip():
            raise ValueError(f"{name} is required when KEY_SIGNUP_ENABLED is true")


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
