import pytest

from app.core.config import Settings

pytestmark = pytest.mark.unit


def test_settings_defaults_without_env_file_are_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.sync_enabled is False
    assert settings.sync_overlap_sols == 7
    assert settings.admin_enabled is False
    assert settings.database_url != settings.test_database_url


def test_settings_read_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_OVERLAP_SOLS", "3")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173"]')

    settings = Settings(_env_file=None)

    assert settings.sync_overlap_sols == 3
    assert settings.admin_enabled is True
    assert settings.cors_origins == ["http://localhost:5173"]


def test_empty_admin_token_keeps_admin_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "")

    assert Settings(_env_file=None).admin_enabled is False
