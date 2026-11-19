"""CLI ``python -m app.cli locations [--rover ROVER] [--force]`` (stage 11).

Loads at once (no once-a-day check); the conditional request stays, ``--force`` drops it and
the shrink guard. Exit codes: 0 every file changed/unchanged/skipped, 1 some file rejected or
failed, 2 unknown rover or a rover without a location source (Spirit, Opportunity).
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.core.config import Settings, get_settings
from tests._locations import (
    ROVER_KINDS,
    count,
    dataset,
    feature_collection,
    fixture_json,
    mock_files,
    ok_response,
    total_calls,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

Factory = async_sessionmaker[AsyncSession]
PERSEVERANCE = "perseverance"


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
def files(upstream_router: respx.MockRouter) -> dict[tuple[str, str], respx.Route]:
    return mock_files(upstream_router)


async def run(*argv: str) -> int:
    try:
        return await asyncio.to_thread(cli.main, ["locations", *argv])
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def shrunk_perseverance_waypoints() -> bytes:
    raw = fixture_json(PERSEVERANCE, "waypoints")
    base = {k: v for k, v in raw.items() if k != "features"}
    return feature_collection(raw["features"][-5:], base)


async def test_locations_one_rover_loads_its_two_files_and_exits_0(
    files: dict[tuple[str, str], respx.Route], clean_db: Factory
) -> None:
    code = await run("--rover", PERSEVERANCE)

    assert code == 0
    assert files[(PERSEVERANCE, "waypoints")].call_count == 1
    assert files[(PERSEVERANCE, "traverse")].call_count == 1
    assert total_calls(files) == 2
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 13
    assert await count(clean_db, "rover_traverse_segments", PERSEVERANCE) == 7
    assert await count(clean_db, "rover_waypoints", "curiosity") == 0


async def test_locations_without_rover_loads_every_file(
    files: dict[tuple[str, str], respx.Route], clean_db: Factory
) -> None:
    code = await run()

    assert code == 0
    assert [files[key].call_count for key in ROVER_KINDS] == [1, 1, 1, 1]
    assert await count(clean_db, "rover_waypoints") == 26
    assert await count(clean_db, "rover_traverse_segments") == 17
    assert await count(clean_db, "location_datasets") == 4


async def test_locations_again_right_away_asks_with_validators_and_exits_0(
    files: dict[tuple[str, str], respx.Route], clean_db: Factory
) -> None:
    assert await run("--rover", PERSEVERANCE) == 0
    state = await dataset(clean_db, PERSEVERANCE, "waypoints")
    for route in files.values():
        route.mock(return_value=httpx.Response(304))

    code = await run("--rover", PERSEVERANCE)

    assert code == 0  # no once-a-day wait: the command loads at once
    request = files[(PERSEVERANCE, "waypoints")].calls.last.request
    assert request.headers["if-none-match"] == state["etag"]
    assert files[(PERSEVERANCE, "waypoints")].call_count == 2
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 13


async def test_locations_unreachable_source_exits_1_and_records_the_failure(
    files: dict[tuple[str, str], respx.Route], clean_db: Factory
) -> None:
    files[(PERSEVERANCE, "traverse")].mock(return_value=httpx.Response(500))

    code = await run("--rover", PERSEVERANCE)

    assert code == 1
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 13  # the other file loaded
    assert await count(clean_db, "rover_traverse_segments", PERSEVERANCE) == 0
    assert (await dataset(clean_db, PERSEVERANCE, "traverse"))["last_error"]


async def test_locations_rejected_file_exits_1_and_keeps_old_data(
    files: dict[tuple[str, str], respx.Route], clean_db: Factory
) -> None:
    assert await run("--rover", PERSEVERANCE) == 0
    files[(PERSEVERANCE, "waypoints")].mock(
        return_value=ok_response(shrunk_perseverance_waypoints())
    )

    code = await run("--rover", PERSEVERANCE)

    assert code == 1
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 13


async def test_locations_force_accepts_a_shrunk_file_without_validators_and_exits_0(
    files: dict[tuple[str, str], respx.Route], clean_db: Factory
) -> None:
    assert await run("--rover", PERSEVERANCE) == 0
    files[(PERSEVERANCE, "waypoints")].mock(
        return_value=ok_response(shrunk_perseverance_waypoints())
    )

    code = await run("--rover", PERSEVERANCE, "--force")

    assert code == 0
    headers = files[(PERSEVERANCE, "waypoints")].calls.last.request.headers
    assert "if-none-match" not in headers and "if-modified-since" not in headers
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 5


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
async def test_locations_rover_without_a_source_exits_2_with_a_message(
    files: dict[tuple[str, str], respx.Route],
    capsys: pytest.CaptureFixture[str],
    rover: str,
) -> None:
    code = await run("--rover", rover)

    assert code == 2
    assert "no location source" in capsys.readouterr().err.lower()
    assert total_calls(files) == 0


@pytest.mark.parametrize("rover", ["mars", "Perseverance ", "../x", ""])
async def test_locations_unknown_rover_exits_2_without_requests(
    files: dict[tuple[str, str], respx.Route], rover: str
) -> None:
    assert await run("--rover", rover) == 2
    assert total_calls(files) == 0


async def test_locations_unknown_option_exits_2(files: dict[tuple[str, str], Any]) -> None:
    assert await run("--bogus") == 2
    assert total_calls(files) == 0
