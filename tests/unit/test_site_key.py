"""`site_key` and `RATE_LIMIT_IPV6_SITE_FACTOR` (docs/API.md "Ограничение частоты", HIGH-2).

`site_key(scope)` is the key of the shared IPv6 budget: a whole /48 network is one key.
IPv4 (also IPv4-mapped IPv6) and a missing client stay exactly as `client_key` has them.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from app.api.apikeys import client_key, site_key
from app.core.config import Settings

pytestmark = pytest.mark.unit


def scope_of(ip: str | None) -> dict[str, Any]:
    return {"type": "http", "client": (ip, 40000) if ip is not None else None}


SAME_SITE = [
    pytest.param("2001:db8:a:1::1", "2001:db8:a:2::1", id="two-64-networks"),
    pytest.param("2001:db8:a:1::1", "2001:db8:a:ffff:ffff:ffff:ffff:ffff", id="64-at-the-edge"),
    pytest.param("2001:db8:a::1", "2001:db8:a:0:0:0:0:2", id="same-64-other-notation"),
    pytest.param("2001:db8:a:1::1", "2001:DB8:A:7::9", id="letter-case"),
]
OTHER_SITE = [
    pytest.param("2001:db8:a:1::1", "2001:db8:b:1::1", id="neighbour-48"),
    pytest.param("2001:db8:a:1::1", "2001:db8:a1:1::1", id="prefix-lookalike"),
    pytest.param("2001:db8:a:1::1", "2001:db9:a:1::1", id="other-32"),
]


@pytest.mark.parametrize(("first", "second"), SAME_SITE)
def test_site_key_addresses_of_one_slash_48_give_one_key(first: str, second: str) -> None:
    assert site_key(scope_of(first)) == site_key(scope_of(second))


@pytest.mark.parametrize(("first", "second"), OTHER_SITE)
def test_site_key_addresses_of_different_slash_48_give_different_keys(
    first: str, second: str
) -> None:
    assert site_key(scope_of(first)) != site_key(scope_of(second))


def test_site_key_of_ipv6_is_coarser_than_client_key() -> None:
    a, b = "2001:db8:a:1::1", "2001:db8:a:2::1"

    assert client_key(scope_of(a)) != client_key(scope_of(b))
    assert site_key(scope_of(a)) == site_key(scope_of(b))


@pytest.mark.parametrize("ip", ["203.0.113.7", "198.51.100.200", "10.0.0.1"])
def test_site_key_ipv4_equals_client_key(ip: str) -> None:
    assert site_key(scope_of(ip)) == client_key(scope_of(ip))


def test_site_key_two_ipv4_addresses_of_one_subnet_stay_separate() -> None:
    assert site_key(scope_of("203.0.113.1")) != site_key(scope_of("203.0.113.2"))


@pytest.mark.parametrize(
    "mapped", ["::ffff:203.0.113.5", "::FFFF:203.0.113.5", "::ffff:cb00:7105"], ids=str
)
def test_site_key_ipv4_mapped_ipv6_is_the_ipv4_client(mapped: str) -> None:
    assert site_key(scope_of(mapped)) == client_key(scope_of(mapped))
    assert site_key(scope_of(mapped)) == site_key(scope_of("203.0.113.5"))


def test_site_key_two_mapped_addresses_do_not_collapse_into_one_site() -> None:
    assert site_key(scope_of("::ffff:203.0.113.5")) != site_key(scope_of("::ffff:203.0.113.6"))


def test_site_key_without_a_client_equals_client_key() -> None:
    assert site_key(scope_of(None)) == client_key(scope_of(None))


UNPARSABLE = [
    pytest.param("garbage", id="garbage"),
    pytest.param("01.2.3.4", id="leading-zero-ipv4"),
    pytest.param("[2001:db8::1]", id="bracketed-ipv6"),
]


@pytest.mark.parametrize("bad", UNPARSABLE)
@pytest.mark.parametrize("func", [client_key, site_key], ids=["client_key", "site_key"])
def test_key_unparsable_address_equals_the_key_of_a_missing_client(bad: str, func: Any) -> None:
    assert func(scope_of(bad)) == func(scope_of(None))


def test_key_unparsable_addresses_share_one_key() -> None:
    keys = {f(scope_of(bad.values[0])) for f in (client_key, site_key) for bad in UNPARSABLE}
    keys.add(client_key(scope_of(None)))

    assert len(keys) == 1


@pytest.mark.parametrize("func", [client_key, site_key], ids=["client_key", "site_key"])
def test_key_address_with_a_zone_drops_the_zone(func: Any) -> None:
    zoned = func(scope_of("fe80::1%eth0"))

    assert zoned == func(scope_of("fe80::1"))
    assert "%" not in zoned
    assert "eth0" not in zoned


def test_site_key_ipv6_never_collides_with_an_ipv4_key() -> None:
    assert site_key(scope_of("2001:db8:a::1")) != site_key(scope_of("203.0.113.5"))


# --- the setting ---


def test_settings_ipv6_site_factor_defaults_to_8() -> None:
    assert Settings.model_fields["rate_limit_ipv6_site_factor"].default == 8


def test_settings_ipv6_site_factor_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_IPV6_SITE_FACTOR", "3")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.rate_limit_ipv6_site_factor == 3


@pytest.mark.parametrize("value", ["0", "-1"])
def test_settings_ipv6_site_factor_below_one_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RATE_LIMIT_IPV6_SITE_FACTOR", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_ipv6_site_factor_one_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_IPV6_SITE_FACTOR", "1")

    assert Settings(_env_file=None).rate_limit_ipv6_site_factor == 1  # type: ignore[call-arg]
