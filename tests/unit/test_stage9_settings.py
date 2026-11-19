"""Stage 9 settings: names and defaults agreed for the rate limit, freshness and alerts."""

from typing import Any

import pytest
from pydantic import SecretStr

from app.core.config import Settings

pytestmark = pytest.mark.unit

DEFAULTS: list[tuple[str, Any]] = [
    ("rate_limit_enabled", True),
    ("rate_limit_per_minute", 60),
    ("rate_limit_per_hour", 2000),
    ("admin_auth_max_failures", 10),
    ("admin_auth_window_minutes", 15),
    ("sync_stale_hours", 24),
    ("photos_stale_days", 21),
    ("telegram_bot_token", None),
    ("telegram_chat_id", None),
    ("telegram_api_base", "https://api.telegram.org"),
    ("alert_check_interval_minutes", 60),
]


@pytest.mark.parametrize(("field", "expected"), DEFAULTS, ids=[d[0] for d in DEFAULTS])
def test_settings_stage9_field_has_agreed_default(field: str, expected: Any) -> None:
    assert field in Settings.model_fields
    assert Settings.model_fields[field].default == expected


ENV_CASES: list[tuple[str, str, Any]] = [
    ("RATE_LIMIT_ENABLED", "rate_limit_enabled", False),
    ("RATE_LIMIT_PER_MINUTE", "rate_limit_per_minute", 5),
    ("RATE_LIMIT_PER_HOUR", "rate_limit_per_hour", 500),
    ("ADMIN_AUTH_MAX_FAILURES", "admin_auth_max_failures", 3),
    ("ADMIN_AUTH_WINDOW_MINUTES", "admin_auth_window_minutes", 30),
    ("SYNC_STALE_HOURS", "sync_stale_hours", 12),
    ("PHOTOS_STALE_DAYS", "photos_stale_days", 20),
    ("TELEGRAM_CHAT_ID", "telegram_chat_id", "-100500"),
    ("TELEGRAM_API_BASE", "telegram_api_base", "https://telegram.example.test"),
    ("ALERT_CHECK_INTERVAL_MINUTES", "alert_check_interval_minutes", 5),
]


@pytest.mark.parametrize(("env", "field", "expected"), ENV_CASES, ids=[c[0] for c in ENV_CASES])
def test_settings_stage9_field_reads_env(
    monkeypatch: pytest.MonkeyPatch, env: str, field: str, expected: Any
) -> None:
    monkeypatch.setenv(env, "false" if expected is False else str(expected))

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert getattr(settings, field) == expected


def test_settings_telegram_token_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "123456:AAH-not-a-real-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert isinstance(settings.telegram_bot_token, SecretStr)
    assert settings.telegram_bot_token.get_secret_value() == token
    assert token not in repr(settings)
    assert token not in str(settings.model_dump())


def test_settings_per_hour_below_per_minute_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "59")

    with pytest.raises(ValueError, match="(?i)rate_limit_per_hour"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_per_hour_equal_to_per_minute_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "60")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert (settings.rate_limit_per_minute, settings.rate_limit_per_hour) == (60, 60)


def test_settings_health_sync_cache_seconds_default_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings.model_fields["health_sync_cache_seconds"].default == 30
    monkeypatch.setenv("HEALTH_SYNC_CACHE_SECONDS", "0")

    assert Settings(_env_file=None).health_sync_cache_seconds == 0  # type: ignore[call-arg]
