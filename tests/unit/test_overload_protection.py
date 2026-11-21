"""Overload protection, unit part (docs/proposals/overload-protection.md).

AC-1: `classify_total` per row of the class table, no database.
AC-9: settings (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `HEAVY_QUERY_CONCURRENCY`), their
validation and which engine gets the pool arguments.
Also the gate itself (`HeavyQueryGate`): no waiting, a slot is released on every exit.
"""

import asyncio
from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import ServiceBusy
from app.core.overload import HeavyQueryGate
from app.db.session import create_engine
from app.main import create_app
from app.services.v2 import TotalSource, classify_total
from tests._overload import CASE_IDS, CASES, make_filter

pytestmark = pytest.mark.unit

ROWS = [pytest.param(kwargs, expected, id=case_id) for case_id, kwargs, expected in CASES]
assert [row.id for row in ROWS] == CASE_IDS


# --- AC-1 ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("kwargs", "expected"), ROWS)
def test_classify_total_filter_shape_gives_the_table_class(
    kwargs: dict[str, Any], expected: str | None
) -> None:
    """AC-1: the class of every filter shape from the section 6 table."""
    result = classify_total(make_filter(kwargs))

    assert isinstance(result, TotalSource)
    if expected is None:
        assert result in (TotalSource.SUMMARY, TotalSource.SCAN_LIGHT)
    else:
        assert result is TotalSource[expected]


def test_total_source_members_are_the_three_classes() -> None:
    """AC-1: the class names the spec fixes (section 7)."""
    assert {member.name for member in TotalSource} == {"SUMMARY", "SCAN_LIGHT", "SCAN_HEAVY"}


def test_classify_total_is_pure_and_repeatable() -> None:
    """AC-1: same filter, same class (no hidden state, no database access)."""
    flt = make_filter({"date_min": None, "min_width": 1})

    assert classify_total(flt) is classify_total(flt) is TotalSource.SCAN_HEAVY


# --- AC-9: settings ------------------------------------------------------------------------------


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("field", "default"),
    [("db_pool_size", 10), ("db_max_overflow", 5), ("heavy_query_concurrency", 2)],
)
def test_settings_overload_field_has_the_agreed_default(field: str, default: int) -> None:
    """AC-9."""
    assert getattr(_settings(), field) == default


@pytest.mark.parametrize(
    ("env", "field", "value"),
    [
        ("DB_POOL_SIZE", "db_pool_size", 8),
        ("DB_MAX_OVERFLOW", "db_max_overflow", 0),
        ("HEAVY_QUERY_CONCURRENCY", "heavy_query_concurrency", 3),
    ],
)
def test_settings_overload_field_reads_env(
    monkeypatch: pytest.MonkeyPatch, env: str, field: str, value: int
) -> None:
    """AC-9."""
    monkeypatch.setenv(env, str(value))

    assert getattr(_settings(), field) == value


