"""Stage 11 settings (LOCATION_*): defaults and bounds (stage plan section 9)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

pytestmark = pytest.mark.unit


def make(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_location_settings_defaults() -> None:
    settings = make()

    assert settings.location_sync_enabled is True
    assert settings.location_base_url == "https://mars.nasa.gov/mmgis-maps/"
    assert settings.location_refresh_hours == 24
    assert settings.location_max_bytes == 16 * 1024 * 1024
    assert settings.location_min_keep_ratio == 0.9
    assert settings.location_stale_days == 3


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("location_refresh_hours", 0),
        ("location_max_bytes", 1023),
        ("location_min_keep_ratio", -0.01),
        ("location_min_keep_ratio", 1.01),
        ("location_stale_days", 0),
    ],
    ids=lambda v: str(v),
)
def test_location_setting_out_of_bounds_is_rejected(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make(**{name: value})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("location_refresh_hours", 1),
        ("location_max_bytes", 1024),
        ("location_min_keep_ratio", 0),
        ("location_min_keep_ratio", 1),
        ("location_stale_days", 1),
    ],
    ids=lambda v: str(v),
)
def test_location_setting_at_the_bound_is_accepted(name: str, value: object) -> None:
    assert getattr(make(**{name: value}), name) == value


def test_location_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCATION_SYNC_ENABLED", "false")
    monkeypatch.setenv("LOCATION_BASE_URL", "https://maps.example.test/")
    monkeypatch.setenv("LOCATION_REFRESH_HOURS", "6")

    settings = Settings(_env_file=None)

    assert settings.location_sync_enabled is False
    assert settings.location_base_url == "https://maps.example.test/"
    assert settings.location_refresh_hours == 6
