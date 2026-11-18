from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_settings_defaults_without_env_file_are_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.sync_enabled is False
    assert settings.sync_overlap_sols == 7
    assert settings.admin_enabled is False
    assert settings.database_url != settings.test_database_url


def test_settings_read_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_OVERLAP_SOLS", "3")
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token-0123456789")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173"]')

    settings = Settings(_env_file=None)

    assert settings.sync_overlap_sols == 3
    assert settings.admin_enabled is True
    assert settings.cors_origins == ["http://localhost:5173"]


def test_empty_admin_token_keeps_admin_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "")

    assert Settings(_env_file=None).admin_enabled is False


@pytest.mark.parametrize("value", ["0", "-1"], ids=["zero", "negative"])
def test_backfill_lock_retry_seconds_not_positive_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A zero retry interval would spin on a held lock without ever waiting."""
    monkeypatch.setenv("BACKFILL_LOCK_RETRY_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_backfill_lock_retry_seconds_small_positive_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKFILL_LOCK_RETRY_SECONDS", "0.05")

    assert Settings(_env_file=None).backfill_lock_retry_seconds == pytest.approx(0.05)


# --- MER_CACHE_DIR: relative paths resolve against the project root ------------------------


@pytest.mark.parametrize("raw", ["data/mer", "./data/mer"], ids=["plain", "dot-slash"])
def test_mer_cache_dir_relative_resolves_against_project_root_regardless_of_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MER_CACHE_DIR", raw)

    resolved = Path(Settings(_env_file=None).mer_cache_dir)

    assert resolved.is_absolute()
    assert resolved.resolve() == (PROJECT_ROOT / "data" / "mer").resolve()


def test_mer_cache_dir_default_resolves_against_project_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MER_CACHE_DIR", raising=False)

    resolved = Path(Settings(_env_file=None).mer_cache_dir)

    assert resolved.resolve() == (PROJECT_ROOT / "data" / "mer").resolve()


def test_mer_cache_dir_absolute_is_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "mer"
    monkeypatch.setenv("MER_CACHE_DIR", str(target))

    assert Path(Settings(_env_file=None).mer_cache_dir).resolve() == target.resolve()
