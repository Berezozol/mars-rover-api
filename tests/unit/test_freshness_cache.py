"""/health/sync response cache (HEALTH_SYNC_CACHE_SECONDS; stage 9).

Agreed interface: ``FreshnessCache(ttl, clock)`` in app/services/health.py with public
``ttl``, ``clock``, ``async get(load)`` and ``clear()``. Within ``ttl`` seconds the loaded
value is reused; ``ttl=0`` disables caching; a failing ``load`` (database error) is not
cached. The clock is fake; nothing sleeps.
"""

import pytest

from app.services.health import FreshnessCache

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class Loader:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def __call__(self) -> object:
        self.calls += 1
        if self.fail:
            raise ConnectionError("database down")
        return {"value": self.calls}


async def test_cache_reuses_value_within_ttl() -> None:
    clock, load = Clock(), Loader()
    cache = FreshnessCache(30, clock)

    first = await cache.get(load)
    clock.now += 29.9
    second = await cache.get(load)

    assert load.calls == 1
    assert second == first


async def test_cache_reloads_after_ttl() -> None:
    clock, load = Clock(), Loader()
    cache = FreshnessCache(30, clock)
    await cache.get(load)

    clock.now += 30.01
    value = await cache.get(load)

    assert load.calls == 2
    assert value == {"value": 2}


async def test_cache_ttl_zero_always_loads() -> None:
    clock, load = Clock(), Loader()
    cache = FreshnessCache(0, clock)

    for _ in range(3):
        await cache.get(load)

    assert load.calls == 3


async def test_cache_clear_forces_reload() -> None:
    clock, load = Clock(), Loader()
    cache = FreshnessCache(30, clock)
    await cache.get(load)

    cache.clear()
    await cache.get(load)

    assert load.calls == 2


async def test_cache_does_not_store_errors() -> None:
    clock, load = Clock(), Loader()
    cache = FreshnessCache(30, clock)
    load.fail = True

    with pytest.raises(ConnectionError):
        await cache.get(load)
    load.fail = False
    value = await cache.get(load)

    assert value == {"value": 2}
    assert load.calls == 2


async def test_cache_error_after_success_keeps_nothing_stale_forever() -> None:
    clock, load = Clock(), Loader()
    cache = FreshnessCache(30, clock)
    await cache.get(load)
    clock.now += 31
    load.fail = True

    with pytest.raises(ConnectionError):
        await cache.get(load)


def test_cache_exposes_ttl_and_replaceable_clock() -> None:
    cache = FreshnessCache(30)
    clock = Clock()
    cache.clock = clock

    assert cache.ttl == 30
    assert cache.clock is clock