@pytest.mark.parametrize(
    "env",
    [
        {"HEAVY_QUERY_CONCURRENCY": "0"},
        {"HEAVY_QUERY_CONCURRENCY": "-1"},
        {"DB_POOL_SIZE": "0"},
        {"DB_MAX_OVERFLOW": "-1"},
        {"DB_POOL_SIZE": "51"},
        {"DB_MAX_OVERFLOW": "51"},
        {"HEAVY_QUERY_CONCURRENCY": "15"},  # == 10 + 5 defaults
        {"HEAVY_QUERY_CONCURRENCY": "16"},
        {"DB_POOL_SIZE": "2", "DB_MAX_OVERFLOW": "0", "HEAVY_QUERY_CONCURRENCY": "2"},
        {"DB_POOL_SIZE": "4", "DB_MAX_OVERFLOW": "1", "HEAVY_QUERY_CONCURRENCY": "5"},
        {"DB_POOL_SIZE": "30", "DB_MAX_OVERFLOW": "21"},
        {"DB_POOL_SIZE": "50", "DB_MAX_OVERFLOW": "1"},
        {"DB_POOL_SIZE": "50", "DB_MAX_OVERFLOW": "50"},
    ],
    ids=[
        "heavy-zero",
        "heavy-negative",
        "pool-zero",
        "overflow-negative",
        "pool-above-50",
        "overflow-above-50",
        "heavy-equals-default-pool",
        "heavy-above-default-pool",
        "heavy-equals-small-pool",
        "heavy-equals-pool-plus-overflow",
        "combined-51",
        "pool-50-plus-1",
        "pool-50-plus-50",
    ],
)
def test_settings_invalid_overload_values_fail_on_startup(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    """AC-9: `heavy >= pool + overflow`, `heavy < 1`, `pool < 1`, `overflow < 0` are errors."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        _settings()


@pytest.mark.parametrize(
    ("pool", "overflow", "heavy"),
    [(10, 5, 14), (4, 0, 3), (2, 0, 1), (1, 1, 1)],
    ids=["default-pool-max-heavy", "small-pool", "minimal", "one-plus-one"],
)
def test_settings_heavy_below_pool_total_is_accepted(pool: int, overflow: int, heavy: int) -> None:
    """AC-9: the boundary just inside the rule."""
    settings = _settings(db_pool_size=pool, db_max_overflow=overflow, heavy_query_concurrency=heavy)

    assert settings.heavy_query_concurrency == heavy


@pytest.mark.parametrize(
    "values",
    [
        {"heavy_query_concurrency": 15},
        {"heavy_query_concurrency": 4, "db_pool_size": 3, "db_max_overflow": 1},
        {"db_pool_size": 0},
        {"db_max_overflow": -1},
        {"db_pool_size": 51},
        {"db_max_overflow": 51},
        {"db_pool_size": 30, "db_max_overflow": 21},
        {"db_pool_size": 50, "db_max_overflow": 1},
        {"db_pool_size": 50, "db_max_overflow": 50},
    ],
    ids=[
        "heavy-equals-pool-plus-overflow",
        "heavy-above-small-pool",
        "pool-zero",
        "overflow-negative",
        "pool-above-50",
        "overflow-above-50",
        "combined-51",
        "pool-50-plus-1",
        "pool-50-plus-50",
    ],
)
def test_create_app_with_copied_invalid_pool_settings_raises(values: dict[str, Any]) -> None:
    """AC-9: `model_copy(update=...)` skips the validators; `create_app` checks again."""
    with pytest.raises(ValueError):
        create_app(_settings().model_copy(update={"rate_limit_enabled": False, **values}))


@pytest.mark.parametrize(
    "values",
    [
        {"db_pool_size": 50, "db_max_overflow": 0, "heavy_query_concurrency": 49},
        {"db_pool_size": 25, "db_max_overflow": 25, "heavy_query_concurrency": 49},
        {"db_pool_size": 1, "db_max_overflow": 49},
        {"db_pool_size": 1},
    ],
    ids=["pool-50-no-overflow", "combined-exactly-50", "overflow-49", "pool-one"],
)
def test_settings_pool_bounds_just_inside_are_accepted(values: dict[str, Any]) -> None:
    """AC-9: 1 <= DB_POOL_SIZE, DB_MAX_OVERFLOW >= 0, DB_POOL_SIZE + DB_MAX_OVERFLOW <= 50."""
    settings = _settings(
        **{**values, "heavy_query_concurrency": values.get("heavy_query_concurrency", 1)}
    )

    assert settings.db_pool_size == values["db_pool_size"]
    assert settings.db_pool_size + settings.db_max_overflow <= 50


@pytest.mark.parametrize(
    "values",
    [
        {"db_pool_size": 50, "db_max_overflow": 0},
        {"db_pool_size": 25, "db_max_overflow": 25},
    ],
    ids=["pool-50", "combined-exactly-50"],
)
def test_create_app_with_copied_pool_at_the_combined_cap_is_accepted(
    values: dict[str, Any],
) -> None:
    """AC-9: the boundary of the combined cap passes the `create_app` re-check."""
    app = create_app(
        _settings(database_url=URL).model_copy(
            update={"rate_limit_enabled": False, "heavy_query_concurrency": 1, **values}
        )
    )

    assert app.state.engine.pool.size() == values["db_pool_size"]


# --- AC-9: engines -------------------------------------------------------------------------------

URL = "postgresql+asyncpg://user:pw@127.0.0.1:1/none"  # never connected to


def test_create_engine_takes_pool_size_and_overflow() -> None:
    """AC-9: `create_engine(pool_size=, max_overflow=)`."""
    engine = create_engine(URL, pool_size=3, max_overflow=1)

    assert engine.pool.size() == 3
    assert engine.pool._max_overflow == 1


def test_create_engine_without_pool_arguments_keeps_sqlalchemy_defaults() -> None:
    """AC-9: the sync engine style (no arguments) is unchanged: 5 + 10."""
    engine = create_engine(URL)

    assert engine.pool.size() == 5
    assert engine.pool._max_overflow == 10


def test_app_api_engine_uses_the_pool_settings_and_worker_engine_does_not() -> None:
    """AC-9: only the engine serving HTTP gets `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`."""
    app = create_app(
        _settings(
            database_url=URL,
            db_pool_size=4,
            db_max_overflow=0,
            heavy_query_concurrency=1,
        )
    )

    api, worker = app.state.engine, app.state.worker_engine
    assert (api.pool.size(), api.pool._max_overflow) == (4, 0)
    assert (worker.pool.size(), worker.pool._max_overflow) == (5, 10)


def test_app_creates_the_heavy_query_gate_with_the_configured_limit() -> None:
    """AC-9 / spec section 7: `app.state.heavy_query_gate` is a gate of the configured size."""
    app = create_app(
        _settings(database_url=URL, db_pool_size=6, db_max_overflow=0, heavy_query_concurrency=3)
    )

    gate = app.state.heavy_query_gate
    assert isinstance(gate, HeavyQueryGate)
    assert gate.in_use == 0


# --- the gate ------------------------------------------------------------------------------------


async def test_gate_slot_counts_in_use_and_frees_on_exit() -> None:
    gate = HeavyQueryGate(2)

    async with gate.slot():
        assert gate.in_use == 1
        async with gate.slot():
            assert gate.in_use == 2
        assert gate.in_use == 1

    assert gate.in_use == 0


async def test_gate_full_raises_service_busy_without_waiting() -> None:
    gate = HeavyQueryGate(1)

    async with gate.slot():
        with pytest.raises(ServiceBusy):
            async with asyncio.timeout(0.5):  # a waiting gate would hit the timeout instead
                async with gate.slot():
                    pytest.fail("the second slot must not be granted")
        assert gate.in_use == 1  # the refusal did not take or free anything


async def test_gate_slot_is_freed_when_the_body_raises() -> None:
    gate = HeavyQueryGate(1)

    with pytest.raises(RuntimeError):
        async with gate.slot():
            raise RuntimeError("boom")

    assert gate.in_use == 0
    async with gate.slot():
        assert gate.in_use == 1


async def test_gate_slot_is_freed_when_the_task_is_cancelled() -> None:
    gate = HeavyQueryGate(1)
    entered = asyncio.Event()

    async def hold() -> None:
        async with gate.slot():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.ensure_future(hold())
    await entered.wait()
    assert gate.in_use == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert gate.in_use == 0


async def test_gate_two_starting_together_get_exactly_one_slot() -> None:
    gate = HeavyQueryGate(1)
    release = asyncio.Event()

    async def take() -> str:
        try:
            async with gate.slot():
                await release.wait()
                return "ok"
        except ServiceBusy:
            return "busy"

    tasks = [asyncio.ensure_future(take()) for _ in range(2)]
    for _ in range(10):
        await asyncio.sleep(0)
    release.set()

    assert sorted(await asyncio.gather(*tasks)) == ["busy", "ok"]
