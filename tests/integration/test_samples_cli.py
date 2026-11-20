"""CLI ``python -m app.cli samples [--force]`` (stage 13).

Loads at once (no once-a-day wait); ``--force`` lets a list lose a stored number through.
Exit codes: 0 changed / unchanged, 1 rejected / failed, 2 usage error.
"""

import asyncio
from collections.abc import Iterator

import httpx
import pytest
import respx

from app import cli
from app.core.config import Settings, get_settings
from tests._samples import (
    COL_SOL,
    EXPECTED,
    NUMBERS,
    Factory,
    as_plain,
    count,
    dataset,
    mock_page,
    page_with_cell,
    page_without_number,
    sample_rows,
    set_page,
    trimmed_bytes,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[None]:
    for key, value in {
        "DATABASE_URL": settings.database_url,
        "SYNC_ENABLED": "false",
        "UPSTREAM_MAX_RPS": "0",
        "UPSTREAM_MAX_RETRIES": "0",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def page(upstream_router: respx.MockRouter) -> respx.Route:
    return mock_page(upstream_router)


async def run(*argv: str) -> int:
    try:
        return await asyncio.to_thread(cli.main, ["samples", *argv])
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


async def test_samples_loads_the_page_and_exits_0(page: respx.Route, clean_db: Factory) -> None:
    code = await run()

    assert code == 0
    assert page.call_count == 1
    assert [as_plain(r) for r in await sample_rows(clean_db)] == [EXPECTED[n] for n in NUMBERS]
    assert (await dataset(clean_db))["items"] == 7


async def test_samples_again_right_away_asks_again_and_exits_0(
    page: respx.Route, clean_db: Factory
) -> None:
    assert await run() == 0
    before = await dataset(clean_db)

    code = await run()

    assert code == 0  # no once-a-day wait: the command loads at once
    assert page.call_count == 2
    after = await dataset(clean_db)
    assert after["changed_at"] == before["changed_at"]  # unchanged
    assert await count(clean_db, "rover_samples") == 7


async def test_samples_changed_page_updates_the_rows_and_exits_0(
    page: respx.Route, clean_db: Factory
) -> None:
    assert await run() == 0
    set_page(page, page_with_cell(25, COL_SOL, "1216"))

    assert await run() == 0

    row = next(r for r in await sample_rows(clean_db) if r["number"] == 25)
    assert row["sol"] == 1216


async def test_samples_unreachable_source_exits_1_and_records_the_failure(
    page: respx.Route, clean_db: Factory
) -> None:
    page.mock(return_value=httpx.Response(500))

    code = await run()

    assert code == 1
    assert await count(clean_db, "rover_samples") == 0


async def test_samples_rejected_page_exits_1_and_keeps_old_data(
    page: respx.Route, clean_db: Factory
) -> None:
    assert await run() == 0
    set_page(page, page_with_cell(25, COL_SOL, "abc"))

    code = await run()

    assert code == 1
    assert [as_plain(r) for r in await sample_rows(clean_db)] == [EXPECTED[n] for n in NUMBERS]
    assert (await dataset(clean_db))["last_error"]


async def test_samples_disappeared_number_exits_1_without_force(
    page: respx.Route, clean_db: Factory
) -> None:
    assert await run() == 0
    set_page(page, page_without_number(8))

    assert await run() == 1
    assert await count(clean_db, "rover_samples") == 7


async def test_samples_force_accepts_a_disappeared_number_and_exits_0(
    page: respx.Route, clean_db: Factory
) -> None:
    assert await run() == 0
    set_page(page, page_without_number(8))

    code = await run("--force")

    assert code == 0
    assert [r["number"] for r in await sample_rows(clean_db)] == [1, 17, 25, 26, 29, 30]


async def test_samples_force_still_exits_1_for_a_broken_page(
    page: respx.Route, clean_db: Factory
) -> None:
    assert await run() == 0
    set_page(page, page_with_cell(25, COL_SOL, "abc"))

    assert await run("--force") == 1
    assert await count(clean_db, "rover_samples") == 7


async def test_samples_force_on_an_unchanged_page_exits_0(
    page: respx.Route, clean_db: Factory
) -> None:
    assert await run() == 0
    set_page(page, trimmed_bytes())

    assert await run("--force") == 0


async def test_samples_unknown_option_exits_2(page: respx.Route) -> None:
    assert await run("--bogus") == 2
    assert page.call_count == 0
