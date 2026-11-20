"""Stage 13 settings (SAMPLES_*): defaults and environment (stage-13 proposal, section 5)."""

import pytest

from app.core.config import Settings
from tests._samples import SAMPLES_URL

pytestmark = pytest.mark.unit


def make(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_samples_settings_defaults() -> None:
    settings = make()

    assert settings.samples_sync_enabled is True
    assert settings.samples_url == SAMPLES_URL
    assert settings.samples_refresh_hours == 24
    assert settings.samples_max_bytes == 4 * 1024 * 1024
    assert settings.samples_stale_days == 3


def test_samples_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAMPLES_SYNC_ENABLED", "false")
    monkeypatch.setenv("SAMPLES_URL", "https://samples.example.test/page/")
    monkeypatch.setenv("SAMPLES_REFRESH_HOURS", "6")
    monkeypatch.setenv("SAMPLES_MAX_BYTES", "2048")
    monkeypatch.setenv("SAMPLES_STALE_DAYS", "5")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.samples_sync_enabled is False
    assert settings.samples_url == "https://samples.example.test/page/"
    assert settings.samples_refresh_hours == 6
    assert settings.samples_max_bytes == 2048
    assert settings.samples_stale_days == 5
