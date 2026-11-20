"""One-off load of the helicopter feed (stage 12): `backfill --rover ingenuity` on the fixture.

The feed (category=ingenuity, same host and format as Mars 2020) is mocked with respx on
tests/fixtures/upstream/ingenuity/list_page0.json relabelled to other sols. Covered: requests,
stored rows (site/drive NULL, cameras, credit), sol summaries and counters, `sync_runs`,
idempotency, failed batch -> partial -> a repeated backfill completes it, and the rules that
keep Ingenuity out of regular synchronisation (scheduler, `/health/sync`, admin sync).
"""

import asyncio
import copy
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.core.config import Settings, get_settings
from tests.integration._fakes import fetch_all, photo_count, photo_sols, rover_row, summaries
from tests.integration._freshness import add_run
from tests.unit._upstream import M2020_URL, MSL_URL, load_fixture, query

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[None]:
    env = {
        "DATABASE_URL": settings.database_url,
        "SYNC_ENABLED": "false",
        "SYNC_CHUNK_SOLS": "10",
        "UPSTREAM_MAX_RPS": "0",
        "UPSTREAM_MAX_RETRIES": "0",
        "UPSTREAM_RETRY_BASE_SECONDS": "0",
        "M2020_FEED_URL": M2020_URL,
        "MSL_FEED_URL": MSL_URL,
        "LOG_LEVEL": "INFO",
        "BACKFILL_LOCK_RETRY_SECONDS": "0.05",
        "BACKFILL_LOCK_MAX_WAIT_SECONDS": "0",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("BACKFILL_BATCH_SOLS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def images_of_sol(sol: int, count: int) -> list[dict[str, Any]]:
    """`count` recorded helicopter images relabelled to `sol` (unique ids)."""
    base = load_fixture("ingenuity/list_page0.json")["images"]
    result = []
    for n in range(count):
        raw = copy.deepcopy(base[n % len(base)])
        raw["sol"] = sol
        raw["imageid"] = f"{raw['imageid']}_S{sol}_{n}"
        result.append(raw)
    return result


class HeliFeed:
    """respx side effect: images of DATA (sol -> count) inside the requested sol range."""

    def __init__(self, data: dict[int, int], fail: dict[int, int] | None = None) -> None:
        self.data = data
        self.fail = dict(fail or {})
        self.requests: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = query(request)
        self.requests.append(params)
        low = int(params["condition_2"].split(":")[0])
        high = int(params["condition_3"].split(":")[0])
        for sol, remaining in list(self.fail.items()):
            if low <= sol <= high and remaining:
                if remaining > 0:
                    self.fail[sol] = remaining - 1
                return httpx.Response(500, json={"error": "synthetic failure"})
        images = [
            raw for sol in sorted(self.data, reverse=True) if low <= sol <= high
            for raw in images_of_sol(sol, self.data[sol])
        ]  # fmt: skip
        page = {
            **load_fixture("ingenuity/list_page0.json"),
            "images": images,
            "total_results": len(images),
        }
        return httpx.Response(200, json=page)


async def run_cli(argv: list[str]) -> int:
    try:
        return await asyncio.to_thread(cli.main, argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def mock_feed(router: respx.MockRouter, data: dict[int, int], **kwargs: Any) -> HeliFeed:
    feed = HeliFeed(data, **kwargs)
    router.get(M2020_URL).mock(side_effect=feed)
    return feed


# --- a backfill of the helicopter feed ---------------------------------------------------------


async def test_backfill_ingenuity_requests_only_the_helicopter_category(
    upstream_router: respx.MockRouter, session_factory: SessionFactory
) -> None:
    feed = mock_feed(upstream_router, {1069: 4})

    code = await run_cli(
        ["backfill", "--rover", "ingenuity", "--from-sol", "1069", "--to-sol", "1069"]
    )

    assert code == 0
    assert feed.requests
    assert {r["category"] for r in feed.requests} == {"ingenuity"}
    assert {r["feed"] for r in feed.requests} == {"raw_images"}
    assert (feed.requests[0]["condition_2"], feed.requests[0]["condition_3"]) == (
        "1069:sol:gte",
        "1069:sol:lte",
    )


async def test_backfill_ingenuity_stores_photos_without_stops_and_with_helicopter_cameras(
    upstream_router: respx.MockRouter, session_factory: SessionFactory
) -> None:
    mock_feed(upstream_router, {1069: 4})

    code = await run_cli(
        ["backfill", "--rover", "ingenuity", "--from-sol", "1069", "--to-sol", "1069"]
    )

    assert code == 0
    rows = await fetch_all(
        session_factory,
        "SELECT p.source_id, p.sol, p.site, p.drive, p.sample_type, p.credit, c.code "
        "FROM photos p JOIN cameras c ON c.id = p.camera_id "
        "WHERE p.rover_id = 'ingenuity' ORDER BY p.source_id",
    )
    assert len(rows) == 4
    assert {r["sol"] for r in rows} == {1069}
    assert {(r["site"], r["drive"]) for r in rows} == {(None, None)}
    assert {r["sample_type"] for r in rows} == {"full"}
    assert {r["credit"] for r in rows} == {"NASA/JPL-Caltech"}
    assert sorted(r["code"] for r in rows) == ["HELI_NAV", "HELI_NAV", "HELI_RTE", "HELI_RTE"]
    assert await photo_count(session_factory, "perseverance") == 0
    cameras = await fetch_all(
        session_factory, "SELECT count(*) AS n FROM cameras WHERE rover_id = 'ingenuity'"
    )
    assert cameras == [{"n": 2}]  # no new camera codes were invented


async def test_backfill_ingenuity_fills_sol_summaries_counters_and_sync_run(
    upstream_router: respx.MockRouter, session_factory: SessionFactory
) -> None:
    mock_feed(upstream_router, {58: 3, 61: 1, 64: 2})

    code = await run_cli(
        [
            "backfill",
            "--rover",
            "ingenuity",
            "--from-sol",
            "55",
            "--to-sol",
            "64",
            "--batch-sols",
            "10",
        ]
    )

    assert code == 0
    assert await photo_sols(session_factory, "ingenuity") == {58: 3, 61: 1, 64: 2}
    sums = await summaries(session_factory, "ingenuity")
    assert {sol: row["total_photos"] for sol, row in sums.items()} == {58: 3, 61: 1, 64: 2}
    assert sorted(sums[58]["cameras"]) == ["HELI_NAV", "HELI_RTE"]
    rover = await rover_row(session_factory, "ingenuity")
    assert (rover["max_sol"], rover["total_photos"], rover["full_total_photos"]) == (64, 6, 6)
    assert rover["last_synced_at"] is not None
    other = await rover_row(session_factory, "perseverance")
    assert (other["max_sol"], other["total_photos"], other["last_synced_at"]) == (None, 0, None)
    runs = await fetch_all(
        session_factory,
        "SELECT rover_id, mode, from_sol, to_sol, status, photos_upserted, failed_sols "
        "FROM sync_runs ORDER BY id",
    )
    # The fixed batch grid walks top down: [60, 64] first, then [55, 59].
    assert runs == [
        {
            "rover_id": "ingenuity",
            "mode": "backfill",
            "from_sol": low,
            "to_sol": high,
            "status": "success",
            "photos_upserted": photos,
            "failed_sols": [],
        }
        for low, high, photos in [(60, 64, 3), (55, 59, 3)]
    ]
    counts = await fetch_all(
        session_factory,
        "SELECT sol, received, dropped FROM sol_source_counts "
        "WHERE rover_id = 'ingenuity' AND received > 0 ORDER BY sol",
    )
    assert counts == [
        {"sol": 58, "received": 3, "dropped": 0},
        {"sol": 61, "received": 1, "dropped": 0},
        {"sol": 64, "received": 2, "dropped": 0},
    ]


async def test_backfill_ingenuity_twice_creates_no_duplicates(
    upstream_router: respx.MockRouter, session_factory: SessionFactory
) -> None:
    mock_feed(upstream_router, {1069: 4})
    argv = [
        "backfill",
        "--rover",
        "ingenuity",
        "--from-sol",
        "1069",
        "--to-sol",
        "1069",
        "--no-resume",
    ]

    assert await run_cli(argv) == 0
    first = await fetch_all(session_factory, "SELECT source_id, id FROM photos ORDER BY source_id")
    assert await run_cli(argv) == 0
    second = await fetch_all(session_factory, "SELECT source_id, id FROM photos ORDER BY source_id")

    assert len(first) == 4
    assert second == first  # same rows, same ids
    rover = await rover_row(session_factory, "ingenuity")
    assert rover["total_photos"] == 4
    runs = await fetch_all(session_factory, "SELECT status FROM sync_runs ORDER BY id")
    assert [r["status"] for r in runs] == ["success", "success"]


async def test_backfill_ingenuity_failed_batch_is_partial_and_a_repeat_completes_it(
    upstream_router: respx.MockRouter, session_factory: SessionFactory
) -> None:
    feed = mock_feed(upstream_router, {1069: 4}, fail={1069: -1})
    argv = ["backfill", "--rover", "ingenuity", "--from-sol", "1069", "--to-sol", "1069"]

    code = await run_cli(argv)

    assert code == 1
    assert await photo_count(session_factory, "ingenuity") == 0
    runs = await fetch_all(session_factory, "SELECT status, failed_sols FROM sync_runs ORDER BY id")
    assert runs[-1]["status"] in ("partial", "failed")
    assert 1069 in runs[-1]["failed_sols"]

    feed.fail = {}  # the source recovers
    assert await run_cli(argv) == 0
    assert await photo_sols(session_factory, "ingenuity") == {1069: 4}


async def test_backfill_ingenuity_drops_thumbnails_of_the_feed(
    upstream_router: respx.MockRouter, session_factory: SessionFactory
) -> None:
    thumb = load_fixture("synthetic/ingenuity_quirks.json")["images"][0]
    assert thumb["sample_type"] == "Thumbnail"
    page = load_fixture("ingenuity/list_page0.json")
    page["images"] = page["images"][:2] + [thumb]
    page["total_results"] = 3
    upstream_router.get(M2020_URL).mock(return_value=httpx.Response(200, json=page))

    code = await run_cli(
        ["backfill", "--rover", "ingenuity", "--from-sol", "1069", "--to-sol", "1069"]
    )

    assert code == 0
    assert await photo_sols(session_factory, "ingenuity") == {1069: 2}


async def test_reconcile_ingenuity_offline_reports_clean_after_a_backfill(
    upstream_router: respx.MockRouter, session_factory: SessionFactory, tmp_path: Any
) -> None:
    mock_feed(upstream_router, {58: 3, 61: 1})
    assert (
        await run_cli(["backfill", "--rover", "ingenuity", "--from-sol", "0", "--to-sol", "64"])
        == 0
    )
    output = tmp_path / "report.json"

    code = await run_cli(
        ["reconcile", "--rover", "ingenuity", "--output", str(output), "--offline"]
    )

    assert code == 0
    assert "ingenuity" in output.read_text(encoding="utf-8")


# --- Ingenuity stays out of regular synchronisation -------------------------------------------


async def test_health_sync_does_not_list_ingenuity(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    now = datetime.now(UTC)
    for rover in ("curiosity", "perseverance", "ingenuity"):
        await add_run(session_factory, rover, finished_at=now, mode="backfill")

    response = await client.get("/health/sync")

    assert [item["rover"] for item in response.json()["rovers"]] == ["curiosity", "perseverance"]
    assert "ingenuity" not in response.text


async def test_backfill_ingenuity_feed_counters_never_reach_the_api_as_site_and_drive(
    upstream_router: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    stop = load_fixture("synthetic/ingenuity_quirks.json")["images"][1]
    assert (stop["site"], stop["drive"]) == (5, "123")  # fixture precondition
    page = load_fixture("ingenuity/list_page0.json")
    page["images"] = [{**stop, "sol": 1069}]
    page["total_results"] = 1
    upstream_router.get(M2020_URL).mock(return_value=httpx.Response(200, json=page))

    code = await run_cli(
        ["backfill", "--rover", "ingenuity", "--from-sol", "1069", "--to-sol", "1069"]
    )

    assert code == 0
    listed = (await client.get("/api/v2/rovers/ingenuity/photos", params={"sol": 1069})).json()
    assert listed["total"] == 1
    card = (await client.get(f"/api/v2/photos/{listed['items'][0]['id']}")).json()
    assert (card["site"], card["drive"], card["location"]) == (None, None, None)
    for site in (0, 5):
        found = (await client.get("/api/v2/rovers/ingenuity/photos", params={"site": site})).json()
        assert (found["items"], found["total"]) == ([], 0)
