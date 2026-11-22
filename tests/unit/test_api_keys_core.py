"""Key format, hashing and settings of stage 20A (spec sections 3 and 7; AC-2, AC-19).

Shared names (spec section 7): `app.core.apikeys` -> `generate_api_key`, `hash_api_key`,
`API_KEY_RE`, `is_our_key_format`, `key_prefix`.
"""

import hashlib

import pytest
from pydantic import ValidationError

from app.core.apikeys import (
    API_KEY_RE,
    generate_api_key,
    hash_api_key,
    is_our_key_format,
    key_prefix,
)
from app.core.config import Settings

pytestmark = pytest.mark.unit

GOOD = "si_" + "AbCdEfGh" * 4


def test_ac2_generated_key_is_si_plus_32_base62_characters() -> None:
    key = generate_api_key()

    assert len(key) == 35
    assert key.startswith("si_")
    assert key[3:].isalnum()
    assert key.isascii()
    assert API_KEY_RE.fullmatch(key)


def test_ac2_generated_keys_are_unique_and_use_the_whole_alphabet() -> None:
    keys = [generate_api_key() for _ in range(300)]

    assert len(set(keys)) == 300
    body = "".join(key[3:] for key in keys)
    assert any(c.isdigit() for c in body)
    assert any(c.islower() for c in body)
    assert any(c.isupper() for c in body)


def test_ac2_hash_is_sha256_of_the_key_as_32_bytes() -> None:
    digest = hash_api_key(GOOD)

    assert isinstance(digest, bytes)
    assert len(digest) == 32
    assert digest == hashlib.sha256(GOOD.encode()).digest()
    assert hash_api_key(GOOD + "x") != digest


def test_ac2_prefix_is_the_first_eleven_characters() -> None:
    assert key_prefix(GOOD) == GOOD[:11]
    assert key_prefix(GOOD) == "si_AbCdEfGh"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(GOOD, id="valid"),
        pytest.param("si_" + "0" * 32, id="digits-only"),
        pytest.param("si_" + "Z" * 32, id="upper-only"),
    ],
)
def test_is_our_key_format_accepts_si_plus_32_alphanumerics(value: str) -> None:
    assert is_our_key_format(value) is True


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("DEMO_KEY", id="demo-key"),
        pytest.param("a" * 40, id="nasa-40-chars"),
        pytest.param("SI_" + "a" * 32, id="upper-case-prefix"),
        pytest.param("si_" + "a" * 31, id="31-chars"),
        pytest.param("si_" + "a" * 33, id="33-chars"),
        pytest.param("si_" + "a" * 31 + "-", id="dash"),
        pytest.param("si_" + "a" * 31 + "_", id="underscore"),
        pytest.param(" si_" + "a" * 32, id="leading-space"),
        pytest.param("si_" + "a" * 32 + "\n", id="trailing-newline"),
        pytest.param("si_" + "я" * 32, id="non-ascii"),
    ],
)
def test_is_our_key_format_rejects_everything_else(value: str) -> None:
    assert is_our_key_format(value) is False


# --- settings (spec sections 3, 7 and the owner's decisions) ---

DEFAULTS = {
    "api_keys_enabled": False,  # decision 14: off until the release
    "rate_limit_per_minute": 60,
    "rate_limit_per_hour": 2000,  # owner, 2026-11-22: stays 2000
    "rate_limit_concurrent": 4,
    "rate_limit_key_per_minute": 120,
    "rate_limit_key_per_hour": 10_000,
    "rate_limit_key_concurrent": 8,
    "api_key_max_per_account": 2,  # owner, 2026-11-22
    "api_key_cache_seconds": 60,
    "api_key_auth_max_failures": 20,
    "api_key_auth_window_minutes": 15,
    "api_key_usage_flush_seconds": 60,
    "api_key_usage_retention_days": 90,
}


@pytest.mark.parametrize(("name", "expected"), DEFAULTS.items(), ids=DEFAULTS)
def test_settings_defaults_of_stage_20(name: str, expected: object) -> None:
    assert getattr(Settings(_env_file=None), name) == expected


def test_key_tier_limits_below_the_anonymous_ones_are_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            api_keys_enabled=True,
            rate_limit_key_per_minute=10,
            rate_limit_per_minute=60,
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            api_keys_enabled=True,
            rate_limit_key_per_hour=100,
            rate_limit_per_hour=2000,
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            api_keys_enabled=True,
            rate_limit_key_concurrent=2,
            rate_limit_concurrent=4,
        )


def test_key_tier_equal_to_the_anonymous_one_is_allowed() -> None:
    settings = Settings(_env_file=None, rate_limit_key_per_minute=60, rate_limit_key_concurrent=4)

    assert settings.rate_limit_key_per_minute == 60


def test_ac19_anonymous_limit_above_the_default_key_tier_is_fine_while_keys_are_off() -> None:
    """Built through the constructor on purpose: the validators must not trip."""
    settings = Settings(_env_file=None, rate_limit_per_minute=200, api_keys_enabled=False)

    assert settings.rate_limit_per_minute == 200
    assert settings.api_keys_enabled is False


def test_ac19_the_same_limits_are_refused_when_keys_are_on() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rate_limit_per_minute=200, api_keys_enabled=True)
